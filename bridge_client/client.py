"""A bpy-free persistent client for the Blender Bridge TCP protocol."""

import socket
import time
import uuid
from typing import Any, Mapping, Optional

from .codec import FrameWriteError, encode_message, recv_message, send_frame


DEFAULT_HOST = "localhost"
DEFAULT_PORT = 9876
PROTOCOL_VERSION = 1
DEFAULT_TIMEOUT = 30.0
DEFAULT_CONNECT_TIMEOUT = 10.0

# Long-running commands need more time than normal scene queries.
SLOW_COMMANDS = {
    "render_image": 120.0,
    "export_scene": 120.0,
    "polyhaven_download": 300.0,
    "execute_code": 120.0,
}


class BridgeTransportError(ConnectionError):
    """A transport failure with enough state to decide whether to retry.

    When ``bytes_written`` is non-zero, the request may have reached Blender
    and callers must not blindly retry it: a mutating command could run twice.
    """

    def __init__(
        self,
        message: str,
        *,
        request_id: Optional[str],
        phase: str,
        bytes_written: int = 0,
        cause: Optional[BaseException] = None,
    ):
        self.request_id = request_id
        self.phase = phase
        self.bytes_written = bytes_written
        self.cause = cause
        self.request_may_have_reached_server = bytes_written > 0
        super().__init__(message)


class BridgeClient:
    """Persistent TCP client with safe reconnect behavior.

    A request is retried only if its socket write failed before any bytes were
    written. Failures after any write, including response timeouts, close the
    socket and raise :class:`BridgeTransportError` instead of resending.
    """

    def __init__(
        self,
        host: str = DEFAULT_HOST,
        port: int = DEFAULT_PORT,
        *,
        timeout: float = DEFAULT_TIMEOUT,
        connect_timeout: float = DEFAULT_CONNECT_TIMEOUT,
        command_timeouts: Optional[Mapping[str, float]] = None,
        max_connect_retries: int = 3,
        retry_backoff: float = 0.5,
        max_safe_send_retries: int = 1,
    ):
        if max_connect_retries < 1:
            raise ValueError("max_connect_retries must be at least 1")
        if max_safe_send_retries < 0:
            raise ValueError("max_safe_send_retries cannot be negative")
        self.host = host
        self.port = int(port)
        self.timeout = timeout
        self.connect_timeout = connect_timeout
        self.command_timeouts = dict(SLOW_COMMANDS)
        if command_timeouts:
            self.command_timeouts.update(command_timeouts)
        self.max_connect_retries = max_connect_retries
        self.retry_backoff = retry_backoff
        self.max_safe_send_retries = max_safe_send_retries
        self._sock = None

    def __enter__(self):
        self.connect()
        return self

    def __exit__(self, exc_type, exc, traceback):
        self.close()
        return False

    def connect(self) -> None:
        """Establish the persistent connection, retrying connection attempts."""
        self._ensure_connected()

    def close(self) -> None:
        """Close the persistent socket, if one is open."""
        if self._sock is not None:
            try:
                self._sock.close()
            except OSError:
                pass
            self._sock = None

    def _socket_is_alive(self) -> bool:
        if self._sock is None:
            return False
        previous_timeout = self._sock.gettimeout()
        try:
            self._sock.setblocking(False)
            try:
                return bool(self._sock.recv(1, socket.MSG_PEEK))
            except BlockingIOError:
                return True
        except OSError:
            return False
        finally:
            if self._sock is not None:
                try:
                    self._sock.settimeout(previous_timeout)
                except OSError:
                    pass

    def _ensure_connected(self, request_id: Optional[str] = None) -> None:
        if self._sock is not None and not self._socket_is_alive():
            self.close()
        if self._sock is not None:
            return

        last_error = None
        for attempt in range(self.max_connect_retries):
            try:
                self._sock = socket.create_connection(
                    (self.host, self.port), timeout=self.connect_timeout
                )
                return
            except (OSError, socket.timeout) as exc:
                last_error = exc
                self.close()
                if attempt < self.max_connect_retries - 1:
                    time.sleep(self.retry_backoff * (2 ** attempt))

        raise BridgeTransportError(
            f"Could not connect to Blender at {self.host}:{self.port} after "
            f"{self.max_connect_retries} attempt(s): {last_error}",
            request_id=request_id,
            phase="connect",
            cause=last_error,
        ) from last_error

    @staticmethod
    def _build_request(
        command: str,
        params: Optional[Mapping[str, Any]],
        options: Optional[Mapping[str, Any]],
        request_fields: Optional[Mapping[str, Any]],
    ) -> dict:
        if not command:
            raise ValueError("command must be a non-empty string")
        request = {
            "v": PROTOCOL_VERSION,
            "id": str(uuid.uuid4()),
            "type": command,
            "params": dict(params or {}),
        }
        if options:
            request["options"] = dict(options)
        if request_fields:
            protected = {"v", "id", "type", "params", "options"}
            overlap = protected.intersection(request_fields)
            if overlap:
                raise ValueError(
                    "request_fields cannot override protocol fields: "
                    + ", ".join(sorted(overlap))
                )
            request.update(request_fields)
        return request

    def send(
        self,
        command: str,
        params: Optional[Mapping[str, Any]] = None,
        options: Optional[Mapping[str, Any]] = None,
        *,
        request_fields: Optional[Mapping[str, Any]] = None,
    ):
        """Send a command and return its parsed JSON response.

        ``request_fields`` supports protocol extensions such as the bridge's
        top-level ``commands`` field for a ``batch`` request.
        """
        request = self._build_request(command, params, options, request_fields)
        request_id = request["id"]
        frame = encode_message(request)
        timeout = self.command_timeouts.get(command, self.timeout)
        safe_send_retries = 0

        while True:
            self._ensure_connected(request_id)
            try:
                self._sock.settimeout(timeout)
                send_frame(self._sock, frame)
            except (FrameWriteError, OSError) as exc:
                bytes_written = exc.bytes_written if isinstance(exc, FrameWriteError) else 0
                cause = exc.cause if isinstance(exc, FrameWriteError) else exc
                self.close()
                if bytes_written == 0 and safe_send_retries < self.max_safe_send_retries:
                    safe_send_retries += 1
                    continue
                may_have_reached = bytes_written > 0
                retry_note = (
                    " The request may have reached Blender; it was not retried."
                    if may_have_reached
                    else " The request was not written to Blender."
                )
                raise BridgeTransportError(
                    f"Bridge send failed for request {request_id}.{retry_note}",
                    request_id=request_id,
                    phase="write",
                    bytes_written=bytes_written,
                    cause=cause,
                ) from exc

            try:
                return recv_message(self._sock)
            except (ConnectionError, OSError, socket.timeout, ValueError) as exc:
                self.close()
                raise BridgeTransportError(
                    f"Bridge response failed for request {request_id}; the request "
                    "may have reached Blender and was not retried.",
                    request_id=request_id,
                    phase="read",
                    bytes_written=len(frame),
                    cause=exc,
                ) from exc

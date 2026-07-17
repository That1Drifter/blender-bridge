"""Length-prefixed JSON framing for the Blender Bridge protocol.

This module intentionally has no Blender dependencies and can be used from a
regular system Python installation.
"""

import json
import struct


HEADER_SIZE = 4
MAX_MESSAGE_SIZE = 50 * 1024 * 1024


class FrameTooLargeError(ValueError):
    """Raised when a frame exceeds the protocol's 50 MB payload limit."""


class FrameWriteError(ConnectionError):
    """A socket write failure that reports how much of the frame was sent."""

    def __init__(self, cause: BaseException, bytes_written: int):
        self.cause = cause
        self.bytes_written = bytes_written
        super().__init__(f"Socket write failed after {bytes_written} byte(s): {cause}")


def encode_message(message: object) -> bytes:
    """Encode *message* as a four-byte big-endian length-prefixed JSON frame."""
    payload = json.dumps(message, separators=(",", ":")).encode("utf-8")
    if len(payload) > MAX_MESSAGE_SIZE:
        raise FrameTooLargeError(
            f"Message too large: {len(payload)} bytes (max {MAX_MESSAGE_SIZE})"
        )
    return struct.pack(">I", len(payload)) + payload


def send_frame(sock, frame: bytes) -> None:
    """Write a complete frame while preserving the number of bytes sent on error."""
    bytes_written = 0
    try:
        while bytes_written < len(frame):
            sent = sock.send(frame[bytes_written:])
            if sent == 0:
                raise ConnectionError("Socket connection closed while sending")
            bytes_written += sent
    except (ConnectionError, OSError) as exc:
        raise FrameWriteError(exc, bytes_written) from exc


def _recv_exact(sock, length: int) -> bytes:
    chunks = bytearray()
    while len(chunks) < length:
        chunk = sock.recv(length - len(chunks))
        if not chunk:
            raise ConnectionError("Connection closed while receiving")
        chunks.extend(chunk)
    return bytes(chunks)


def recv_message(sock):
    """Read and decode one framed JSON message from *sock*."""
    header = _recv_exact(sock, HEADER_SIZE)
    length = struct.unpack(">I", header)[0]
    if length > MAX_MESSAGE_SIZE:
        raise FrameTooLargeError(
            f"Message too large: {length} bytes (max {MAX_MESSAGE_SIZE})"
        )
    return json.loads(_recv_exact(sock, length).decode("utf-8"))

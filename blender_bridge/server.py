# Blender Bridge — TCP Socket Server

import socket
import threading
import traceback
import json
import queue
import bpy

from .constants import DEFAULT_HOST, DEFAULT_PORT, SOCKET_TIMEOUT, RECV_BUFFER_SIZE
from .protocol import encode_message, read_message, validate_request, make_error_response
from .constants import ERR_PROTOCOL_MISMATCH, ERR_INTERNAL_ERROR


class BlenderBridgeServer:
    def __init__(self, host=DEFAULT_HOST, port=DEFAULT_PORT):
        self.host = host
        self.port = port
        self.running = False
        self.socket = None
        self.server_thread = None
        self.dispatcher = None  # set after construction
        self._request_queue = queue.Queue()
        self._timer_lock = threading.Lock()
        self._timer_registered = False

    def set_dispatcher(self, dispatcher):
        self.dispatcher = dispatcher

    def start(self):
        if self.running:
            print("[MCP] Server already running")
            return

        self.running = True
        try:
            self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self.socket.bind((self.host, self.port))
            self.socket.listen(1)
            self.socket.settimeout(SOCKET_TIMEOUT)

            self.server_thread = threading.Thread(target=self._server_loop, daemon=True)
            self.server_thread.start()
            print(f"[MCP] Server started on {self.host}:{self.port}")
        except Exception as e:
            print(f"[MCP] Failed to start: {e}")
            self.stop()

    def stop(self):
        self.running = False
        if self.socket:
            try:
                self.socket.close()
            except Exception:
                pass
            self.socket = None
        if self.server_thread and self.server_thread.is_alive():
            try:
                self.server_thread.join(timeout=2.0)
            except Exception:
                pass
            self.server_thread = None
        print("[MCP] Server stopped")

    def _server_loop(self):
        while self.running:
            try:
                client, address = self.socket.accept()
                print(f"[MCP] Client connected: {address}")
                t = threading.Thread(target=self._handle_client, args=(client,), daemon=True)
                t.start()
            except socket.timeout:
                continue
            except Exception as e:
                if self.running:
                    print(f"[MCP] Accept error: {e}")

    def _handle_client(self, client):
        client.settimeout(None)
        buffer = b""

        try:
            while self.running:
                data = client.recv(RECV_BUFFER_SIZE)
                if not data:
                    print("[MCP] Client disconnected")
                    break

                buffer += data

                # Parse all complete messages in buffer
                while True:
                    try:
                        msg, buffer = read_message(buffer)
                    except json.JSONDecodeError as e:
                        # The payload cannot be safely parsed; report the protocol error
                        # before closing because any following frame boundary is untrusted.
                        print(f"[MCP] Protocol error: invalid JSON: {e}")
                        self._send(client, make_error_response(
                            None, ERR_PROTOCOL_MISMATCH, f"Invalid JSON: {e}"
                        ))
                        return
                    except ValueError as e:
                        # Oversized message — send error and drop connection
                        print(f"[MCP] Protocol error: {e}")
                        self._send(client, make_error_response(None, ERR_PROTOCOL_MISMATCH, str(e)))
                        return

                    if msg is None:
                        break  # need more data

                    # Validate request structure
                    err = validate_request(msg)
                    if err:
                        self._send(client, make_error_response(msg.get("id"), ERR_PROTOCOL_MISMATCH, err))
                        continue

                    # The socket thread only validates and enqueues work. Blender
                    # API access always happens in process_pending_requests().
                    self._dispatch_on_main_thread(client, msg)

        except Exception as e:
            if self.running:
                print(f"[MCP] Client error: {e}")
                traceback.print_exc()
        finally:
            try:
                client.close()
            except Exception:
                pass

    def _dispatch_on_main_thread(self, client, request):
        """Queue a validated request for execution on Blender's main thread.

        Interactive Blender drains this queue via ``bpy.app.timers``. Background
        startup uses the same queue from its explicit main-thread pump because
        timers do not run while its keep-alive loop owns the main thread.
        """
        self._request_queue.put((client, request))
        if not bpy.app.background:
            self._schedule_timer_dispatch()

    def _schedule_timer_dispatch(self):
        """Schedule one interactive timer callback to drain the request queue."""
        with self._timer_lock:
            if self._timer_registered or not self.running:
                return
            self._timer_registered = True

        try:
            bpy.app.timers.register(self._timer_callback, first_interval=0.0)
        except Exception:
            with self._timer_lock:
                self._timer_registered = False
            raise

    def _timer_callback(self):
        """Interactive-mode main-thread dispatch, one request per timer tick."""
        self.process_pending_requests(max_requests=1)
        with self._timer_lock:
            if self.running and not self._request_queue.empty():
                return 0.0
            self._timer_registered = False
        return None

    def process_pending_requests(self, max_requests=None):
        """Run queued requests on Blender's main thread and return their count.

        ``start_bridge.py`` calls this from its background-mode main loop. It is
        intentionally public so headless launchers do not need to use timers.
        """
        processed = 0
        while max_requests is None or processed < max_requests:
            try:
                client, request = self._request_queue.get_nowait()
            except queue.Empty:
                break
            self._execute_request(client, request)
            processed += 1
        return processed

    def _execute_request(self, client, request):
        """Dispatch one queued request and return its response to the socket."""
        try:
            if self.dispatcher:
                response = self.dispatcher.dispatch(request)
            else:
                response = make_error_response(
                    request.get("id"), ERR_INTERNAL_ERROR, "No dispatcher configured"
                )

            # Capture handlers can return a standard error envelope without
            # changing dispatcher handler routing. Convert that envelope back
            # to the normal top-level protocol error response.
            result = response.get("result") if isinstance(response, dict) else None
            if isinstance(result, dict) and result.get("status") == "error":
                error = result.get("error", {})
                error_response = make_error_response(
                    request.get("id"),
                    error.get("code", ERR_INTERNAL_ERROR),
                    error.get("message", "Command failed"),
                    error.get("details"),
                )
                for field in ("timing_ms", "history_index"):
                    if field in response:
                        error_response[field] = response[field]
                response = error_response
            self._send(client, response)
        except Exception as e:
            print(f"[MCP] Dispatch error: {e}")
            traceback.print_exc()
            try:
                self._send(client, make_error_response(
                    request.get("id"), ERR_INTERNAL_ERROR, str(e)
                ))
            except Exception:
                pass

    def _send(self, client, response: dict):
        """Send a length-prefixed response to the client."""
        try:
            client.sendall(encode_message(response))
        except Exception:
            print("[MCP] Failed to send response — client disconnected")

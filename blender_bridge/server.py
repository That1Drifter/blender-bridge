# Blender Bridge — TCP Socket Server

import socket
import threading
import traceback
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

                    # Dispatch on Blender's main thread via timer
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
        """Schedule command execution on Blender's main thread."""
        def timer_callback():
            try:
                if self.dispatcher:
                    response = self.dispatcher.dispatch(request)
                else:
                    response = make_error_response(
                        request.get("id"), ERR_INTERNAL_ERROR, "No dispatcher configured"
                    )
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
            return None  # don't repeat timer

        bpy.app.timers.register(timer_callback, first_interval=0.0)

    def _send(self, client, response: dict):
        """Send a length-prefixed response to the client."""
        try:
            client.sendall(encode_message(response))
        except Exception:
            print("[MCP] Failed to send response — client disconnected")

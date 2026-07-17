"""Blender-free unit tests for :mod:`bridge_client.client`."""

import socketserver
import threading
import time
import unittest

from bridge_client.client import BridgeClient, BridgeTransportError
from bridge_client.codec import encode_message, recv_message, send_frame


class RecordingHandler(socketserver.BaseRequestHandler):
    def handle(self):
        while True:
            try:
                request = recv_message(self.request)
            except (ConnectionError, OSError):
                return
            with self.server.request_lock:
                self.server.requests.append(request)
            self.server.respond(self.request, request)


class BridgeClientUnitTests(unittest.TestCase):
    def setUp(self):
        self.server = socketserver.ThreadingTCPServer(("127.0.0.1", 0), RecordingHandler)
        self.server.daemon_threads = True
        self.server.allow_reuse_address = True
        self.server.requests = []
        self.server.request_lock = threading.Lock()
        self.server.respond = self._echo_response
        self.server_thread = threading.Thread(target=self.server.serve_forever)
        self.server_thread.start()
        self.addCleanup(self._stop_server)

    def _stop_server(self):
        self.server.shutdown()
        self.server.server_close()
        self.server_thread.join(timeout=1)

    @staticmethod
    def _echo_response(sock, request):
        send_frame(sock, encode_message({"id": request["id"], "ok": True}))

    def _client(self, **kwargs):
        host, port = self.server.server_address
        return BridgeClient(host, port, retry_backoff=0, **kwargs)

    def test_each_request_has_a_unique_id(self):
        client = self._client()
        self.addCleanup(client.close)

        first = client.send("scene_info")
        second = client.send("scene_info")

        self.assertTrue(first["ok"])
        self.assertTrue(second["ok"])
        with self.server.request_lock:
            request_ids = [request["id"] for request in self.server.requests]
        self.assertEqual(len(request_ids), 2)
        self.assertEqual(len(set(request_ids)), 2)

    def test_response_timeout_raises_transport_error(self):
        received = threading.Event()

        def delay_response(_sock, _request):
            received.set()
            time.sleep(0.2)

        self.server.respond = delay_response
        client = self._client(timeout=0.05)
        self.addCleanup(client.close)

        with self.assertRaises(BridgeTransportError) as context:
            client.send("scene_info")

        self.assertTrue(received.wait(1))
        self.assertEqual(context.exception.phase, "read")
        self.assertTrue(context.exception.request_may_have_reached_server)

    def test_response_failure_is_not_replayed(self):
        received = threading.Event()

        def close_without_response(sock, _request):
            received.set()
            sock.close()

        self.server.respond = close_without_response
        client = self._client(max_safe_send_retries=3)
        self.addCleanup(client.close)

        with self.assertRaises(BridgeTransportError) as context:
            client.send("delete_object", {"name": "Cube"})

        self.assertTrue(received.wait(1))
        time.sleep(0.05)
        with self.server.request_lock:
            self.assertEqual(len(self.server.requests), 1)
        self.assertEqual(context.exception.phase, "read")
        self.assertTrue(context.exception.request_may_have_reached_server)


if __name__ == "__main__":
    unittest.main()

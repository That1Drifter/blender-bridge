"""Blender-free unit tests for the wire codec."""

import struct
import unittest

from bridge_client import codec


class ChunkedSocket:
    """In-memory socket that returns at most one predefined chunk per recv."""

    def __init__(self, payload: bytes, chunk_sizes: list[int]):
        self._payload = payload
        self._chunk_sizes = iter(chunk_sizes)

    def recv(self, _size: int) -> bytes:
        try:
            size = next(self._chunk_sizes)
        except StopIteration:
            size = len(self._payload)
        size = min(size, _size)
        chunk, self._payload = self._payload[:size], self._payload[size:]
        return chunk


class CodecTests(unittest.TestCase):
    def test_encode_and_decode_round_trip(self):
        message = {"type": "scene_info", "params": {"name": "Café"}}
        frame = codec.encode_message(message)

        self.assertEqual(codec.recv_message(ChunkedSocket(frame, [len(frame)])), message)

    def test_decode_handles_partial_socket_buffers(self):
        message = {"id": "partial", "values": [1, 2, 3]}
        frame = codec.encode_message(message)

        decoded = codec.recv_message(ChunkedSocket(frame, [1, 2, 1, 3, 2, 1]))

        self.assertEqual(decoded, message)

    def test_decode_multiple_frames_from_one_buffer(self):
        first = {"id": "one"}
        second = {"id": "two"}
        socket = ChunkedSocket(
            codec.encode_message(first) + codec.encode_message(second),
            [1024],
        )

        self.assertEqual(codec.recv_message(socket), first)
        self.assertEqual(codec.recv_message(socket), second)

    def test_rejects_oversize_encoded_and_received_frames(self):
        original_limit = codec.MAX_MESSAGE_SIZE
        codec.MAX_MESSAGE_SIZE = 10
        self.addCleanup(setattr, codec, "MAX_MESSAGE_SIZE", original_limit)

        with self.assertRaises(codec.FrameTooLargeError):
            codec.encode_message({"payload": "too large"})

        oversized_header = struct.pack(">I", codec.MAX_MESSAGE_SIZE + 1)
        with self.assertRaises(codec.FrameTooLargeError):
            codec.recv_message(ChunkedSocket(oversized_header, [4]))


if __name__ == "__main__":
    unittest.main()

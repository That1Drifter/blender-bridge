# Blender Bridge — Wire Protocol
#
# Length-prefixed framing: [4 bytes big-endian uint32 payload length][UTF-8 JSON]

import struct
import json
from .constants import HEADER_SIZE, MAX_MESSAGE_SIZE, PROTOCOL_VERSION


def encode_message(obj: dict) -> bytes:
    """Encode a dict as a length-prefixed JSON message."""
    payload = json.dumps(obj, separators=(",", ":")).encode("utf-8")
    return struct.pack(">I", len(payload)) + payload


def read_message(buffer: bytes) -> tuple:
    """Try to parse one message from the buffer.

    Returns (parsed_dict_or_None, remaining_buffer).
    Raises ValueError on oversized messages.
    """
    if len(buffer) < HEADER_SIZE:
        return None, buffer
    length = struct.unpack(">I", buffer[:HEADER_SIZE])[0]
    if length > MAX_MESSAGE_SIZE:
        raise ValueError(f"Message too large: {length} bytes (max {MAX_MESSAGE_SIZE})")
    total = HEADER_SIZE + length
    if len(buffer) < total:
        return None, buffer
    payload = buffer[HEADER_SIZE:total]
    return json.loads(payload.decode("utf-8")), buffer[total:]


def validate_request(msg: dict) -> str | None:
    """Validate an incoming request. Returns error string or None if valid."""
    if not isinstance(msg, dict):
        return "Request must be a JSON object"
    v = msg.get("v")
    if v is None:
        return "Missing protocol version field 'v'"
    if v != PROTOCOL_VERSION:
        return f"Protocol version mismatch: got {v}, expected {PROTOCOL_VERSION}"
    if "type" not in msg:
        return "Missing 'type' field"
    if "id" not in msg:
        return "Missing 'id' field"
    return None


def make_response(request_id, status, result=None, error=None,
                  diff=None, screenshot=None, timing_ms=0, history_index=None):
    """Build a standard response dict."""
    resp = {
        "v": PROTOCOL_VERSION,
        "id": request_id,
        "status": status,
    }
    if status == "success":
        resp["result"] = result
    else:
        resp["error"] = error if isinstance(error, dict) else {
            "code": error or ERR_INTERNAL_ERROR,
            "message": str(error),
        }
    if diff is not None:
        resp["diff"] = diff
    if screenshot is not None:
        resp["screenshot"] = screenshot
    if timing_ms:
        resp["timing_ms"] = timing_ms
    if history_index is not None:
        resp["history_index"] = history_index
    return resp


def make_error_response(request_id, code, message, details=None):
    """Build an error response dict."""
    return {
        "v": PROTOCOL_VERSION,
        "id": request_id,
        "status": "error",
        "error": {
            "code": code,
            "message": message,
            "details": details,
        },
    }

# Blender Bridge Protocol v1

Blender Bridge exposes a stable, local TCP/JSON API. Any automation client can
use it; the MCP server is only one client implementation.

## Endpoint and connection model

The addon listens on `localhost:9876` by default. It is a localhost-only,
unauthenticated TCP service, so do not expose the port to an untrusted network.

The server accepts multiple concurrent client connections. Requests from all
clients execute sequentially on Blender's main thread, and each response is
sent to the client that submitted its request. Per-connection ordering follows
submission order; cross-client interleaving is unspecified. Clients are
responsible for choosing unique IDs. A `BridgeClient` instance is not
thread-safe: use one per thread or guard it with a lock. If a connection drops
after a mutating request was sent, do not blindly resend it: its response may
have been lost after Blender already performed the operation.

## Framing

Each message is one frame:

```
+--------------------------+--------------------------------+
| 4-byte big-endian uint32 | UTF-8 encoded JSON payload     |
+--------------------------+--------------------------------+
```

The length is the payload length in bytes. The maximum payload is 50 MiB
(`50 * 1024 * 1024` bytes). A frame may arrive across multiple TCP reads, and
multiple complete frames may arrive in one read; clients must buffer and parse
accordingly.

## Request envelope

Every request is a JSON object with this envelope:

```json
{"v":1,"id":"request-123","type":"ping","params":{},"options":{}}
```

`v` must be `1`. `id` and `type` are required. `params` and `options` are
optional in the current dispatcher and default to `{}` when absent. `type` is
the command name and `params` supplies that command's keyword arguments.
`options` controls response feedback such as scene diffs and screenshots.

## Response envelopes

All responses contain `v`, `id`, and `status`. A normal successful response
from `make_response` has this shape (the last four fields are included only
when supplied):

```json
{
  "v": 1,
  "id": "request-123",
  "status": "success",
  "result": {},
  "diff": {},
  "screenshot": "base64-data",
  "timing_ms": 12,
  "history_index": 4
}
```

An error response from `make_error_response` always has this shape; `details`
is present and is `null` unless the caller provides it:

```json
{
  "v": 1,
  "id": "request-123",
  "status": "error",
  "error": {
    "code": "INVALID_PARAMS",
    "message": "Description of the failure",
    "details": null
  }
}
```

Command-dispatch errors also use `status: "error"` and an `error` object with
`code` and `message`; they can additionally include `timing_ms` and
`history_index`.

## Error codes

| Constant | Wire value | Meaning |
|---|---|---|
| `ERR_UNKNOWN_COMMAND` | `UNKNOWN_COMMAND` | The request `type` is not registered. |
| `ERR_INVALID_PARAMS` | `INVALID_PARAMS` | Command arguments are invalid. |
| `ERR_OBJECT_NOT_FOUND` | `OBJECT_NOT_FOUND` | A referenced Blender object was not found. |
| `ERR_EXECUTION_ERROR` | `EXECUTION_ERROR` | Command execution failed. |
| `ERR_CHECKPOINT_INVALID` | `CHECKPOINT_INVALID` | The requested checkpoint cannot be used. |
| `ERR_SANDBOX_VIOLATION` | `SANDBOX_VIOLATION` | Safe code execution violated its sandbox. |
| `ERR_PROTOCOL_MISMATCH` | `PROTOCOL_MISMATCH` | Frame or request-envelope protocol error. |
| `ERR_INTERNAL_ERROR` | `INTERNAL_ERROR` | Server configuration or unexpected internal failure. |

An oversized frame produces `PROTOCOL_MISMATCH` with `id: null` and closes the
connection. Malformed JSON likewise produces a structured
`PROTOCOL_MISMATCH` error with `id: null`, then the server closes the
connection because subsequent framing cannot be trusted.

## Feature detection

Send `get_capabilities` to discover the installed bridge's feature flags,
versions, command support, defaults, and `supported_protocol_versions`. Use
that response for feature detection instead of assuming every installation has
the same capabilities.

## Minimal raw-socket client

This standard-library-only example sends `ping` and `get_scene_info` and
prints the correlated responses.

```python
import json
import socket
import struct


def recv_exact(sock, count):
    chunks = []
    while count:
        chunk = sock.recv(count)
        if not chunk:
            raise ConnectionError("Blender Bridge closed the connection")
        chunks.append(chunk)
        count -= len(chunk)
    return b"".join(chunks)


def send_request(sock, request):
    payload = json.dumps(request, separators=(",", ":")).encode("utf-8")
    sock.sendall(struct.pack(">I", len(payload)) + payload)
    length = struct.unpack(">I", recv_exact(sock, 4))[0]
    return json.loads(recv_exact(sock, length).decode("utf-8"))


with socket.create_connection(("localhost", 9876)) as sock:
    print(send_request(sock, {
        "v": 1, "id": "ping-1", "type": "ping", "params": {}, "options": {}
    }))
    print(send_request(sock, {
        "v": 1, "id": "scene-1", "type": "get_scene_info", "params": {}, "options": {}
    }))
```

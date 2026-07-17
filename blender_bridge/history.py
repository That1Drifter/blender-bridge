# Blender Bridge — Command History Log

import hashlib
import json
import os
import time
from datetime import datetime, timezone

import bpy

from .constants import AUDIT_LOG_FULL_CODE


_AUDIT_LOG_FILENAME = "blender_bridge_audit.jsonl"


def _serialize_audit_payload(payload) -> str:
    """Return a stable text representation for hashing and optional logging."""
    if isinstance(payload, str):
        return payload
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)


def audit_execution(command: str, mode: str, payload, success: bool,
                    error: Exception | str | None = None) -> None:
    """Append one best-effort execution record to the persistent JSONL audit log."""
    try:
        payload_text = _serialize_audit_payload(payload)
        payload_bytes = payload_text.encode("utf-8")
        record = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "command": command,
            "mode": mode,
            "payload_sha256": hashlib.sha256(payload_bytes).hexdigest(),
            "payload_size": len(payload_bytes),
            "success": bool(success),
            "error": None if error is None else str(error),
        }
        if AUDIT_LOG_FULL_CODE:
            record["payload"] = payload_text

        config_dir = bpy.utils.user_resource("CONFIG")
        os.makedirs(config_dir, exist_ok=True)
        log_path = os.path.join(config_dir, _AUDIT_LOG_FILENAME)
        with open(log_path, "a", encoding="utf-8") as audit_file:
            audit_file.write(json.dumps(record, separators=(",", ":")) + "\n")
    except Exception:
        # Audit logging is deliberately best effort and must not affect execution.
        pass


class CommandHistory:
    def __init__(self, max_entries: int = 500):
        self._entries = []
        self._max = max_entries
        self._next_index = 1

    def log(self, cmd_type: str, params: dict, status: str, timing_ms: int) -> int:
        """Log a command execution. Returns the history index."""
        index = self._next_index
        self._next_index += 1

        # Summarize params (truncate code strings for readability)
        params_summary = {}
        for k, v in params.items():
            if isinstance(v, str) and len(v) > 100:
                params_summary[k] = v[:100] + "..."
            else:
                params_summary[k] = v

        entry = {
            "index": index,
            "timestamp": time.time(),
            "type": cmd_type,
            "params_summary": params_summary,
            "status": status,
            "timing_ms": timing_ms,
        }
        self._entries.append(entry)

        # Trim if over max
        if len(self._entries) > self._max:
            self._entries = self._entries[-self._max:]

        return index

    def get_history(self, limit: int = 20, offset: int = 0) -> dict:
        """Get command history entries."""
        total = len(self._entries)
        # Return most recent first
        entries = list(reversed(self._entries))
        page = entries[offset:offset + limit]
        return {
            "total": total,
            "offset": offset,
            "limit": limit,
            "entries": page,
        }

    def get_entry(self, index: int) -> dict | None:
        """Get a specific history entry by index."""
        for e in self._entries:
            if e["index"] == index:
                return e
        return None

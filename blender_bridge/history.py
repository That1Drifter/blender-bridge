# Blender Bridge — Command History Log

import time


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

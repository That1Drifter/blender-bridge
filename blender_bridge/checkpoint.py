# Blender Bridge — Undo Checkpoint Management

import bpy
import time


class CheckpointInvalidError(Exception):
    """Raised when a checkpoint cannot be restored safely."""


class CheckpointManager:
    def __init__(self):
        self.checkpoints = []  # list of checkpoint dicts
        self._next_id = 0
        self._steps_since_last = 0  # undo steps since most recent checkpoint
        self._last_invalidation_reason = None

    def create(self, label: str = "checkpoint") -> dict:
        """Create a named checkpoint by pushing a Blender undo step."""
        bpy.ops.ed.undo_push(message=f"MCP Checkpoint: {label}")

        cp = {
            "id": f"cp_{self._next_id}",
            "label": label,
            "timestamp": time.time(),
            "undo_steps_after": 0,
            "valid": True,
        }
        self._next_id += 1
        self._steps_since_last = 0
        self.checkpoints.append(cp)
        return {"checkpoint_id": cp["id"], "label": cp["label"]}

    def list_checkpoints(self) -> dict:
        """List all checkpoints."""
        return {
            "checkpoints": [
                {
                    "id": cp["id"],
                    "label": cp["label"],
                    "timestamp": cp["timestamp"],
                    "undo_steps_after": cp["undo_steps_after"],
                    "valid": cp["valid"],
                }
                for cp in self.checkpoints
            ]
        }

    def restore(self, checkpoint_id: str) -> dict:
        """Restore a valid checkpoint using the tracked bridge undo steps."""
        # Find the checkpoint
        idx = None
        for i, cp in enumerate(self.checkpoints):
            if cp["id"] == checkpoint_id:
                idx = i
                break
        if idx is None:
            self._raise_invalid(checkpoint_id, self._last_invalidation_reason or "checkpoint_not_found")

        checkpoint = self.checkpoints[idx]
        if not checkpoint["valid"]:
            self._raise_invalid(
                checkpoint_id,
                checkpoint.get("invalidation_reason", "checkpoint_invalid"),
            )

        # Calculate total undo steps needed
        # Sum undo_steps_after for the target checkpoint and all after it
        total_steps = sum(cp["undo_steps_after"] for cp in self.checkpoints[idx:])
        # Plus one undo per checkpoint after the target (each checkpoint pushed one step)
        total_steps += len(self.checkpoints) - idx - 1

        if total_steps == 0:
            return {"restored_to": checkpoint_id, "undone_steps": 0}

        # Perform undos
        for _ in range(total_steps):
            bpy.ops.ed.undo()

        # Trim checkpoint list — remove everything after the restored checkpoint
        self.checkpoints = self.checkpoints[:idx + 1]
        self.checkpoints[idx]["undo_steps_after"] = 0
        self._steps_since_last = 0

        return {"restored_to": checkpoint_id, "undone_steps": total_steps}

    def invalidate_all(self, reason: str):
        """Mark every existing checkpoint unsafe to restore after undo history changes."""
        self._last_invalidation_reason = reason
        self._steps_since_last = 0
        for checkpoint in self.checkpoints:
            checkpoint["valid"] = False
            checkpoint["invalidation_reason"] = reason

    def clear_all(self, reason: str):
        """Invalidate and discard checkpoints after Blender rebuilds its undo history."""
        self.invalidate_all(reason)
        self.checkpoints.clear()

    @staticmethod
    def _raise_invalid(checkpoint_id: str, reason: str):
        raise CheckpointInvalidError(
            f"Checkpoint '{checkpoint_id}' cannot be restored: invalidation reason: {reason}"
        )

    def increment_steps(self):
        """Called after each execute_code to track undo steps since last checkpoint."""
        self._steps_since_last += 1
        if self.checkpoints:
            self.checkpoints[-1]["undo_steps_after"] += 1

# Blender MCP Addon v2 — Undo Checkpoint Management

import bpy
import time


class CheckpointManager:
    def __init__(self):
        self.checkpoints = []  # list of checkpoint dicts
        self._next_id = 0
        self._steps_since_last = 0  # undo steps since most recent checkpoint

    def create(self, label: str = "checkpoint") -> dict:
        """Create a named checkpoint by pushing a Blender undo step."""
        bpy.ops.ed.undo_push(message=f"MCP Checkpoint: {label}")

        cp = {
            "id": f"cp_{self._next_id}",
            "label": label,
            "timestamp": time.time(),
            "undo_steps_after": 0,
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
                }
                for cp in self.checkpoints
            ]
        }

    def restore(self, checkpoint_id: str) -> dict:
        """Restore to a checkpoint by undoing the appropriate number of steps."""
        # Find the checkpoint
        idx = None
        for i, cp in enumerate(self.checkpoints):
            if cp["id"] == checkpoint_id:
                idx = i
                break
        if idx is None:
            raise KeyError(f"Checkpoint '{checkpoint_id}' not found")

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

    def increment_steps(self):
        """Called after each execute_code to track undo steps since last checkpoint."""
        self._steps_since_last += 1
        if self.checkpoints:
            self.checkpoints[-1]["undo_steps_after"] += 1

"""Thread-safe lifecycle management for Blender Bridge async jobs."""

import copy
import threading
import time
import uuid
from collections import deque

from .constants import JOB_TTL_SECONDS


TERMINAL_STATES = frozenset({"succeeded", "failed", "cancelled"})


class JobNotFoundError(LookupError):
    """Raised when a job id is absent or its retained record has expired."""


class JobManager:
    """Own the lock-guarded job registry and queued main-thread work.

    Consumer callbacks placed in the pending queue must be executed on Blender's
    main thread. In background mode such a callback may itself block the bridge
    request pump; cancellation of an operation already executing there is
    cooperative-only and may not interrupt it.
    """

    def __init__(self, ttl_seconds=JOB_TTL_SECONDS, clock=time.time):
        self._ttl_seconds = ttl_seconds
        self._clock = clock
        self._lock = threading.RLock()
        self._jobs = {}
        self._pending = deque()
        self._cancel_callbacks = {}

    def create(self, command, params):
        """Create and return a queued job record."""
        del params  # Reserved for consumers; job records intentionally omit inputs.
        now = self._clock()
        job_id = str(uuid.uuid4())
        job = {
            "id": job_id,
            "command": command,
            "state": "queued",
            "progress": None,
            "created_at": now,
            "started_at": None,
            "finished_at": None,
            "result": None,
            "error": None,
            "cancel_requested": False,
        }
        with self._lock:
            self._purge_expired_locked(now)
            self._jobs[job_id] = job
            return self._copy(job)

    def get(self, job_id):
        """Return a snapshot of one retained job."""
        with self._lock:
            self._purge_expired_locked()
            return self._copy(self._require_locked(job_id))

    def cancel(self, job_id):
        """Request cancellation and return the updated job snapshot.

        Queued jobs become cancelled immediately. Running jobs retain their
        state until their consumer acknowledges cancellation; any registered
        cancellation callback is invoked outside the registry lock.
        """
        callback = None
        with self._lock:
            self._purge_expired_locked()
            job = self._require_locked(job_id)
            if job["state"] in TERMINAL_STATES:
                raise ValueError(
                    f"Job '{job_id}' is already terminal ({job['state']}) and cannot be cancelled"
                )
            job["cancel_requested"] = True
            if job["state"] == "queued":
                self._set_terminal_locked(job, "cancelled", error="Cancellation requested")
            else:
                callback = self._cancel_callbacks.get(job_id)
            snapshot = self._copy(job)

        if callback is not None:
            try:
                callback()
            except Exception:
                # The request is durable even if Blender cannot be interrupted
                # at this exact point; the consumer can acknowledge it later.
                pass
        return snapshot

    def list_jobs(self):
        """Return retained jobs ordered from oldest to newest."""
        with self._lock:
            self._purge_expired_locked()
            jobs = sorted(self._jobs.values(), key=lambda job: job["created_at"])
            return [self._copy(job) for job in jobs]

    def enqueue(self, job_id, callback):
        """Queue a consumer callback for a later main-thread execution slice."""
        with self._lock:
            job = self._require_locked(job_id)
            if job["state"] != "queued":
                raise ValueError(f"Job '{job_id}' is not queued")
            self._pending.append((job_id, callback))

    def run_pending(self, max_jobs=1):
        """Run up to ``max_jobs`` queued callbacks on the calling thread."""
        ran = 0
        while max_jobs is None or ran < max_jobs:
            with self._lock:
                self._purge_expired_locked()
                if not self._pending:
                    break
                job_id, callback = self._pending.popleft()
                job = self._jobs.get(job_id)
                if job is None or job["state"] != "queued":
                    continue
            try:
                callback()
            except Exception as exc:
                with self._lock:
                    job = self._jobs.get(job_id)
                    if job is not None and job["state"] in {"queued", "running"}:
                        self._set_terminal_locked(job, "failed", error=str(exc))
            ran += 1
        return ran

    def mark_running(self, job_id, progress=None):
        """Transition a queued job to running."""
        with self._lock:
            job = self._require_locked(job_id)
            if job["state"] != "queued":
                raise ValueError(f"Job '{job_id}' cannot transition from {job['state']} to running")
            job["state"] = "running"
            job["started_at"] = self._clock()
            job["progress"] = self._validate_progress(progress)
            return self._copy(job)

    def set_progress(self, job_id, progress):
        """Update progress for a running job."""
        with self._lock:
            job = self._require_locked(job_id)
            if job["state"] != "running":
                raise ValueError(f"Job '{job_id}' is not running")
            job["progress"] = self._validate_progress(progress)
            return self._copy(job)

    def mark_succeeded(self, job_id, result):
        """Transition a running job to succeeded with its command result."""
        with self._lock:
            job = self._require_locked(job_id)
            self._require_running_locked(job, "succeeded")
            self._set_terminal_locked(job, "succeeded", result=result, progress=1.0)
            return self._copy(job)

    def mark_failed(self, job_id, error):
        """Transition a queued or running job to failed."""
        with self._lock:
            job = self._require_locked(job_id)
            if job["state"] not in {"queued", "running"}:
                raise ValueError(f"Job '{job_id}' cannot transition from {job['state']} to failed")
            self._set_terminal_locked(job, "failed", error=error)
            return self._copy(job)

    def mark_cancelled(self, job_id, error="Cancellation requested"):
        """Acknowledge cancellation of a queued or running job."""
        with self._lock:
            job = self._require_locked(job_id)
            if job["state"] not in {"queued", "running"}:
                return self._copy(job)
            job["cancel_requested"] = True
            self._set_terminal_locked(job, "cancelled", error=error)
            return self._copy(job)

    def set_cancel_callback(self, job_id, callback):
        """Register a main-thread consumer callback for running cancellation."""
        with self._lock:
            job = self._require_locked(job_id)
            if job["state"] not in {"queued", "running"}:
                raise ValueError(f"Job '{job_id}' is already terminal")
            self._cancel_callbacks[job_id] = callback

    def clear_cancel_callback(self, job_id):
        with self._lock:
            self._cancel_callbacks.pop(job_id, None)

    def _set_terminal_locked(self, job, state, result=None, error=None, progress=None):
        job["state"] = state
        job["finished_at"] = self._clock()
        job["result"] = result
        job["error"] = error
        if progress is not None:
            job["progress"] = self._validate_progress(progress)
        self._cancel_callbacks.pop(job["id"], None)

    def _purge_expired_locked(self, now=None):
        now = self._clock() if now is None else now
        expired = [
            job_id for job_id, job in self._jobs.items()
            if job["state"] in TERMINAL_STATES
            and job["finished_at"] is not None
            and now - job["finished_at"] >= self._ttl_seconds
        ]
        for job_id in expired:
            del self._jobs[job_id]
            self._cancel_callbacks.pop(job_id, None)

    def _require_locked(self, job_id):
        job = self._jobs.get(job_id)
        if job is None:
            raise JobNotFoundError(f"Job '{job_id}' was not found")
        return job

    @staticmethod
    def _require_running_locked(job, target_state):
        if job["state"] != "running":
            raise ValueError(
                f"Job '{job['id']}' cannot transition from {job['state']} to {target_state}"
            )

    @staticmethod
    def _validate_progress(progress):
        if progress is None:
            return None
        value = float(progress)
        if not 0.0 <= value <= 1.0:
            raise ValueError("Job progress must be between 0.0 and 1.0")
        return value

    @staticmethod
    def _copy(job):
        return copy.deepcopy(job)

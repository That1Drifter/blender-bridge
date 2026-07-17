"""End-to-end acceptance test for async Blender Bridge render jobs.

Run with the system Python: ``python tests/test_jobs.py``.
"""

import os
import signal
import socket
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
BLENDER_EXE = Path(r"C:/Program Files/Blender Foundation/Blender 4.5/blender.exe")
PORT = 9877
TERMINAL_STATES = {"succeeded", "failed", "cancelled"}
JOB_FIELDS = {
    "id", "command", "state", "progress", "created_at", "started_at",
    "finished_at", "result", "error", "cancel_requested",
}

sys.path.insert(0, str(REPO_ROOT))
from bridge_client import BridgeClient  # noqa: E402


def wait_for_port(process, timeout=30.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError("Blender exited before opening the bridge port")
        try:
            with socket.create_connection(("localhost", PORT), timeout=0.25):
                return
        except OSError:
            time.sleep(0.1)
    raise TimeoutError(f"Bridge did not accept connections on port {PORT} within {timeout}s")


def stop_process(process):
    if process.poll() is not None:
        return
    try:
        if os.name == "nt":
            process.send_signal(signal.CTRL_BREAK_EVENT)
        else:
            process.send_signal(signal.SIGINT)
        process.wait(timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=10)


def main():
    if not BLENDER_EXE.is_file():
        raise FileNotFoundError(f"Blender executable not found: {BLENDER_EXE}")

    output_path = Path(tempfile.gettempdir()) / f"bbridge_job_{uuid.uuid4().hex}.png"
    creationflags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
    process = subprocess.Popen(
        [
            str(BLENDER_EXE), "--background", "--factory-startup",
            "--python", str(REPO_ROOT / "start_bridge.py"), "--", "--port", str(PORT),
        ],
        cwd=REPO_ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        creationflags=creationflags,
    )

    try:
        wait_for_port(process)
        with BridgeClient(port=PORT, timeout=60.0) as client:
            capabilities = client.send("get_capabilities")
            assert capabilities["status"] == "success", capabilities
            assert capabilities["result"]["features"]["jobs"] is True, capabilities

            submission = client.send(
                "render_image",
                {
                    "async_mode": True,
                    "engine": "WORKBENCH",
                    "samples": 1,
                    "resolution": [32, 32],
                    "save_to": str(output_path),
                },
            )
            print(f"submission: {submission}")
            assert submission["status"] == "success", submission
            assert submission["result"]["state"] == "queued", submission
            job_id = submission["result"]["job_id"]

            ping = client.send("ping")
            print(f"ping after submission: {ping}")
            assert ping["status"] == "success" and ping["result"] == "pong", ping

            first_status = client.send("get_job_status", {"job_id": job_id})
            print(f"first status: {first_status}")
            assert first_status["status"] == "success", first_status
            assert first_status["result"]["id"] == job_id, first_status
            assert set(first_status["result"]) == JOB_FIELDS, first_status

            deadline = time.monotonic() + 60.0
            status = first_status
            while status["result"]["state"] not in TERMINAL_STATES:
                if time.monotonic() >= deadline:
                    raise TimeoutError(f"Job did not finish: {status}")
                time.sleep(0.05)
                status = client.send("get_job_status", {"job_id": job_id})

            print(f"terminal status: {status}")
            assert status["result"]["state"] == "succeeded", status
            assert isinstance(status["result"]["result"], dict), status
            assert status["result"]["result"]["file_path"] == str(output_path), status
            assert output_path.is_file(), output_path

            bogus = client.send("get_job_status", {"job_id": str(uuid.uuid4())})
            print(f"bogus status: {bogus}")
            assert bogus["status"] == "error", bogus
            assert bogus["error"]["code"] == "JOB_NOT_FOUND", bogus

            cancel_finished = client.send("cancel_job", {"job_id": job_id})
            print(f"cancel finished: {cancel_finished}")
            assert cancel_finished["status"] == "error", cancel_finished
            assert cancel_finished["error"]["code"] == "INVALID_PARAMS", cancel_finished

            jobs = client.send("list_jobs")
            print(f"list jobs: {jobs}")
            assert jobs["status"] == "success", jobs
            assert any(job["id"] == job_id for job in jobs["result"]), jobs

        print("ASYNC JOB E2E PASS")
    finally:
        stop_process(process)
        if output_path.exists():
            output_path.unlink()
        output = process.stdout.read() if process.stdout else ""
        if output:
            print("Blender output:")
            print(output.rstrip())


if __name__ == "__main__":
    main()

"""Headless acceptance test for the four high-level asset recipes.

Run with the system Python: ``python tests/test_recipes.py``.
"""

import hashlib
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
PORT = 9878
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
    raise TimeoutError("Bridge did not open its port")


def stop_process(process):
    if process.poll() is not None:
        return
    try:
        process.send_signal(signal.CTRL_BREAK_EVENT if os.name == "nt" else signal.SIGINT)
        process.wait(timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        process.terminate()
        process.wait(timeout=10)


def sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main():
    if not BLENDER_EXE.is_file():
        raise FileNotFoundError(BLENDER_EXE)
    output_path = Path(tempfile.gettempdir()) / f"bbridge_recipe_{uuid.uuid4().hex}.gltf"
    flags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
    process = subprocess.Popen(
        [str(BLENDER_EXE), "--background", "--factory-startup", "--python",
         str(REPO_ROOT / "start_bridge.py"), "--", "--port", str(PORT)],
        cwd=REPO_ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        creationflags=flags,
    )
    try:
        wait_for_port(process)
        with BridgeClient(port=PORT, timeout=60.0) as client:
            added = client.send("add_object", {"type": "CUBE", "name": "RecipeCube"})
            assert added["status"] == "success", added

            validation = client.send("validate_game_asset", {"object": "RecipeCube"})
            print(f"validation: {validation}")
            assert validation["status"] == "success", validation
            manifest = validation["result"]
            assert manifest["schema_version"] == "1", manifest
            assert manifest["recipe"] == "validate_game_asset", manifest
            assert manifest["warnings"], manifest
            assert manifest["phases"], manifest

            exported = client.send("export_game_asset", {
                "object": "RecipeCube", "out_path": str(output_path), "preset": "godot",
            })
            print(f"export: {exported}")
            assert exported["status"] == "success", exported
            manifest = exported["result"]
            assert manifest["status"] == "succeeded", manifest
            assert output_path.is_file(), manifest
            assert manifest["generated_files"][0]["sha256"] == sha256(output_path), manifest

            missing = client.send("export_game_asset", {
                "object": "NoSuchRecipeObject", "out_path": str(output_path), "preset": "godot",
            })
            print(f"missing object: {missing}")
            assert missing["status"] == "error", missing
            assert missing["error"]["code"] == "OBJECT_NOT_FOUND", missing
        print("RECIPE E2E PASS")
    finally:
        stop_process(process)
        for path in (output_path, output_path.with_suffix(".bin")):
            if path.exists():
                path.unlink()
        output = process.stdout.read() if process.stdout else ""
        if output:
            print("Blender output:")
            print(output.rstrip())


if __name__ == "__main__":
    main()

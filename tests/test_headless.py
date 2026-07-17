"""End-to-end acceptance test for the background Blender Bridge launcher."""

import os
import signal
import socket
import subprocess
import sys
import time
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
BLENDER_EXE = Path(os.environ.get(
    "BLENDER_EXE", r"C:/Program Files/Blender Foundation/Blender 4.5/blender.exe"
))
PORT = 9876

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


@unittest.skipUnless(BLENDER_EXE.is_file(), "Blender not available")
class HeadlessBridgeTests(unittest.TestCase):
    def test_background_bridge(self):
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
            with BridgeClient(port=PORT, timeout=10.0) as client:
                ping = client.send("ping")
                print(f"ping: {ping}")
                self.assertEqual(ping["status"], "success", ping)
                self.assertEqual(ping["result"], "pong", ping)

                scene = client.send("get_scene_info")
                print(f"get_scene_info: {scene}")
                self.assertEqual(scene["status"], "success", scene)

                capabilities = client.send("get_capabilities")
                print(f"get_capabilities: {capabilities}")
                self.assertEqual(capabilities["status"], "success", capabilities)
                self.assertTrue(capabilities["result"]["background"], capabilities)
                self.assertFalse(capabilities["result"]["features"]["screenshots"], capabilities)

                screenshot = client.send("get_viewport_screenshot")
                print(f"get_viewport_screenshot: {screenshot}")
                self.assertEqual(screenshot["status"], "error", screenshot)
                self.assertEqual(screenshot["error"]["code"], "UNSUPPORTED_IN_BACKGROUND", screenshot)
        finally:
            stop_process(process)
            output = process.stdout.read() if process.stdout else ""
            if process.stdout:
                process.stdout.close()
            if output:
                print("Blender output:")
                print(output.rstrip())


if __name__ == "__main__":
    unittest.main(verbosity=2)

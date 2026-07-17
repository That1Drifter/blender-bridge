"""Bpy-free cache acceptance tests for Poly Haven downloads.

Run with the system Python: ``python tests/test_polyhaven_cache.py``.
"""

import http.server
import importlib.util
import os
import sys
import tempfile
import threading
import time
import types
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


def load_polyhaven_module():
    """Load the integration without executing Blender Bridge's bpy package init."""
    package = types.ModuleType("blender_bridge")
    package.__path__ = [str(REPO_ROOT / "blender_bridge")]
    sys.modules["blender_bridge"] = package
    integrations = types.ModuleType("blender_bridge.integrations")
    integrations.__path__ = [str(REPO_ROOT / "blender_bridge" / "integrations")]
    sys.modules["blender_bridge.integrations"] = integrations

    constants_spec = importlib.util.spec_from_file_location(
        "blender_bridge.constants", REPO_ROOT / "blender_bridge" / "constants.py"
    )
    constants = importlib.util.module_from_spec(constants_spec)
    sys.modules[constants_spec.name] = constants
    constants_spec.loader.exec_module(constants)

    module_spec = importlib.util.spec_from_file_location(
        "blender_bridge.integrations.polyhaven",
        REPO_ROOT / "blender_bridge" / "integrations" / "polyhaven.py",
    )
    module = importlib.util.module_from_spec(module_spec)
    sys.modules[module_spec.name] = module
    module_spec.loader.exec_module(module)
    return module


ph = load_polyhaven_module()


class CacheHandler(http.server.BaseHTTPRequestHandler):
    request_counts = {}
    started_slow = threading.Event()
    files = {
        "/asset.bin": b"complete cache payload",
        "/recover.bin": b"recovered cache payload",
        "/slow.bin": b"x" * (1024 * 1024),
    }

    def do_GET(self):
        body = self.files[self.path]
        self.request_counts[self.path] = self.request_counts.get(self.path, 0) + 1
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if self.path == "/slow.bin":
            self.started_slow.set()
            for offset in range(0, len(body), 1024):
                try:
                    self.wfile.write(body[offset:offset + 1024])
                    self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                    return
                time.sleep(0.002)
        else:
            self.wfile.write(body)

    def log_message(self, _format, *_args):
        pass


class PolyHavenCacheTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), CacheHandler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base_url = f"http://127.0.0.1:{cls.server.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.thread.join(timeout=5)
        cls.server.server_close()

    def setUp(self):
        self.cache = tempfile.TemporaryDirectory()
        self.previous_cache = os.environ.get("BLENDER_BRIDGE_PH_CACHE")
        os.environ["BLENDER_BRIDGE_PH_CACHE"] = self.cache.name
        CacheHandler.request_counts = {}
        CacheHandler.started_slow.clear()

    def tearDown(self):
        if self.previous_cache is None:
            os.environ.pop("BLENDER_BRIDGE_PH_CACHE", None)
        else:
            os.environ["BLENDER_BRIDGE_PH_CACHE"] = self.previous_cache
        self.cache.cleanup()

    def test_atomic_cache_hit_and_part_recovery(self):
        url = self.base_url + "/asset.bin"
        path = ph.download_to_cache("fake_asset", "2k", "asset.bin", url)
        self.assertEqual(Path(path).read_bytes(), CacheHandler.files["/asset.bin"])
        self.assertFalse(Path(path + ".part").exists())
        self.assertEqual(CacheHandler.request_counts["/asset.bin"], 1)

        cached = ph.download_to_cache("fake_asset", "2k", "asset.bin", url)
        self.assertEqual(cached, path)
        self.assertEqual(CacheHandler.request_counts["/asset.bin"], 1)

        recovery_path = ph.cache_file_path("fake_asset", "2k", "recover.bin")
        Path(recovery_path).parent.mkdir(parents=True, exist_ok=True)
        Path(recovery_path + ".part").write_bytes(b"corrupt partial")
        ph.download_to_cache("fake_asset", "2k", "recover.bin", self.base_url + "/recover.bin")
        self.assertEqual(Path(recovery_path).read_bytes(), CacheHandler.files["/recover.bin"])
        self.assertFalse(Path(recovery_path + ".part").exists())
        self.assertEqual(CacheHandler.request_counts["/recover.bin"], 1)

    def test_cancelled_transfer_leaves_no_cache_entry(self):
        cancelled = threading.Event()
        errors = []

        def transfer():
            try:
                ph.download_to_cache(
                    "fake_asset", "4k", "slow.bin", self.base_url + "/slow.bin",
                    cancel_requested=cancelled.is_set, chunk_size=1024,
                )
            except Exception as exc:  # Assert the worker's exception in this thread.
                errors.append(exc)

        thread = threading.Thread(target=transfer)
        thread.start()
        self.assertTrue(CacheHandler.started_slow.wait(timeout=5))
        time.sleep(0.02)
        cancelled.set()
        thread.join(timeout=5)
        self.assertFalse(thread.is_alive())
        self.assertEqual(len(errors), 1)
        self.assertIsInstance(errors[0], ph.DownloadCancelled)

        path = ph.cache_file_path("fake_asset", "4k", "slow.bin")
        self.assertFalse(Path(path).exists())
        self.assertFalse(Path(path + ".part").exists())


if __name__ == "__main__":
    unittest.main(verbosity=2)

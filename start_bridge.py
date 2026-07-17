"""Launch Blender Bridge in Blender background mode.

Run with:
    blender --background --factory-startup --python start_bridge.py [-- --port 9876]
"""

import os
import signal
import sys
import time
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import bpy  # noqa: E402
import blender_bridge  # noqa: E402
from blender_bridge.constants import DEFAULT_PORT  # noqa: E402


def _parse_port(argv):
    """Read ``-- --port N`` after Blender's argument separator."""
    port_value = os.environ.get("BLENDER_BRIDGE_PORT", str(DEFAULT_PORT))
    if "--" in argv:
        bridge_args = argv[argv.index("--") + 1:]
        if "--port" in bridge_args:
            index = bridge_args.index("--port")
            if index + 1 >= len(bridge_args):
                raise ValueError("--port requires a value")
            port_value = bridge_args[index + 1]
    try:
        port = int(port_value)
    except ValueError as exc:
        raise ValueError(f"Invalid bridge port: {port_value!r}") from exc
    if not 1 <= port <= 65535:
        raise ValueError(f"Bridge port must be between 1 and 65535, got {port}")
    return port


def _restore_signal_handler(signum, previous):
    """Restore a valid previous Python handler when Blender supplied one."""
    if previous in (signal.SIG_DFL, signal.SIG_IGN) or callable(previous):
        signal.signal(signum, previous)


def main():
    port = _parse_port(sys.argv)
    stop_requested = False

    def request_stop(_signum, _frame):
        nonlocal stop_requested
        stop_requested = True

    previous_sigint = signal.signal(signal.SIGINT, request_stop)
    previous_sigbreak = None
    if hasattr(signal, "SIGBREAK"):
        previous_sigbreak = signal.signal(signal.SIGBREAK, request_stop)
    try:
        blender_bridge.register()
        blender_bridge._start_server(port)
        server = blender_bridge._get_server()
        if server is None or not server.running:
            raise RuntimeError(f"Blender Bridge failed to start on port {port}")

        print(f"[Bridge] Headless request pump running on localhost:{port}")
        while server.running and not stop_requested:
            processed = server.process_pending_requests()
            if not processed:
                time.sleep(0.01)
    except KeyboardInterrupt:
        print("[Bridge] KeyboardInterrupt received; shutting down")
    finally:
        _restore_signal_handler(signal.SIGINT, previous_sigint)
        if hasattr(signal, "SIGBREAK"):
            _restore_signal_handler(signal.SIGBREAK, previous_sigbreak)
        blender_bridge._stop_server()
        blender_bridge.unregister()
        print("[Bridge] Headless bridge stopped")


if __name__ == "__main__":
    main()

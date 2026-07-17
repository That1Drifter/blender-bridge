# Blender Bridge
# A production assistant bridge for controlling Blender via Claude

import bpy

bl_info = {
    "name": "Blender Bridge",
    "author": "Drifter",
    "version": (2, 0, 0),
    "blender": (4, 0, 0),
    "location": "View3D > Sidebar > Bridge",
    "description": "Connect Blender to Claude — texturing, scene setup, export, and asset management",
    "category": "Interface",
}

from .server import BlenderBridgeServer
from .dispatcher import Dispatcher
from .constants import DEFAULT_HOST

# Module-level singleton
_server_instance = None
_dispatcher_instance = None


@bpy.app.handlers.persistent
def _clear_checkpoints_after_load(_unused):
    """Discard bridge checkpoints after Blender rebuilds undo history on file load."""
    if _dispatcher_instance:
        _dispatcher_instance._checkpoint_mgr.clear_all("file_loaded")


def _get_server():
    return _server_instance


def _start_server(port):
    global _server_instance, _dispatcher_instance

    if _server_instance and _server_instance.running:
        _server_instance.stop()

    _dispatcher_instance = Dispatcher()
    _server_instance = BlenderBridgeServer(host=DEFAULT_HOST, port=port)
    _server_instance.set_dispatcher(_dispatcher_instance)
    _server_instance.start()


def _stop_server():
    global _server_instance, _dispatcher_instance

    if _server_instance:
        _server_instance.stop()
        _server_instance = None
    _dispatcher_instance = None


def register():
    from .ui import CLASSES, register_properties

    for cls in CLASSES:
        bpy.utils.register_class(cls)
    register_properties()
    if _clear_checkpoints_after_load not in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.append(_clear_checkpoints_after_load)
    print("[Bridge] Blender Bridge addon registered")


def unregister():
    from .ui import CLASSES, unregister_properties

    _stop_server()
    if _clear_checkpoints_after_load in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(_clear_checkpoints_after_load)
    unregister_properties()
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
    print("[Bridge] Blender Bridge addon unregistered")

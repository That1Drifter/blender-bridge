# Blender Bridge — UI Panel & Operators

import bpy
from bpy.props import IntProperty, BoolProperty


class BBRIDGE_OT_Connect(bpy.types.Operator):
    bl_idname = "bbridge.connect"
    bl_label = "Connect"
    bl_description = "Start the Bridge TCP server for Claude"

    def execute(self, context):
        from . import _get_server
        server = _get_server()
        if server and server.running:
            self.report({"WARNING"}, "Already connected")
            return {"CANCELLED"}

        from . import _start_server
        port = context.scene.bbridge_port
        _start_server(port)
        context.scene.bbridge_connected = True
        return {"FINISHED"}


class BBRIDGE_OT_Disconnect(bpy.types.Operator):
    bl_idname = "bbridge.disconnect"
    bl_label = "Disconnect"
    bl_description = "Stop the Bridge TCP server"

    def execute(self, context):
        from . import _stop_server
        _stop_server()
        context.scene.bbridge_connected = False
        return {"FINISHED"}


class BBRIDGE_PT_Panel(bpy.types.Panel):
    bl_label = "Blender Bridge"
    bl_idname = "BBRIDGE_PT_Panel"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Bridge"

    def draw(self, context):
        layout = self.layout
        scene = context.scene

        box = layout.box()
        box.label(text="Connection", icon="LINKED")
        row = box.row()
        row.prop(scene, "bbridge_port", text="Port")

        if scene.bbridge_connected:
            box.operator("bbridge.disconnect", text="Disconnect", icon="CANCEL")
            box.label(text=f"Running on port {scene.bbridge_port}", icon="CHECKMARK")
        else:
            box.operator("bbridge.connect", text="Connect", icon="PLAY")

        box = layout.box()
        box.prop(scene, "bbridge_show_defaults", text="Defaults",
                 icon="TRIA_DOWN" if scene.bbridge_show_defaults else "TRIA_RIGHT",
                 emboss=False)
        if scene.bbridge_show_defaults:
            box.prop(scene, "bbridge_auto_diff", text="Auto-include scene diff")
            box.prop(scene, "bbridge_auto_screenshot", text="Auto-include screenshot")
            box.prop(scene, "bbridge_screenshot_size", text="Screenshot size")

        box = layout.box()
        box.label(text="Security", icon="LOCKED")
        box.prop(scene, "bbridge_allow_raw_exec", text="Allow Raw Exec")
        if scene.bbridge_allow_raw_exec:
            box.label(text="Raw Python execution enabled", icon="ERROR")


CLASSES = [
    BBRIDGE_OT_Connect,
    BBRIDGE_OT_Disconnect,
    BBRIDGE_PT_Panel,
]


def register_properties():
    from .constants import (
        ALLOW_RAW_EXEC, DEFAULT_INCLUDE_DIFF, DEFAULT_INCLUDE_SCREENSHOT, DEFAULT_PORT,
        DEFAULT_SCREENSHOT_SIZE,
    )
    bpy.types.Scene.bbridge_port = IntProperty(
        name="Port", default=DEFAULT_PORT, min=1024, max=65535
    )
    bpy.types.Scene.bbridge_connected = BoolProperty(name="Connected", default=False)
    bpy.types.Scene.bbridge_show_defaults = BoolProperty(name="Show Defaults", default=False)
    bpy.types.Scene.bbridge_auto_diff = BoolProperty(
        name="Auto Diff", default=DEFAULT_INCLUDE_DIFF
    )
    bpy.types.Scene.bbridge_auto_screenshot = BoolProperty(
        name="Auto Screenshot", default=DEFAULT_INCLUDE_SCREENSHOT
    )
    bpy.types.Scene.bbridge_screenshot_size = IntProperty(
        name="Screenshot Size", default=DEFAULT_SCREENSHOT_SIZE, min=256, max=2048
    )
    bpy.types.Scene.bbridge_allow_raw_exec = BoolProperty(
        name="Allow Raw Exec",
        description="Allow arbitrary Python execution with Blender process permissions",
        default=ALLOW_RAW_EXEC,
    )


def unregister_properties():
    props = [
        "bbridge_port", "bbridge_connected", "bbridge_show_defaults",
        "bbridge_auto_diff", "bbridge_auto_screenshot", "bbridge_screenshot_size",
        "bbridge_allow_raw_exec",
    ]
    for p in props:
        if hasattr(bpy.types.Scene, p):
            delattr(bpy.types.Scene, p)

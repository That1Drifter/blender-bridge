# Blender MCP Addon v2 — UI Panel & Operators

import bpy
from bpy.props import IntProperty, BoolProperty


class BMCP_OT_Connect(bpy.types.Operator):
    bl_idname = "bmcp.connect"
    bl_label = "Connect to MCP Server"
    bl_description = "Start the MCP TCP server for Claude"

    def execute(self, context):
        from . import _get_server
        server = _get_server()
        if server and server.running:
            self.report({"WARNING"}, "Already connected")
            return {"CANCELLED"}

        from . import _start_server
        port = context.scene.bmcp_port
        _start_server(port)
        context.scene.bmcp_connected = True
        return {"FINISHED"}


class BMCP_OT_Disconnect(bpy.types.Operator):
    bl_idname = "bmcp.disconnect"
    bl_label = "Disconnect from MCP Server"
    bl_description = "Stop the MCP TCP server"

    def execute(self, context):
        from . import _stop_server
        _stop_server()
        context.scene.bmcp_connected = False
        return {"FINISHED"}


class BMCP_PT_Panel(bpy.types.Panel):
    bl_label = "Blender MCP v2"
    bl_idname = "BMCP_PT_Panel"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "BlenderMCP"

    def draw(self, context):
        layout = self.layout
        scene = context.scene

        # Connection section
        box = layout.box()
        box.label(text="Connection", icon="LINKED")
        row = box.row()
        row.prop(scene, "bmcp_port", text="Port")

        if scene.bmcp_connected:
            box.operator("bmcp.disconnect", text="Disconnect", icon="CANCEL")
            box.label(text=f"Running on port {scene.bmcp_port}", icon="CHECKMARK")
        else:
            box.operator("bmcp.connect", text="Connect", icon="PLAY")

        # Defaults section (collapsible)
        box = layout.box()
        box.prop(scene, "bmcp_show_defaults", text="Defaults",
                 icon="TRIA_DOWN" if scene.bmcp_show_defaults else "TRIA_RIGHT",
                 emboss=False)
        if scene.bmcp_show_defaults:
            box.prop(scene, "bmcp_auto_diff", text="Auto-include scene diff")
            box.prop(scene, "bmcp_auto_screenshot", text="Auto-include screenshot")
            box.prop(scene, "bmcp_screenshot_size", text="Screenshot size")


# All classes to register
CLASSES = [
    BMCP_OT_Connect,
    BMCP_OT_Disconnect,
    BMCP_PT_Panel,
]


def register_properties():
    from .constants import DEFAULT_PORT, DEFAULT_SCREENSHOT_SIZE
    bpy.types.Scene.bmcp_port = IntProperty(
        name="Port", default=DEFAULT_PORT, min=1024, max=65535
    )
    bpy.types.Scene.bmcp_connected = BoolProperty(name="Connected", default=False)
    bpy.types.Scene.bmcp_show_defaults = BoolProperty(name="Show Defaults", default=False)
    bpy.types.Scene.bmcp_auto_diff = BoolProperty(name="Auto Diff", default=True)
    bpy.types.Scene.bmcp_auto_screenshot = BoolProperty(name="Auto Screenshot", default=False)
    bpy.types.Scene.bmcp_screenshot_size = IntProperty(
        name="Screenshot Size", default=DEFAULT_SCREENSHOT_SIZE, min=256, max=2048
    )


def unregister_properties():
    props = [
        "bmcp_port", "bmcp_connected", "bmcp_show_defaults",
        "bmcp_auto_diff", "bmcp_auto_screenshot", "bmcp_screenshot_size",
    ]
    for p in props:
        if hasattr(bpy.types.Scene, p):
            delattr(bpy.types.Scene, p)

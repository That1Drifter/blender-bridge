#!/usr/bin/env python
"""
Blender Bridge Server — MCP interface between MCP clients and Blender.

Exposes Blender commands as MCP tools. Connects to the Blender addon's
TCP socket server on localhost:9876 using length-prefixed JSON framing.

Usage:
    python bridge_server.py
"""

import json
from mcp.server.fastmcp import FastMCP
from bridge_client import BridgeClient

# ---------------------------------------------------------------------------
# TCP client for Blender addon
# ---------------------------------------------------------------------------

BLENDER_HOST = "localhost"
BLENDER_PORT = 9876

# Timeouts per command category (seconds)
_TIMEOUT_DEFAULT = 30
_TIMEOUT_RENDER = 120
_TIMEOUT_DOWNLOAD = 300

_SLOW_COMMANDS = {
    "render_image": _TIMEOUT_RENDER,
    "export_scene": _TIMEOUT_RENDER,
    "polyhaven_download": _TIMEOUT_DOWNLOAD,
    "execute_code": _TIMEOUT_RENDER,
}

# Global connection instance
_connection = BridgeClient(
    host=BLENDER_HOST,
    port=BLENDER_PORT,
    timeout=_TIMEOUT_DEFAULT,
    command_timeouts=_SLOW_COMMANDS,
    max_connect_retries=3,
)


def blender_command(cmd_type: str, params: dict = None, options: dict = None) -> dict:
    """Send a command to Blender and return the response."""
    return _connection.send(cmd_type, params, options)


def _mutating_result(r: dict) -> str:
    """Format a mutating command response with result + scene diff."""
    output = {"result": r.get("result")}
    if r.get("diff"):
        output["scene_changes"] = r["diff"]
    if r.get("error"):
        output["error"] = r["error"]
    return json.dumps(output, indent=2)


# ---------------------------------------------------------------------------
# MCP Server
# ---------------------------------------------------------------------------

mcp = FastMCP("Blender Bridge", dependencies=["mcp"])


# --- Scene Introspection ---

@mcp.tool()
def get_scene_info(limit: int = 50, offset: int = 0) -> str:
    """Get an overview of the current Blender scene including objects, collections, render engine, and frame range. Use this first to understand what's in the scene."""
    r = blender_command("get_scene_info", {"limit": limit, "offset": offset})
    return json.dumps(r.get("result", r), indent=2)


@mcp.tool()
def get_object_info(name: str, include_modifiers: bool = True,
                    include_materials: bool = True,
                    include_constraints: bool = True) -> str:
    """Get detailed information about a specific Blender object including its transform, mesh data, modifiers, materials, and bounding box."""
    r = blender_command("get_object_info", {
        "name": name,
        "include_modifiers": include_modifiers,
        "include_materials": include_materials,
        "include_constraints": include_constraints,
    })
    return json.dumps(r.get("result", r), indent=2)


@mcp.tool()
def get_object_bounds(name: str) -> str:
    """Get world-space bounding box for an object. Returns min, max, center, and dimensions."""
    r = blender_command("get_object_bounds", {"name": name})
    return json.dumps(r.get("result", r), indent=2)


@mcp.tool()
def get_material_info(name: str) -> str:
    """Get the full shader node tree for a material, including all nodes, their inputs/values, and connections."""
    r = blender_command("get_material_info", {"name": name})
    return json.dumps(r.get("result", r), indent=2)


@mcp.tool()
def get_world_info() -> str:
    """Get world/environment settings including background color and HDRI configuration."""
    r = blender_command("get_world_info")
    return json.dumps(r.get("result", r), indent=2)


@mcp.tool()
def get_render_settings() -> str:
    """Get current render settings: engine, resolution, samples, frame range."""
    r = blender_command("get_render_settings")
    return json.dumps(r.get("result", r), indent=2)


@mcp.tool()
def list_objects(type_filter: str = "", collection: str = "",
                 limit: int = 100, offset: int = 0) -> str:
    """List objects in the scene with optional filtering by type (MESH, LIGHT, CAMERA, etc.) or collection name."""
    params = {"limit": limit, "offset": offset}
    if type_filter:
        params["type_filter"] = type_filter
    if collection:
        params["collection"] = collection
    r = blender_command("list_objects", params)
    return json.dumps(r.get("result", r), indent=2)


# --- Code Execution ---

@mcp.tool()
def execute_blender_code(code: str, mode: str = "exec") -> str:
    """Execute arbitrary Blender Python code. The namespace includes bpy, mathutils, Vector, Euler, Matrix, Quaternion, Color, and math. Use print() to return values. Mode can be 'exec' (full access) or 'safe' (blocks os/sys/subprocess/open). Returns stdout output and a scene diff showing what changed."""
    r = blender_command("execute_code", {"code": code, "mode": mode},
                        options={"include_diff": True})
    output = {}
    if r.get("result"):
        output["result"] = r["result"]
    if r.get("diff"):
        output["scene_changes"] = r["diff"]
    if r.get("error"):
        output["error"] = r["error"]
    return json.dumps(output, indent=2)


# --- Structured Object Manipulation ---

@mcp.tool()
def add_object(type: str, name: str = "", location: list = None,
               rotation: list = None, scale: list = None,
               segments: int = 0, ring_count: int = 0, vertices: int = 0,
               radius: float = 0, depth: float = 0, size: float = 0) -> str:
    """Add a primitive object to the scene. Types: CUBE, SPHERE, PLANE, CYLINDER, CONE, TORUS, CIRCLE, MONKEY, EMPTY, CAMERA, LIGHT_POINT, LIGHT_SUN, LIGHT_SPOT, LIGHT_AREA. For spheres: segments (default 32), ring_count (default 16). For cylinders: vertices (default 32). Returns the created object info and scene diff."""
    params = {"type": type}
    if name:
        params["name"] = name
    if location:
        params["location"] = location
    if rotation:
        params["rotation"] = rotation
    if scale:
        params["scale"] = scale
    if segments:
        params["segments"] = segments
    if ring_count:
        params["ring_count"] = ring_count
    if vertices:
        params["vertices"] = vertices
    if radius:
        params["radius"] = radius
    if depth:
        params["depth"] = depth
    if size:
        params["size"] = size
    r = blender_command("add_object", params, options={"include_diff": True})
    return _mutating_result(r)


@mcp.tool()
def delete_object(name: str) -> str:
    """Delete an object from the scene by name."""
    r = blender_command("delete_object", {"name": name}, options={"include_diff": True})
    return _mutating_result(r)


@mcp.tool()
def set_transform(name: str, location: list = None,
                  rotation: list = None, scale: list = None) -> str:
    """Set an object's location, rotation (euler), and/or scale. Pass only the properties you want to change."""
    params = {"name": name}
    if location is not None:
        params["location"] = location
    if rotation is not None:
        params["rotation"] = rotation
    if scale is not None:
        params["scale"] = scale
    r = blender_command("set_transform", params, options={"include_diff": True})
    return json.dumps(r.get("result", r), indent=2)


@mcp.tool()
def set_parent(child: str, parent: str = "") -> str:
    """Set or clear an object's parent. Pass empty parent to unparent."""
    params = {"child": child}
    if parent:
        params["parent"] = parent
    r = blender_command("set_parent", params)
    return json.dumps(r.get("result", r), indent=2)


@mcp.tool()
def duplicate_object(name: str, new_name: str = "", linked: bool = False) -> str:
    """Duplicate an object. If linked=True, shares mesh data with the original."""
    params = {"name": name, "linked": linked}
    if new_name:
        params["new_name"] = new_name
    r = blender_command("duplicate_object", params, options={"include_diff": True})
    return json.dumps(r.get("result", r), indent=2)


@mcp.tool()
def set_visibility(name: str, visible: bool = None, render_visible: bool = None) -> str:
    """Set an object's visibility in the viewport and/or in renders."""
    params = {"name": name}
    if visible is not None:
        params["visible"] = visible
    if render_visible is not None:
        params["render_visible"] = render_visible
    r = blender_command("set_visibility", params)
    return json.dumps(r.get("result", r), indent=2)


# --- Camera ---

@mcp.tool()
def frame_camera(target: str = "", targets: list = None, camera: str = "",
                 padding: float = 1.2, exclude: list = None, types: list = None,
                 preserve_direction: bool = False) -> str:
    """Auto-aim the camera to frame the scene or specific objects. target: single object name.
    targets: list of object names. exclude: list of object names to skip.
    types: list of object types (default: MESH, CURVE, SURFACE, FONT). padding: 1.0=tight, 1.5=loose.
    preserve_direction: if True, keep camera's current angle; if False (default), use fresh 3/4 view."""
    params = {"padding": padding, "preserve_direction": preserve_direction}
    if target:
        params["target"] = target
    if targets:
        params["targets"] = targets
    if camera:
        params["camera"] = camera
    if exclude:
        params["exclude"] = exclude
    if types:
        params["types"] = types
    r = blender_command("frame_camera", params, options={"include_diff": True})
    return _mutating_result(r)


# --- Materials ---

@mcp.tool()
def create_material(name: str, base_color: list = None,
                    metallic: float = None, roughness: float = None) -> str:
    """Create a new material with Principled BSDF shader. base_color is [R, G, B, A] with values 0-1."""
    params = {"name": name}
    if base_color is not None:
        params["base_color"] = base_color
    if metallic is not None:
        params["metallic"] = metallic
    if roughness is not None:
        params["roughness"] = roughness
    r = blender_command("create_material", params)
    return json.dumps(r.get("result", r), indent=2)


@mcp.tool()
def set_material(object: str, material: str, slot: int = 0) -> str:
    """Assign an existing material to an object's material slot."""
    r = blender_command("set_material", {"object": object, "material": material, "slot": slot})
    return json.dumps(r.get("result", r), indent=2)


# --- Modifiers ---

@mcp.tool()
def add_modifier(object: str, type: str, name: str = "", properties: str = "") -> str:
    """Add a modifier to an object. Types: SUBSURF, MIRROR, ARRAY, SOLIDIFY, BEVEL, BOOLEAN, DECIMATE, SHRINKWRAP, REMESH, WIREFRAME, EDGE_SPLIT, WEIGHTED_NORMAL, SIMPLE_DEFORM, SMOOTH, CAST, CURVE, LATTICE, WELD, SKIN. properties: JSON string of modifier settings, e.g. '{"levels": 2, "render_levels": 3}' for SUBSURF."""
    p = {"object": object, "type": type}
    if name:
        p["name"] = name
    if properties:
        p.update(json.loads(properties))
    r = blender_command("add_modifier", p)
    return json.dumps(r.get("result", r), indent=2)


@mcp.tool()
def remove_modifier(object: str, modifier: str) -> str:
    """Remove a modifier from an object by name."""
    r = blender_command("remove_modifier", {"object": object, "modifier": modifier})
    return json.dumps(r.get("result", r), indent=2)


@mcp.tool()
def apply_modifier(object: str, modifier: str) -> str:
    """Apply (bake) a modifier, making its effect permanent on the mesh. The modifier is removed and its effect becomes part of the mesh geometry."""
    r = blender_command("apply_modifier", {"object": object, "modifier": modifier},
                        options={"include_diff": True})
    return _mutating_result(r)


# --- Object Operations ---

@mcp.tool()
def join_objects(objects: list, target: str = "") -> str:
    """Merge multiple mesh objects into one. objects: list of object names. target: which object receives the others (default: first in list)."""
    params = {"objects": objects}
    if target:
        params["target"] = target
    r = blender_command("join_objects", params, options={"include_diff": True})
    return _mutating_result(r)


# --- World / Environment ---

@mcp.tool()
def set_world(color: list = None, strength: float = None, hdri_path: str = "") -> str:
    """Set world/environment settings. color: [R, G, B, A] background color. strength: background intensity. hdri_path: path to HDRI image for environment lighting."""
    params = {}
    if color is not None:
        params["color"] = color
    if strength is not None:
        params["strength"] = strength
    if hdri_path:
        params["hdri_path"] = hdri_path
    r = blender_command("set_world", params, options={"include_diff": True})
    return _mutating_result(r)


# --- Export ---

# --- Lights ---

@mcp.tool()
def set_light(name: str, energy: float = None, color: list = None,
              type: str = "", spot_size: float = None, spot_blend: float = None,
              size: float = None, shadow_soft_size: float = None) -> str:
    """Configure a light's properties. type: POINT, SUN, SPOT, AREA. energy: light intensity. color: [R, G, B] (0-1). spot_size/spot_blend: for SPOT lights. size: for AREA lights."""
    params = {"name": name}
    if energy is not None:
        params["energy"] = energy
    if color is not None:
        params["color"] = color
    if type:
        params["type"] = type
    if spot_size is not None:
        params["spot_size"] = spot_size
    if spot_blend is not None:
        params["spot_blend"] = spot_blend
    if size is not None:
        params["size"] = size
    if shadow_soft_size is not None:
        params["shadow_soft_size"] = shadow_soft_size
    r = blender_command("set_light", params, options={"include_diff": True})
    return _mutating_result(r)


# --- Camera Settings ---

@mcp.tool()
def set_camera(name: str = "", lens: float = None, sensor_width: float = None,
               clip_start: float = None, clip_end: float = None,
               dof_enabled: bool = None, dof_focus_distance: float = None,
               dof_aperture: float = None) -> str:
    """Configure camera properties. lens: focal length in mm. dof_enabled: toggle depth of field. dof_focus_distance: focus distance. dof_aperture: f-stop value (lower = more blur)."""
    params = {}
    if name:
        params["name"] = name
    if lens is not None:
        params["lens"] = lens
    if sensor_width is not None:
        params["sensor_width"] = sensor_width
    if clip_start is not None:
        params["clip_start"] = clip_start
    if clip_end is not None:
        params["clip_end"] = clip_end
    if dof_enabled is not None:
        params["dof_enabled"] = dof_enabled
    if dof_focus_distance is not None:
        params["dof_focus_distance"] = dof_focus_distance
    if dof_aperture is not None:
        params["dof_aperture"] = dof_aperture
    r = blender_command("set_camera", params, options={"include_diff": True})
    return _mutating_result(r)


# --- Selection ---

@mcp.tool()
def select_objects(names: list, active: str = "", deselect_first: bool = True) -> str:
    """Select objects by name. names: list of object names. active: which becomes the active object (default: first). deselect_first: clear selection before selecting (default true)."""
    params = {"names": names, "deselect_first": deselect_first}
    if active:
        params["active"] = active
    r = blender_command("select_objects", params)
    return json.dumps(r.get("result", r), indent=2)


# --- Animation ---

@mcp.tool()
def set_keyframe(name: str, frame: int, property: str = "location",
                 value: list = None) -> str:
    """Insert a keyframe on an object. property: 'location', 'rotation_euler', 'scale'. value: set the property to this value before keying (e.g. [0, 0, 2] for location)."""
    params = {"name": name, "frame": frame, "property": property}
    if value is not None:
        params["value"] = value
    r = blender_command("set_keyframe", params, options={"include_diff": True})
    return _mutating_result(r)


# --- Mesh Creation ---

@mcp.tool()
def create_mesh(name: str, vertices: list = None, edges: list = None,
                faces: list = None, location: list = None) -> str:
    """Create a mesh from raw vertex/face data. vertices: list of [x,y,z] coords.
    faces: list of index lists, e.g. [[0,1,2]]. edges: list of [v1,v2] pairs (optional)."""
    params = {"name": name, "vertices": vertices or []}
    if edges:
        params["edges"] = edges
    if faces:
        params["faces"] = faces
    if location is not None:
        params["location"] = location
    r = blender_command("create_mesh", params, options={"include_diff": True})
    return _mutating_result(r)


# --- Batch Operations ---

@mcp.tool()
def batch_transform(transforms: list = None) -> str:
    """Set transforms on multiple objects in one call. transforms: list of dicts,
    e.g. [{"name":"Cube","location":[1,0,0]},{"name":"Sphere","scale":[2,2,2]}].
    Each entry can have name, location, rotation, scale."""
    r = blender_command("batch_transform", {"transforms": transforms or []},
                        options={"include_diff": True})
    return _mutating_result(r)


# --- Shader Nodes ---

@mcp.tool()
def add_shader_node(material: str, node_type: str, name: str = "",
                    location: list = None, properties: dict = None) -> str:
    """Add a node to a material's shader tree. node_type: Blender node identifier
    (e.g. ShaderNodeTexImage, ShaderNodeTexNoise, ShaderNodeBump, ShaderNodeMapping).
    location: [x, y]. properties: dict of input values, e.g. {"Scale": 5.0, "Detail": 3.0}."""
    params = {"material": material, "node_type": node_type}
    if name:
        params["name"] = name
    if location:
        params["location"] = location
    if properties:
        params["properties"] = properties
    r = blender_command("add_shader_node", params, options={"include_diff": True})
    return _mutating_result(r)


@mcp.tool()
def connect_shader_nodes(material: str, from_node: str, from_output: str,
                         to_node: str, to_input: str) -> str:
    """Connect two shader nodes. Use node names and socket names (e.g. from_output='Color', to_input='Base Color')."""
    r = blender_command("connect_shader_nodes", {
        "material": material,
        "from_node": from_node, "from_output": from_output,
        "to_node": to_node, "to_input": to_input,
    }, options={"include_diff": True})
    return _mutating_result(r)


@mcp.tool()
def remove_shader_node(material: str, node_name: str) -> str:
    """Remove a node from a material's shader tree."""
    r = blender_command("remove_shader_node", {
        "material": material, "node_name": node_name
    }, options={"include_diff": True})
    return _mutating_result(r)


# --- Collections ---

@mcp.tool()
def create_collection(name: str, parent: str = "") -> str:
    """Create a new collection. parent: name of parent collection (empty = scene root)."""
    params = {"name": name}
    if parent:
        params["parent"] = parent
    r = blender_command("create_collection", params, options={"include_diff": True})
    return _mutating_result(r)


@mcp.tool()
def move_to_collection(object: str, collection: str) -> str:
    """Move an object to a collection (removes from current collections)."""
    r = blender_command("move_to_collection", {
        "object": object, "collection": collection
    }, options={"include_diff": True})
    return _mutating_result(r)


@mcp.tool()
def set_collection_visibility(collection: str, visible: bool = None,
                              render_visible: bool = None) -> str:
    """Set collection visibility in viewport and/or render."""
    params = {"collection": collection}
    if visible is not None:
        params["visible"] = visible
    if render_visible is not None:
        params["render_visible"] = render_visible
    r = blender_command("set_collection_visibility", params)
    return json.dumps(r.get("result", r), indent=2)


# --- Mesh Editing ---

@mcp.tool()
def edit_mesh(object: str, operation: str, params: str = "") -> str:
    """Perform mesh editing operations. object: mesh object name. operation: subdivide, bevel, inset, extrude, merge, separate. params: JSON dict of operation-specific settings. Examples: bevel '{"width":0.1,"segments":3}', subdivide '{"cuts":2}', inset '{"thickness":0.05}', extrude '{"value":0.5}', merge '{"distance":0.001}'."""
    p = {"object": object, "operation": operation}
    if params:
        p["params"] = json.loads(params)
    r = blender_command("edit_mesh", p, options={"include_diff": True})
    return _mutating_result(r)


# --- Scene Controls ---

@mcp.tool()
def set_scene(fps: int = None, frame_start: int = None, frame_end: int = None,
              frame_current: int = None, active_camera: str = "") -> str:
    """Configure scene settings. fps: frames per second. frame_start/end: animation range. active_camera: set which camera renders."""
    params = {}
    if fps is not None:
        params["fps"] = fps
    if frame_start is not None:
        params["frame_start"] = frame_start
    if frame_end is not None:
        params["frame_end"] = frame_end
    if frame_current is not None:
        params["frame_current"] = frame_current
    if active_camera:
        params["active_camera"] = active_camera
    r = blender_command("set_scene", params, options={"include_diff": True})
    return _mutating_result(r)


# --- Render Settings ---

@mcp.tool()
def set_render_settings(engine: str = "", resolution_x: int = 0,
                        resolution_y: int = 0, samples: int = 0,
                        film_transparent: bool = None) -> str:
    """Update render settings. Valid engines: EEVEE, CYCLES, WORKBENCH. Set resolution, samples, transparency."""
    params = {}
    if engine:
        params["engine"] = engine
    if resolution_x:
        params["resolution_x"] = resolution_x
    if resolution_y:
        params["resolution_y"] = resolution_y
    if samples:
        params["samples"] = samples
    if film_transparent is not None:
        params["film_transparent"] = film_transparent
    r = blender_command("set_render_settings", params)
    return json.dumps(r.get("result", r), indent=2)


# --- Capture ---

@mcp.tool()
def get_viewport_screenshot(max_size: int = 512, save_to: str = "") -> str:
    """Capture a screenshot of the 3D viewport. If save_to path is provided, saves full image there and returns just the path. Otherwise returns a small JPEG thumbnail as base64 plus a file_path to the full resolution image."""
    params = {"max_size": max_size, "format": "PNG"}
    if save_to:
        params["save_to"] = save_to
    r = blender_command("get_viewport_screenshot", params)
    result = r.get("result", r)
    return json.dumps(result, indent=2)


@mcp.tool()
def render_image(engine: str = "", samples: int = 0,
                 resolution_x: int = 512, resolution_y: int = 512,
                 save_to: str = "", async_mode: bool = False) -> str:
    """Render the scene and return the image, or queue a job when async_mode is true. Valid engines: EEVEE, CYCLES, WORKBENCH. If save_to path is provided, saves full render there and returns just the path. Otherwise returns a small JPEG thumbnail as base64 plus a file_path to the full render."""
    params = {
        "resolution": [resolution_x, resolution_y],
        "format": "PNG",
        "async_mode": async_mode,
    }
    if engine:
        params["engine"] = engine
    if samples:
        params["samples"] = samples
    if save_to:
        params["save_to"] = save_to
    r = blender_command("render_image", params)
    result = r.get("result", r)
    return json.dumps(result, indent=2)


# --- Async Jobs ---

@mcp.tool()
def get_job_status(job_id: str) -> str:
    """Get the current state, progress, result, or error for an async job."""
    r = blender_command("get_job_status", {"job_id": job_id})
    return json.dumps(r.get("result", r), indent=2)


@mcp.tool()
def cancel_job(job_id: str) -> str:
    """Request cancellation of a queued or running async job."""
    r = blender_command("cancel_job", {"job_id": job_id})
    return json.dumps(r.get("result", r), indent=2)


@mcp.tool()
def list_jobs() -> str:
    """List all async jobs and their current states."""
    r = blender_command("list_jobs")
    return json.dumps(r.get("result", r), indent=2)


# --- Checkpoints ---

@mcp.tool()
def create_checkpoint(label: str = "checkpoint") -> str:
    """Create a named undo checkpoint. Use before making changes you might want to roll back."""
    r = blender_command("create_checkpoint", {"label": label})
    return json.dumps(r.get("result", r), indent=2)


@mcp.tool()
def list_checkpoints() -> str:
    """List all undo checkpoints with their IDs and labels."""
    r = blender_command("list_checkpoints")
    return json.dumps(r.get("result", r), indent=2)


@mcp.tool()
def restore_checkpoint(checkpoint_id: str) -> str:
    """Restore the scene to a previous checkpoint, undoing all changes made after it."""
    r = blender_command("restore_checkpoint", {"checkpoint_id": checkpoint_id})
    return json.dumps(r.get("result", r), indent=2)


# --- History ---

@mcp.tool()
def get_command_history(limit: int = 20) -> str:
    """Get recent command history showing what operations were performed."""
    r = blender_command("get_history", {"limit": limit})
    return json.dumps(r.get("result", r), indent=2)


# ---------------------------------------------------------------------------
# UV & Mesh Tools
# ---------------------------------------------------------------------------

@mcp.tool()
def uv_unwrap(object: str, method: str = "smart_project", params: dict = None) -> str:
    """UV unwrap an object. Methods: smart_project, cube_project, cylinder_project,
    sphere_project, pack_islands, mark_seam, clear_seam.
    params: method-specific options, e.g. {"angle_limit": 66, "island_margin": 0.02}."""
    r = blender_command("uv_unwrap", {"object": object, "method": method, "params": params or {}},
                        options={"include_diff": True})
    return _mutating_result(r)


@mcp.tool()
def boolean_operation(object: str, cutter: str, operation: str = "DIFFERENCE",
                      solver: str = "FAST", apply: bool = True,
                      hide_cutter: bool = True) -> str:
    """Boolean operation between two mesh objects. Operations: DIFFERENCE, UNION, INTERSECT.
    The cutter object is subtracted from/added to/intersected with the target object."""
    r = blender_command("boolean_operation", {
        "object": object, "cutter": cutter, "operation": operation,
        "solver": solver, "apply": apply, "hide_cutter": hide_cutter,
    }, options={"include_diff": True})
    return _mutating_result(r)


@mcp.tool()
def validate_mesh(name: str) -> str:
    """Validate mesh for 3D printing and game asset readiness.
    Checks manifold geometry, loose verts/edges, flipped normals, volume, surface area."""
    r = blender_command("validate_mesh", {"name": name})
    return json.dumps(r.get("result", r), indent=2)


@mcp.tool()
def get_dimensions(name: str) -> str:
    """Get object dimensions in scene units and millimeters, plus bounding box."""
    r = blender_command("get_dimensions", {"name": name})
    return json.dumps(r.get("result", r), indent=2)


@mcp.tool()
def set_dimensions(name: str, x: float = 0, y: float = 0, z: float = 0,
                   unit: str = "m", uniform: bool = False) -> str:
    """Set object to exact dimensions. Specify x/y/z in the given unit (mm, cm, m, in, ft).
    Pass 0 to leave an axis unchanged. Set uniform=True for proportional scaling."""
    params = {"name": name, "unit": unit, "uniform": uniform}
    if x:
        params["x"] = x
    if y:
        params["y"] = y
    if z:
        params["z"] = z
    r = blender_command("set_dimensions", params, options={"include_diff": True})
    return _mutating_result(r)


@mcp.tool()
def set_scene_units(system: str = "METRIC", length: str = "MILLIMETERS",
                    scale: float = 1.0) -> str:
    """Set scene unit system. system: METRIC/IMPERIAL/NONE.
    length: METERS/CENTIMETERS/MILLIMETERS/FEET/INCHES/etc."""
    r = blender_command("set_scene_units", {
        "system": system, "length": length, "scale": scale,
    }, options={"include_diff": True})
    return _mutating_result(r)


@mcp.tool()
def fix_mesh(object: str, operation: str, params: dict = None) -> str:
    """Fix mesh problems. Operations: recalculate_normals, fill_holes,
    remove_doubles, make_manifold (runs all three).
    params: operation-specific options, e.g. {"distance": 0.001} for remove_doubles."""
    r = blender_command("fix_mesh", {"object": object, "operation": operation, "params": params or {}},
                        options={"include_diff": True})
    return _mutating_result(r)


@mcp.tool()
def load_texture(material: str, filepath: str, input_name: str = "Base Color",
                 name: str = "", non_color: bool = False) -> str:
    """Load an image texture into a material and connect to Principled BSDF.
    input_name: Base Color, Metallic, Roughness, Normal, Emission Color, Alpha, etc.
    Set non_color=True for normal/roughness/metallic maps."""
    params = {"material": material, "filepath": filepath, "input_name": input_name,
              "non_color": non_color}
    if name:
        params["name"] = name
    r = blender_command("load_texture", params, options={"include_diff": True})
    return _mutating_result(r)


@mcp.tool()
def create_pbr_material(name: str, preset: str = "", base_color: list = None,
                        metallic: float = -1, roughness: float = -1,
                        specular: float = -1, emission_color: list = None,
                        emission_strength: float = -1, alpha: float = -1,
                        transmission: float = -1, ior: float = -1) -> str:
    """Create a PBR material with optional preset and value overrides.
    Presets: weathered_metal, worn_plastic, wood, fabric, rubber, concrete, glass.
    base_color/emission_color: [R, G, B, A] with values 0-1."""
    params = {"name": name}
    if preset:
        params["preset"] = preset
    if base_color:
        params["base_color"] = base_color
    if metallic >= 0:
        params["metallic"] = metallic
    if roughness >= 0:
        params["roughness"] = roughness
    if specular >= 0:
        params["specular"] = specular
    if emission_color:
        params["emission_color"] = emission_color
    if emission_strength >= 0:
        params["emission_strength"] = emission_strength
    if alpha >= 0:
        params["alpha"] = alpha
    if transmission >= 0:
        params["transmission"] = transmission
    if ior >= 0:
        params["ior"] = ior
    r = blender_command("create_pbr_material", params, options={"include_diff": True})
    return _mutating_result(r)


# ---------------------------------------------------------------------------
# Export & LOD
# ---------------------------------------------------------------------------

@mcp.tool()
def export_scene(filepath: str, format: str = "GLB", selected_only: bool = False,
                 apply_modifiers: bool = True, preset: str = "") -> str:
    """Export scene to file. Formats: GLB, GLTF, FBX, OBJ, STL.
    Presets: dayz (FBX, Y-up, no anim), godot (GLTF separate), print (STL, scene units).
    selected_only exports only selected objects."""
    params = {"filepath": filepath, "format": format,
              "selected_only": selected_only, "apply_modifiers": apply_modifiers}
    if preset:
        params["preset"] = preset
    r = blender_command("export_scene", params)
    return json.dumps(r.get("result", r), indent=2)


# ---------------------------------------------------------------------------
# High-level asset recipes
# ---------------------------------------------------------------------------

@mcp.tool()
def apply_pbr_material_set(object: str, base_color: str = "", roughness: str = "",
                           metallic: str = "", normal: str = "", height: str = "",
                           texture_directory: str = "", material_name: str = "") -> str:
    """Create and assign one PBR material from texture paths, or auto-detect maps in texture_directory."""
    params = {"object": object}
    for key, value in (("base_color", base_color), ("roughness", roughness),
                       ("metallic", metallic), ("normal", normal), ("height", height),
                       ("texture_directory", texture_directory), ("material_name", material_name)):
        if value:
            params[key] = value
    return _mutating_result(blender_command("apply_pbr_material_set", params, options={"include_diff": True}))


@mcp.tool()
def validate_game_asset(object: str, max_tris: int = -1) -> str:
    """Read-only game-asset validation. Returns a versioned recipe manifest with warnings and errors."""
    params = {"object": object}
    if max_tris >= 0:
        params["max_tris"] = max_tris
    r = blender_command("validate_game_asset", params)
    return json.dumps(r.get("result", r), indent=2)


@mcp.tool()
def export_game_asset(object: str, out_path: str, preset: str = "godot",
                      max_tris: int = -1) -> str:
    """Validate then export exactly one object with preset dayz, godot, or print."""
    params = {"object": object, "out_path": out_path, "preset": preset}
    if max_tris >= 0:
        params["max_tris"] = max_tris
    return _mutating_result(blender_command("export_game_asset", params, options={"include_diff": True}))


@mcp.tool()
def create_preview_sheet(object: str, out_path: str, resolution: int = 256) -> str:
    """Render front, side, top, and three-quarter PNG previews; returns their manifest and SHA-256 values."""
    r = blender_command("create_preview_sheet", {
        "object": object, "out_path": out_path, "resolution": resolution,
    }, options={"include_diff": True})
    return _mutating_result(r)


@mcp.tool()
def generate_lods(object: str, ratios: list = None, collection: str = "",
                  apply: bool = False) -> str:
    """Generate LOD (Level of Detail) copies with decimation.
    ratios: list of floats, e.g. [1.0, 0.5, 0.25, 0.1]. Default: [1.0, 0.5, 0.25, 0.1].
    Creates a collection to organize LODs."""
    params = {"object": object, "apply": apply}
    if ratios:
        params["ratios"] = ratios
    if collection:
        params["collection"] = collection
    r = blender_command("generate_lods", params, options={"include_diff": True})
    return _mutating_result(r)


# ---------------------------------------------------------------------------
# Mirror, Array, Constraints
# ---------------------------------------------------------------------------

@mcp.tool()
def add_mirror(object: str, axis: str = "X", use_bisect: bool = False,
               merge_threshold: float = 0.001, apply: bool = False) -> str:
    """Add a mirror modifier. axis can be 'X', 'Y', 'Z', 'XY', 'XZ', 'YZ', 'XYZ'.
    Set apply=True to apply immediately."""
    r = blender_command("add_mirror", {
        "object": object, "axis": axis, "use_bisect": use_bisect,
        "merge_threshold": merge_threshold, "apply": apply,
    }, options={"include_diff": True})
    return _mutating_result(r)


@mcp.tool()
def array_pattern(object: str, pattern: str = "linear", count: int = 5,
                  offset: list = None, params: dict = None) -> str:
    """Create an array pattern. pattern: 'linear' or 'circular'.
    For linear: offset as [x, y, z] (default [1, 0, 0]).
    For circular: params as {"axis": "Z"}."""
    cmd_params = {"object": object, "pattern": pattern, "count": count}
    if offset:
        cmd_params["offset"] = offset
    if params:
        cmd_params["params"] = params
    r = blender_command("array_pattern", cmd_params, options={"include_diff": True})
    return _mutating_result(r)


@mcp.tool()
def add_constraint(object: str, type: str, name: str = "",
                   target: str = "") -> str:
    """Add an object constraint. Types: TRACK_TO, COPY_LOCATION, COPY_ROTATION,
    COPY_SCALE, LIMIT_ROTATION, DAMPED_TRACK, CHILD_OF, etc."""
    params = {"object": object, "type": type}
    if name:
        params["name"] = name
    if target:
        params["target"] = target
    r = blender_command("add_constraint", params, options={"include_diff": True})
    return _mutating_result(r)


@mcp.tool()
def remove_constraint(object: str, constraint: str) -> str:
    """Remove a constraint from an object."""
    r = blender_command("remove_constraint", {
        "object": object, "constraint": constraint,
    }, options={"include_diff": True})
    return _mutating_result(r)


# ---------------------------------------------------------------------------
# Poly Haven (CC0 Assets)
# ---------------------------------------------------------------------------

@mcp.tool()
def polyhaven_search(asset_type: str = "", categories: str = "",
                     limit: int = 20) -> str:
    """Search Poly Haven for free CC0 assets (HDRIs, textures, 3D models).
    asset_type: 'hdris', 'textures', 'models', or empty for all.
    categories: comma-separated filter. Returns asset IDs for use with polyhaven_download."""
    params = {"limit": limit}
    if asset_type:
        params["asset_type"] = asset_type
    if categories:
        params["categories"] = categories
    r = blender_command("polyhaven_search", params)
    return json.dumps(r.get("result", r), indent=2)


@mcp.tool()
def polyhaven_download(asset_id: str, asset_type: str, resolution: str = "1k",
                       format: str = "", apply_to: str = "",
                       async_mode: bool = False) -> str:
    """Download a Poly Haven asset and import into Blender.
    asset_type: 'hdris' (sets world environment), 'textures' (creates PBR material),
    'models' (imports geometry). resolution: '1k', '2k', '4k'.
    apply_to: object name to assign texture material to (textures only). Set
    async_mode to queue the download and import as a job."""
    params = {
        "asset_id": asset_id,
        "asset_type": asset_type,
        "resolution": resolution,
        "async_mode": async_mode,
    }
    if format:
        params["format"] = format
    if apply_to:
        params["apply_to"] = apply_to
    r = blender_command("polyhaven_download", params, options={"include_diff": True})
    return _mutating_result(r)


# ---------------------------------------------------------------------------
# Texture Queries
# ---------------------------------------------------------------------------

@mcp.tool()
def get_textures() -> str:
    """List all image textures in the scene with metadata (path, size, colorspace, packed status)."""
    r = blender_command("get_textures")
    return json.dumps(r.get("result", r), indent=2)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    mcp.run(transport="stdio")

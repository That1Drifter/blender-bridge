# Blender Bridge — Code Execution

import io
import re
import builtins
from contextlib import redirect_stdout

import bpy
import mathutils
import math

import os
import tempfile
import urllib.request

from .constants import ENGINE_ALIASES, VALID_ENGINES


# ---------------------------------------------------------------------------
# Shared namespace
# ---------------------------------------------------------------------------

def _make_namespace():
    """Pre-built namespace so Claude doesn't need to import common modules."""
    return {
        "bpy": bpy,
        "mathutils": mathutils,
        "math": math,
        "Vector": mathutils.Vector,
        "Euler": mathutils.Euler,
        "Matrix": mathutils.Matrix,
        "Quaternion": mathutils.Quaternion,
        "Color": mathutils.Color,
    }


# ---------------------------------------------------------------------------
# Raw execution mode
# ---------------------------------------------------------------------------

def execute_code(code: str, history_index: int = 0) -> dict:
    """Execute arbitrary Blender Python code (raw mode).

    Pushes an undo step before execution so it can be rolled back.
    Returns dict with 'executed' bool and 'stdout' captured output.
    """
    namespace = _make_namespace()

    bpy.ops.ed.undo_push(message=f"MCP exec #{history_index}")

    capture = io.StringIO()
    with redirect_stdout(capture):
        exec(code, namespace)

    return {
        "executed": True,
        "stdout": capture.getvalue(),
    }


# ---------------------------------------------------------------------------
# Safe execution mode
# ---------------------------------------------------------------------------

BLOCKED_MODULES = frozenset({
    "os", "sys", "subprocess", "shutil", "pathlib", "socket",
    "http", "urllib", "ftplib", "smtplib", "ctypes", "importlib",
    "signal", "multiprocessing", "threading", "tempfile", "glob",
    "webbrowser", "code", "codeop", "compileall",
})

BLOCKED_BUILTINS = frozenset({
    "open", "exec", "eval", "compile", "__import__",
    "getattr", "setattr", "delattr", "globals", "locals",
    "breakpoint", "exit", "quit",
})

# Regex patterns for blocked imports
_IMPORT_PATTERNS = [
    re.compile(r"\bimport\s+([\w.]+)"),
    re.compile(r"\bfrom\s+([\w.]+)\s+import\b"),
]


def _check_imports(code: str):
    """Scan code for blocked imports. Raises on violation."""
    for pattern in _IMPORT_PATTERNS:
        for match in pattern.finditer(code):
            module_name = match.group(1).split(".")[0]
            if module_name in BLOCKED_MODULES:
                raise SandboxViolation(
                    f"Import of '{module_name}' is blocked in safe mode"
                )


def execute_code_safe(code: str, history_index: int = 0) -> dict:
    """Execute Blender Python code with import and builtin restrictions.

    Blocks dangerous modules (os, sys, subprocess, etc.) and
    dangerous builtins (open, eval, __import__, etc.).
    """
    # Static check for blocked imports
    _check_imports(code)

    # Build restricted builtins
    safe_builtins = {
        k: v for k, v in vars(builtins).items()
        if k not in BLOCKED_BUILTINS
    }

    namespace = _make_namespace()
    namespace["__builtins__"] = safe_builtins

    bpy.ops.ed.undo_push(message=f"MCP safe exec #{history_index}")

    capture = io.StringIO()
    with redirect_stdout(capture):
        exec(code, namespace)

    return {
        "executed": True,
        "stdout": capture.getvalue(),
        "mode": "safe",
    }


class SandboxViolation(Exception):
    """Raised when safe mode blocks a dangerous operation."""
    pass


# ---------------------------------------------------------------------------
# Whitelist operations mode
# ---------------------------------------------------------------------------

def execute_operations(operations: list) -> dict:
    """Execute a list of whitelisted operations.

    Each operation is {"op": "operation_name", "args": {kwargs}}.
    Returns per-operation results.
    """
    bpy.ops.ed.undo_push(message="MCP operations batch")

    results = []
    for i, op_def in enumerate(operations):
        op_name = op_def.get("op", "")
        args = op_def.get("args", {})

        handler = OPERATION_REGISTRY.get(op_name)
        if not handler:
            results.append({"op": op_name, "status": "error", "message": f"Unknown operation: {op_name}"})
            continue
        try:
            result = handler(args)
            results.append({"op": op_name, "status": "success", "result": result})
        except Exception as e:
            results.append({"op": op_name, "status": "error", "message": str(e)})

    return {"results": results}


# ---------------------------------------------------------------------------
# Structured manipulation commands
# ---------------------------------------------------------------------------

def _round3(v):
    return [round(x, 3) for x in v]


def _active_object_info():
    """Return basic info about the active object after an operation."""
    obj = bpy.context.active_object
    if not obj:
        return None
    return {
        "name": obj.name,
        "type": obj.type,
        "location": _round3(obj.location),
    }


def add_object(type: str, name: str = None, location: list = None,
               rotation: list = None, scale: list = None, **extra_params) -> dict:
    """Add a primitive object to the scene.

    Extra params like segments, ring_count, vertices, radius, depth, size
    are forwarded to the underlying bpy.ops call.
    """
    bpy.ops.ed.undo_push(message=f"MCP add_object {type}")

    kwargs = {}
    if location:
        kwargs["location"] = tuple(location)
    if rotation:
        kwargs["rotation"] = tuple(rotation)
    if scale:
        kwargs["scale"] = tuple(scale)

    # Forward mesh-specific params
    _mesh_params = {"segments", "ring_count", "vertices", "radius", "depth", "size",
                    "major_radius", "minor_radius", "major_segments", "minor_segments"}
    for k, v in extra_params.items():
        if k in _mesh_params:
            kwargs[k] = v

    type_upper = type.upper()
    ops_map = {
        "CUBE": bpy.ops.mesh.primitive_cube_add,
        "SPHERE": bpy.ops.mesh.primitive_uv_sphere_add,
        "PLANE": bpy.ops.mesh.primitive_plane_add,
        "CYLINDER": bpy.ops.mesh.primitive_cylinder_add,
        "CONE": bpy.ops.mesh.primitive_cone_add,
        "TORUS": bpy.ops.mesh.primitive_torus_add,
        "CIRCLE": bpy.ops.mesh.primitive_circle_add,
        "MONKEY": bpy.ops.mesh.primitive_monkey_add,
        "EMPTY": bpy.ops.object.empty_add,
        "CAMERA": bpy.ops.object.camera_add,
        "LIGHT_POINT": lambda **kw: bpy.ops.object.light_add(type="POINT", **kw),
        "LIGHT_SUN": lambda **kw: bpy.ops.object.light_add(type="SUN", **kw),
        "LIGHT_SPOT": lambda **kw: bpy.ops.object.light_add(type="SPOT", **kw),
        "LIGHT_AREA": lambda **kw: bpy.ops.object.light_add(type="AREA", **kw),
    }

    op = ops_map.get(type_upper)
    if not op:
        raise ValueError(f"Unknown object type: {type}. Available: {sorted(ops_map.keys())}")

    op(**kwargs)

    if name and bpy.context.active_object:
        bpy.context.active_object.name = name

    return _active_object_info()


def delete_object(name: str) -> dict:
    """Delete an object by name."""
    obj = bpy.data.objects.get(name)
    if not obj:
        raise KeyError(f"Object '{name}' not found")

    bpy.ops.ed.undo_push(message=f"MCP delete_object {name}")
    bpy.data.objects.remove(obj, do_unlink=True)
    return {"deleted": name}


def set_transform(name: str, location: list = None,
                  rotation: list = None, scale: list = None) -> dict:
    """Set an object's transform properties."""
    obj = bpy.data.objects.get(name)
    if not obj:
        raise KeyError(f"Object '{name}' not found")

    bpy.ops.ed.undo_push(message=f"MCP set_transform {name}")

    if location is not None:
        obj.location = tuple(location)
    if rotation is not None:
        obj.rotation_euler = tuple(rotation)
    if scale is not None:
        obj.scale = tuple(scale)

    return {
        "name": name,
        "location": _round3(obj.location),
        "rotation_euler": _round3(obj.rotation_euler),
        "scale": _round3(obj.scale),
    }


def set_parent(child: str, parent: str = None) -> dict:
    """Set or clear an object's parent."""
    child_obj = bpy.data.objects.get(child)
    if not child_obj:
        raise KeyError(f"Object '{child}' not found")

    bpy.ops.ed.undo_push(message=f"MCP set_parent {child}")

    if parent:
        parent_obj = bpy.data.objects.get(parent)
        if not parent_obj:
            raise KeyError(f"Parent object '{parent}' not found")
        child_obj.parent = parent_obj
    else:
        child_obj.parent = None

    return {"child": child, "parent": parent}


def duplicate_object(name: str, new_name: str = None, linked: bool = False) -> dict:
    """Duplicate an object."""
    obj = bpy.data.objects.get(name)
    if not obj:
        raise KeyError(f"Object '{name}' not found")

    bpy.ops.ed.undo_push(message=f"MCP duplicate_object {name}")

    if linked:
        new_obj = obj.copy()
    else:
        new_obj = obj.copy()
        if obj.data:
            new_obj.data = obj.data.copy()

    if new_name:
        new_obj.name = new_name

    bpy.context.collection.objects.link(new_obj)

    return {
        "name": new_obj.name,
        "type": new_obj.type,
        "location": _round3(new_obj.location),
    }


def set_visibility(name: str, visible: bool = None, render_visible: bool = None) -> dict:
    """Set object visibility in viewport and/or render."""
    obj = bpy.data.objects.get(name)
    if not obj:
        raise KeyError(f"Object '{name}' not found")

    bpy.ops.ed.undo_push(message=f"MCP set_visibility {name}")

    if visible is not None:
        obj.hide_viewport = not visible
    if render_visible is not None:
        obj.hide_render = not render_visible

    return {
        "name": name,
        "visible": not obj.hide_viewport,
        "render_visible": not obj.hide_render,
    }


def create_material(name: str, base_color: list = None,
                    metallic: float = None, roughness: float = None) -> dict:
    """Create a new material with optional Principled BSDF settings."""
    bpy.ops.ed.undo_push(message=f"MCP create_material {name}")

    mat = bpy.data.materials.new(name=name)
    mat.use_nodes = True

    # Find or expect Principled BSDF
    principled = None
    for node in mat.node_tree.nodes:
        if node.type == "BSDF_PRINCIPLED":
            principled = node
            break

    if principled:
        if base_color is not None:
            principled.inputs["Base Color"].default_value = tuple(base_color)
        if metallic is not None:
            principled.inputs["Metallic"].default_value = metallic
        if roughness is not None:
            principled.inputs["Roughness"].default_value = roughness

    return {"name": mat.name}


def set_material(object: str, material: str, slot: int = 0) -> dict:
    """Assign a material to an object's material slot."""
    obj = bpy.data.objects.get(object)
    if not obj:
        raise KeyError(f"Object '{object}' not found")
    mat = bpy.data.materials.get(material)
    if not mat:
        raise KeyError(f"Material '{material}' not found")

    bpy.ops.ed.undo_push(message=f"MCP set_material {object}")

    # Ensure enough material slots
    while len(obj.material_slots) <= slot:
        obj.data.materials.append(None)

    obj.material_slots[slot].material = mat
    return {"object": object, "material": material, "slot": slot}


def add_modifier(object: str, type: str, name: str = None, **params) -> dict:
    """Add a modifier to an object."""
    obj = bpy.data.objects.get(object)
    if not obj:
        raise KeyError(f"Object '{object}' not found")

    bpy.ops.ed.undo_push(message=f"MCP add_modifier {object} {type}")

    mod = obj.modifiers.new(name=name or type, type=type.upper())

    # Apply common params
    for k, v in params.items():
        if hasattr(mod, k):
            setattr(mod, k, v)

    return {"object": object, "modifier": mod.name, "type": mod.type}


def remove_modifier(object: str, modifier: str) -> dict:
    """Remove a modifier from an object."""
    obj = bpy.data.objects.get(object)
    if not obj:
        raise KeyError(f"Object '{object}' not found")

    mod = obj.modifiers.get(modifier)
    if not mod:
        raise KeyError(f"Modifier '{modifier}' not found on '{object}'")

    bpy.ops.ed.undo_push(message=f"MCP remove_modifier {object} {modifier}")
    obj.modifiers.remove(mod)
    return {"object": object, "removed": modifier}


def set_render_settings(engine: str = None, resolution_x: int = None,
                        resolution_y: int = None, samples: int = None,
                        film_transparent: bool = None) -> dict:
    """Update render settings."""
    bpy.ops.ed.undo_push(message="MCP set_render_settings")
    scene = bpy.context.scene
    render = scene.render

    if engine is not None:
        eng = ENGINE_ALIASES.get(engine.upper(), engine.upper())
        if eng not in VALID_ENGINES:
            raise ValueError(
                f"Unknown engine '{engine}'. Valid engines: EEVEE, CYCLES, WORKBENCH "
                f"(or full names: {sorted(VALID_ENGINES)})"
            )
        render.engine = eng
    if resolution_x is not None:
        render.resolution_x = resolution_x
    if resolution_y is not None:
        render.resolution_y = resolution_y
    if film_transparent is not None:
        render.film_transparent = film_transparent
    if samples is not None:
        if render.engine == "CYCLES" and hasattr(scene, "cycles"):
            scene.cycles.samples = samples
        elif hasattr(scene, "eevee"):
            scene.eevee.taa_render_samples = samples

    return {
        "engine": render.engine,
        "resolution_x": render.resolution_x,
        "resolution_y": render.resolution_y,
    }


def frame_camera(target: str = None, targets: list = None, camera: str = None,
                 padding: float = 1.2, exclude: list = None,
                 types: list = None,
                 preserve_direction: bool = False) -> dict:
    """Aim the camera to frame the scene or specific object(s).

    Args:
        target: Single object name to frame.
        targets: List of object names to frame (alternative to target).
        camera: Camera name. None = scene active camera.
        padding: Distance multiplier (1.0 = tight, 1.5 = loose).
        exclude: List of object names to exclude when framing all.
        types: Object types to include. Default: MESH, CURVE, SURFACE, FONT.
        preserve_direction: If True, keep the camera's current viewing angle.
                            If False (default), use a fresh 3/4 view.
    """
    bpy.ops.ed.undo_push(message="MCP frame_camera")

    # Get camera
    cam_obj = bpy.data.objects.get(camera) if camera else bpy.context.scene.camera
    if not cam_obj or cam_obj.type != "CAMERA":
        raise ValueError(f"No valid camera found (specified: {camera})")

    allowed_types = set(types) if types else {"MESH", "CURVE", "SURFACE", "FONT"}
    exclude_names = set(exclude) if exclude else set()

    # Collect target objects
    if targets:
        objects = []
        for name in targets:
            obj = bpy.data.objects.get(name)
            if not obj:
                raise KeyError(f"Object '{name}' not found")
            objects.append(obj)
    elif target:
        obj = bpy.data.objects.get(target)
        if not obj:
            raise KeyError(f"Object '{target}' not found")
        objects = [obj]
    else:
        objects = [o for o in bpy.context.scene.objects
                   if o.visible_get() and o.type in allowed_types
                   and o.name not in exclude_names]
        if not objects:
            raise ValueError("No visible objects matching criteria in scene")

    # Compute combined world-space AABB
    all_corners = []
    for obj in objects:
        if hasattr(obj, "bound_box") and obj.bound_box:
            for corner in obj.bound_box:
                all_corners.append(obj.matrix_world @ mathutils.Vector(corner))

    if not all_corners:
        raise ValueError("Could not compute bounding box for target objects")

    xs = [c.x for c in all_corners]
    ys = [c.y for c in all_corners]
    zs = [c.z for c in all_corners]

    center = mathutils.Vector((
        (min(xs) + max(xs)) / 2,
        (min(ys) + max(ys)) / 2,
        (min(zs) + max(zs)) / 2,
    ))

    # Bounding sphere radius
    radius = max((c - center).length for c in all_corners)
    if radius < 0.001:
        radius = 1.0  # Fallback for points/empties

    # Compute required distance from FOV — use the tighter dimension
    fov = cam_obj.data.angle  # horizontal FOV in radians
    aspect = bpy.context.scene.render.resolution_x / max(1, bpy.context.scene.render.resolution_y)
    vertical_fov = 2 * math.atan(math.tan(fov / 2) / max(aspect, 0.001))
    effective_fov = min(fov, vertical_fov)

    distance = radius * padding / math.tan(effective_fov / 2)

    # Position camera
    if preserve_direction:
        cam_to_center = cam_obj.location - center
        if cam_to_center.length < 0.001:
            direction = mathutils.Vector((1.0, -1.0, 0.8)).normalized()
        else:
            direction = cam_to_center.normalized()
    else:
        # Fresh 3/4 view: 30° elevation, 45° azimuth
        elev = math.radians(30)
        azim = math.radians(45)
        direction = mathutils.Vector((
            math.cos(elev) * math.cos(azim),
            -math.cos(elev) * math.sin(azim),
            math.sin(elev),
        )).normalized()

    cam_obj.location = center + direction * distance

    # Aim camera at center
    look_dir = center - cam_obj.location
    rot_quat = look_dir.to_track_quat("-Z", "Y")
    cam_obj.rotation_euler = rot_quat.to_euler()

    return {
        "camera": cam_obj.name,
        "location": _round3(cam_obj.location),
        "target_center": _round3(center),
        "distance": round(distance, 3),
    }


def apply_modifier(object: str, modifier: str) -> dict:
    """Apply (bake) a modifier, making its effect permanent on the mesh."""
    obj = bpy.data.objects.get(object)
    if not obj:
        raise KeyError(f"Object '{object}' not found")
    if obj.type != "MESH":
        raise ValueError(f"Object '{object}' is {obj.type}, not MESH — cannot apply modifier")
    mod = obj.modifiers.get(modifier)
    if not mod:
        raise KeyError(f"Modifier '{modifier}' not found on '{object}'")

    bpy.ops.ed.undo_push(message=f"MCP apply_modifier {object} {modifier}")

    # Must be in object mode with the target as active object
    if bpy.context.active_object and bpy.context.active_object.mode != "OBJECT":
        bpy.ops.object.mode_set(mode="OBJECT")
    bpy.context.view_layer.objects.active = obj
    obj.select_set(True)

    result = bpy.ops.object.modifier_apply(modifier=modifier)
    if result != {"FINISHED"}:
        raise RuntimeError(f"modifier_apply failed for '{modifier}' on '{object}'")

    return {"object": object, "applied": modifier}


def join_objects(objects: list, target: str = None) -> dict:
    """Merge multiple objects into one.

    Args:
        objects: List of object names to join.
        target: Name of the object that receives the others. Defaults to first in list.
    """
    if not objects or len(objects) < 2:
        raise ValueError("Need at least 2 object names to join")

    target_name = target or objects[0]
    target_obj = bpy.data.objects.get(target_name)
    if not target_obj:
        raise KeyError(f"Target object '{target_name}' not found")

    # Validate all objects exist and are meshes
    for name in objects:
        obj = bpy.data.objects.get(name)
        if not obj:
            raise KeyError(f"Object '{name}' not found")
        if obj.type != "MESH":
            raise ValueError(f"Object '{name}' is {obj.type}, not MESH — cannot join non-mesh objects")

    bpy.ops.ed.undo_push(message=f"MCP join_objects -> {target_name}")

    # Ensure object mode
    if bpy.context.active_object and bpy.context.active_object.mode != "OBJECT":
        bpy.ops.object.mode_set(mode="OBJECT")

    # Deselect all, then select targets
    bpy.ops.object.select_all(action="DESELECT")
    for name in objects:
        obj = bpy.data.objects.get(name)
        obj.select_set(True)

    bpy.context.view_layer.objects.active = target_obj
    bpy.ops.object.join()

    return {
        "result": target_obj.name,
        "joined_count": len(objects),
    }


def export_scene(filepath: str, format: str = "GLB",
                  selected_only: bool = False, apply_modifiers: bool = True,
                  preset: str = None) -> dict:
    """Export the scene to a file.

    Args:
        filepath: Output file path.
        format: GLB, GLTF, FBX, OBJ, or STL.
        selected_only: If True, export only selected objects.
        apply_modifiers: If True, apply modifiers before export.
        preset: Optional preset: dayz (FBX), godot (GLTF), print (STL).
    """
    # Preset overrides
    if preset:
        p = preset.lower()
        if p == "dayz":
            format = "FBX"
        elif p == "godot":
            format = "GLTF"
        elif p == "print":
            format = "STL"
        else:
            raise ValueError(f"Unknown preset '{preset}'. Valid: dayz, godot, print")

    fmt = format.upper()

    # If selected_only, ensure selection context
    if selected_only:
        if bpy.context.active_object and bpy.context.active_object.mode != "OBJECT":
            bpy.ops.object.mode_set(mode="OBJECT")

    def _export_gltf(fp, gltf_format):
        bpy.ops.export_scene.gltf(
            filepath=fp,
            export_format=gltf_format,
            use_selection=selected_only,
            export_apply=apply_modifiers,
        )

    def _export_fbx(fp):
        kwargs = {
            "filepath": fp,
            "use_selection": selected_only,
            "use_mesh_modifiers": apply_modifiers,
        }
        if preset and preset.lower() == "dayz":
            kwargs.update({
                "global_scale": 1.0,
                "apply_unit_scale": True,
                "apply_scale_options": "FBX_SCALE_ALL",
                "axis_forward": "-Z",
                "axis_up": "Y",
                "bake_anim": False,
            })
        bpy.ops.export_scene.fbx(**kwargs)

    def _export_obj(fp):
        kwargs = {"filepath": fp}
        if selected_only:
            kwargs["export_selected_objects"] = True
        bpy.ops.wm.obj_export(**kwargs)

    def _export_stl(fp):
        kwargs = {"filepath": fp}
        if selected_only:
            kwargs["export_selected_objects"] = True
        if preset and preset.lower() == "print":
            kwargs["global_scale"] = 1.0
            kwargs["use_scene_unit"] = True
            kwargs["ascii"] = False
        bpy.ops.wm.stl_export(**kwargs)

    exporters = {
        "GLB": lambda fp: _export_gltf(fp, "GLB"),
        "GLTF": lambda fp: _export_gltf(fp, "GLTF_SEPARATE"),
        "FBX": _export_fbx,
        "OBJ": _export_obj,
        "STL": _export_stl,
    }

    exporter = exporters.get(fmt)
    if not exporter:
        raise ValueError(f"Unknown export format: {format}. Valid: {sorted(exporters.keys())}")

    exporter(filepath)
    return {
        "filepath": filepath,
        "format": fmt,
        "preset": preset,
        "selected_only": selected_only,
    }


# ---------------------------------------------------------------------------
# LOD generation
# ---------------------------------------------------------------------------

def generate_lods(object: str, ratios: list = None, collection: str = None,
                  apply: bool = False) -> dict:
    """Generate LOD (Level of Detail) copies with decimation.

    Args:
        object: Source object name (becomes LOD0).
        ratios: List of decimation ratios, e.g. [1.0, 0.5, 0.25, 0.1].
                Defaults to [1.0, 0.5, 0.25, 0.1].
        collection: Collection name to organize LODs. Defaults to '{object}_LODs'.
        apply: If True, apply the decimate modifiers.
    """
    obj = bpy.data.objects.get(object)
    if not obj:
        raise KeyError(f"Object '{object}' not found")
    if obj.type != "MESH":
        raise ValueError(f"Object '{object}' is {obj.type}, not MESH")

    ratios = ratios or [1.0, 0.5, 0.25, 0.1]
    bpy.ops.ed.undo_push(message=f"MCP generate_lods {object}")

    # Create LOD collection
    col_name = collection or f"{object}_LODs"
    lod_col = bpy.data.collections.get(col_name)
    if not lod_col:
        lod_col = bpy.data.collections.new(col_name)
        bpy.context.scene.collection.children.link(lod_col)

    lods = []
    base_verts = len(obj.data.vertices)

    for i, ratio in enumerate(ratios):
        lod_name = f"{object}_LOD{i}"

        if i == 0 and ratio >= 1.0:
            # LOD0 is the original — just link to collection
            if obj.name not in [o.name for o in lod_col.objects]:
                lod_col.objects.link(obj)
            lods.append({
                "name": obj.name,
                "lod_level": i,
                "ratio": ratio,
                "vertices": base_verts,
            })
            continue

        # Duplicate object and mesh data
        new_mesh = obj.data.copy()
        new_obj = obj.data.id_data.copy() if i == 0 else bpy.data.objects.new(lod_name, new_mesh)
        if i > 0:
            # Copy transforms
            new_obj.location = obj.location.copy()
            new_obj.rotation_euler = obj.rotation_euler.copy()
            new_obj.scale = obj.scale.copy()
        new_obj.name = lod_name
        new_obj.data = new_mesh

        # Link to LOD collection
        lod_col.objects.link(new_obj)

        # Add decimate modifier
        if ratio < 1.0:
            mod = new_obj.modifiers.new(name=f"Decimate_LOD{i}", type="DECIMATE")
            mod.ratio = ratio

            if apply:
                bpy.context.view_layer.objects.active = new_obj
                new_obj.select_set(True)
                bpy.ops.object.modifier_apply(modifier=mod.name)

        vert_count = len(new_obj.data.vertices)
        lods.append({
            "name": new_obj.name,
            "lod_level": i,
            "ratio": ratio,
            "vertices": vert_count,
        })

    return {
        "source": object,
        "collection": col_name,
        "lod_count": len(lods),
        "lods": lods,
    }


def set_world(color: list = None, strength: float = None, hdri_path: str = None) -> dict:
    """Set world/environment settings.

    Args:
        color: Background color [R, G, B, A] (0-1).
        strength: Background strength.
        hdri_path: Path to an HDRI image file for environment lighting.
    """
    bpy.ops.ed.undo_push(message="MCP set_world")

    world = bpy.context.scene.world
    if not world:
        world = bpy.data.worlds.new("World")
        bpy.context.scene.world = world

    world.use_nodes = True
    tree = world.node_tree

    # Find Background node
    bg_node = None
    for node in tree.nodes:
        if node.type == "BACKGROUND":
            bg_node = node
            break
    if not bg_node:
        bg_node = tree.nodes.new("ShaderNodeBackground")

    if color is not None:
        bg_node.inputs["Color"].default_value = tuple(color)
    if strength is not None:
        bg_node.inputs["Strength"].default_value = strength

    result = {"world": world.name}

    if hdri_path:
        # Remove existing links to Background Color input
        for link in list(tree.links):
            if link.to_node == bg_node and link.to_socket.name == "Color":
                tree.links.remove(link)

        # Add or find environment texture node
        env_node = None
        for node in tree.nodes:
            if node.type == "TEX_ENVIRONMENT":
                env_node = node
                break
        if not env_node:
            env_node = tree.nodes.new("ShaderNodeTexEnvironment")
            env_node.location = (-300, 300)

        env_node.image = bpy.data.images.load(hdri_path)

        # Connect to Background Color input
        tree.links.new(env_node.outputs["Color"], bg_node.inputs["Color"])
        result["hdri"] = hdri_path

    return result


def set_light(name: str, energy: float = None, color: list = None,
              type: str = None, spot_size: float = None, spot_blend: float = None,
              size: float = None, shadow_soft_size: float = None) -> dict:
    """Configure a light object's properties."""
    obj = bpy.data.objects.get(name)
    if not obj:
        raise KeyError(f"Object '{name}' not found")
    if obj.type != "LIGHT":
        raise ValueError(f"Object '{name}' is {obj.type}, not LIGHT")

    bpy.ops.ed.undo_push(message=f"MCP set_light {name}")
    light = obj.data

    if type is not None:
        valid_types = {"POINT", "SUN", "SPOT", "AREA"}
        t = type.upper()
        if t not in valid_types:
            raise ValueError(f"Invalid light type '{type}'. Valid: {sorted(valid_types)}")
        light.type = t
        light = obj.data  # re-fetch — Blender recreates data on type change
    if energy is not None:
        light.energy = energy
    if color is not None:
        light.color = tuple(color[:3])
    if spot_size is not None and light.type == "SPOT":
        light.spot_size = spot_size
    if spot_blend is not None and light.type == "SPOT":
        light.spot_blend = spot_blend
    if size is not None and light.type == "AREA":
        light.size = size
    if shadow_soft_size is not None and light.type in ("POINT", "SUN"):
        light.shadow_soft_size = shadow_soft_size

    result = {
        "name": name,
        "type": light.type,
        "energy": light.energy,
        "color": [round(c, 3) for c in light.color],
    }
    if light.type == "SPOT":
        result["spot_size"] = round(light.spot_size, 3)
        result["spot_blend"] = round(light.spot_blend, 3)
    if light.type == "AREA":
        result["size"] = round(light.size, 3)
    return result


def set_camera(name: str = None, lens: float = None, sensor_width: float = None,
               clip_start: float = None, clip_end: float = None,
               dof_enabled: bool = None, dof_focus_distance: float = None,
               dof_aperture: float = None) -> dict:
    """Configure camera properties."""
    cam_obj = bpy.data.objects.get(name) if name else bpy.context.scene.camera
    if not cam_obj or cam_obj.type != "CAMERA":
        raise ValueError(f"No valid camera found (specified: {name})")

    bpy.ops.ed.undo_push(message=f"MCP set_camera {cam_obj.name}")
    cam = cam_obj.data

    if lens is not None:
        cam.lens = lens
    if sensor_width is not None:
        cam.sensor_width = sensor_width
    if clip_start is not None:
        cam.clip_start = clip_start
    if clip_end is not None:
        cam.clip_end = clip_end
    if dof_enabled is not None:
        cam.dof.use_dof = dof_enabled
    if dof_focus_distance is not None:
        cam.dof.focus_distance = dof_focus_distance
    if dof_aperture is not None:
        cam.dof.aperture_fstop = dof_aperture

    return {
        "name": cam_obj.name,
        "lens": round(cam.lens, 2),
        "sensor_width": round(cam.sensor_width, 2),
        "clip_start": round(cam.clip_start, 3),
        "clip_end": round(cam.clip_end, 1),
        "dof_enabled": cam.dof.use_dof,
        "dof_focus_distance": round(cam.dof.focus_distance, 3),
        "dof_aperture": round(cam.dof.aperture_fstop, 2),
    }


def select_objects(names: list, active: str = None, deselect_first: bool = True) -> dict:
    """Select objects by name."""
    bpy.ops.ed.undo_push(message="MCP select_objects")

    if deselect_first:
        bpy.ops.object.select_all(action="DESELECT")

    selected = []
    for name in names:
        obj = bpy.data.objects.get(name)
        if not obj:
            raise KeyError(f"Object '{name}' not found")
        obj.select_set(True)
        selected.append(name)

    active_name = active or (names[0] if names else None)
    if active_name:
        obj = bpy.data.objects.get(active_name)
        if obj:
            bpy.context.view_layer.objects.active = obj

    return {
        "selected": selected,
        "active": bpy.context.active_object.name if bpy.context.active_object else None,
    }


def set_keyframe(name: str, frame: int, property: str = "location",
                 value: list = None) -> dict:
    """Insert a keyframe on an object property at the given frame.

    Args:
        name: Object name.
        frame: Frame number.
        property: Data path — "location", "rotation_euler", "scale", or custom path.
        value: If provided, set the property to this value before keying.
    """
    obj = bpy.data.objects.get(name)
    if not obj:
        raise KeyError(f"Object '{name}' not found")

    bpy.ops.ed.undo_push(message=f"MCP set_keyframe {name} f{frame}")
    bpy.context.scene.frame_set(frame)

    if value is not None:
        if property == "location":
            obj.location = tuple(value)
        elif property == "rotation_euler":
            obj.rotation_euler = tuple(value)
        elif property == "scale":
            obj.scale = tuple(value)
        else:
            setattr(obj, property, tuple(value) if isinstance(value, list) else value)

    obj.keyframe_insert(data_path=property, frame=frame)

    return {
        "object": name,
        "frame": frame,
        "property": property,
    }


def create_mesh(name: str, vertices: list, edges: list = None,
                faces: list = None, location: list = None) -> dict:
    """Create a mesh object from raw vertex/edge/face data.

    Args:
        name: Object name.
        vertices: List of [x, y, z] vertex coordinates.
        edges: List of [v1_idx, v2_idx] edge pairs (optional).
        faces: List of [v1, v2, v3, ...] face index lists (optional).
        location: Object location [x, y, z].
    """
    bpy.ops.ed.undo_push(message=f"MCP create_mesh {name}")

    verts = [tuple(v) for v in vertices]
    e = [tuple(edge) for edge in (edges or [])]
    f = [tuple(face) for face in (faces or [])]

    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata(verts, e, f)
    mesh.update(calc_edges=True)
    mesh.calc_normals()

    obj = bpy.data.objects.new(name, mesh)
    bpy.context.collection.objects.link(obj)

    if location:
        obj.location = tuple(location)

    return {
        "name": obj.name,
        "vertices": len(mesh.vertices),
        "edges": len(mesh.edges),
        "polygons": len(mesh.polygons),
        "location": _round3(obj.location),
    }


def batch_transform(transforms: list) -> dict:
    """Set transforms on multiple objects in one call.

    Args:
        transforms: List of {"name": str, "location": [...], "rotation": [...], "scale": [...]}.
    """
    # Pre-validate all objects exist before modifying anything
    for t in transforms:
        name = t.get("name")
        if not bpy.data.objects.get(name):
            raise KeyError(f"Object '{name}' not found (pre-validation)")

    bpy.ops.ed.undo_push(message="MCP batch_transform")

    results = []
    for t in transforms:
        name = t.get("name")
        obj = bpy.data.objects.get(name)
        if "location" in t:
            obj.location = tuple(t["location"])
        if "rotation" in t:
            obj.rotation_euler = tuple(t["rotation"])
        if "scale" in t:
            obj.scale = tuple(t["scale"])
        results.append({
            "name": name,
            "location": _round3(obj.location),
            "rotation_euler": _round3(obj.rotation_euler),
            "scale": _round3(obj.scale),
        })

    return {"results": results}


# ---------------------------------------------------------------------------
# Shader node tools
# ---------------------------------------------------------------------------

def add_shader_node(material: str, node_type: str, name: str = None,
                    location: list = None, properties: dict = None) -> dict:
    """Add a node to a material's shader node tree.

    Args:
        material: Material name.
        node_type: Blender node type identifier (e.g. ShaderNodeTexImage,
                   ShaderNodeTexNoise, ShaderNodeValToRGB, ShaderNodeBump, etc.)
        name: Optional node name.
        location: [x, y] position in the node editor.
        properties: Dict of input values or node settings to apply.
    """
    mat = bpy.data.materials.get(material)
    if not mat:
        raise KeyError(f"Material '{material}' not found")
    if not mat.use_nodes:
        mat.use_nodes = True

    bpy.ops.ed.undo_push(message=f"MCP add_shader_node {node_type}")

    tree = mat.node_tree
    node = tree.nodes.new(node_type)
    if name:
        node.name = name
        node.label = name
    if location:
        node.location = tuple(location)

    # Apply properties to inputs
    if properties:
        for key, value in properties.items():
            # Try as node input first
            inp = node.inputs.get(key)
            if inp:
                if isinstance(value, list):
                    inp.default_value = tuple(value) if len(value) <= 4 else value
                else:
                    inp.default_value = value
            elif hasattr(node, key):
                setattr(node, key, value)

    # Build response with available sockets
    inputs = {inp.name: inp.type for inp in node.inputs}
    outputs = {out.name: out.type for out in node.outputs}

    return {
        "node": node.name,
        "type": node.bl_idname,
        "inputs": inputs,
        "outputs": outputs,
    }


def connect_shader_nodes(material: str, from_node: str, from_output: str,
                         to_node: str, to_input: str) -> dict:
    """Connect two nodes in a material's shader tree."""
    mat = bpy.data.materials.get(material)
    if not mat:
        raise KeyError(f"Material '{material}' not found")

    bpy.ops.ed.undo_push(message=f"MCP connect_shader_nodes")

    tree = mat.node_tree
    src = tree.nodes.get(from_node)
    dst = tree.nodes.get(to_node)
    if not src:
        raise KeyError(f"Node '{from_node}' not found in material '{material}'")
    if not dst:
        raise KeyError(f"Node '{to_node}' not found in material '{material}'")

    src_socket = src.outputs.get(from_output)
    dst_socket = dst.inputs.get(to_input)
    if not src_socket:
        available = [o.name for o in src.outputs]
        raise KeyError(f"Output '{from_output}' not found on '{from_node}'. Available: {available}")
    if not dst_socket:
        available = [i.name for i in dst.inputs]
        raise KeyError(f"Input '{to_input}' not found on '{to_node}'. Available: {available}")

    tree.links.new(src_socket, dst_socket)

    return {
        "from": f"{from_node}.{from_output}",
        "to": f"{to_node}.{to_input}",
    }


def remove_shader_node(material: str, node_name: str) -> dict:
    """Remove a node from a material's shader tree."""
    mat = bpy.data.materials.get(material)
    if not mat:
        raise KeyError(f"Material '{material}' not found")

    tree = mat.node_tree
    node = tree.nodes.get(node_name)
    if not node:
        raise KeyError(f"Node '{node_name}' not found in material '{material}'")

    bpy.ops.ed.undo_push(message=f"MCP remove_shader_node {node_name}")
    tree.nodes.remove(node)

    return {"removed": node_name, "material": material}


# ---------------------------------------------------------------------------
# Collection management
# ---------------------------------------------------------------------------

def create_collection(name: str, parent: str = None) -> dict:
    """Create a new collection, optionally under a parent collection."""
    bpy.ops.ed.undo_push(message=f"MCP create_collection {name}")

    col = bpy.data.collections.new(name)

    if parent:
        parent_col = bpy.data.collections.get(parent)
        if not parent_col:
            raise KeyError(f"Parent collection '{parent}' not found")
        parent_col.children.link(col)
    else:
        bpy.context.scene.collection.children.link(col)

    return {"name": col.name, "parent": parent}


def move_to_collection(object: str, collection: str) -> dict:
    """Move an object to a collection (removes from current collections)."""
    obj = bpy.data.objects.get(object)
    if not obj:
        raise KeyError(f"Object '{object}' not found")
    col = bpy.data.collections.get(collection)
    if not col:
        raise KeyError(f"Collection '{collection}' not found")

    bpy.ops.ed.undo_push(message=f"MCP move_to_collection {object}")

    # Remove from all current collections
    for c in list(obj.users_collection):
        c.objects.unlink(obj)

    col.objects.link(obj)

    return {"object": object, "collection": collection}


def set_collection_visibility(collection: str, visible: bool = None,
                              render_visible: bool = None) -> dict:
    """Set collection visibility in viewport and/or render."""
    col = bpy.data.collections.get(collection)
    if not col:
        raise KeyError(f"Collection '{collection}' not found")

    bpy.ops.ed.undo_push(message=f"MCP set_collection_visibility {collection}")

    if visible is not None:
        col.hide_viewport = not visible
    if render_visible is not None:
        col.hide_render = not render_visible

    return {
        "collection": collection,
        "visible": not col.hide_viewport,
        "render_visible": not col.hide_render,
    }


# ---------------------------------------------------------------------------
# Mesh edit mode operations
# ---------------------------------------------------------------------------

def edit_mesh(object: str, operation: str, params: dict = None) -> dict:
    """Perform a mesh editing operation.

    Handles mode switching automatically.

    Args:
        object: Object name.
        operation: One of: subdivide, bevel, inset, extrude, merge, separate.
        params: Operation-specific parameters as dict.
    """
    obj = bpy.data.objects.get(object)
    if not obj:
        raise KeyError(f"Object '{object}' not found")
    if obj.type != "MESH":
        raise ValueError(f"Object '{object}' is {obj.type}, not MESH")

    params = params or {}
    bpy.ops.ed.undo_push(message=f"MCP edit_mesh {object} {operation}")

    # Ensure object mode first, set active
    if bpy.context.active_object and bpy.context.active_object.mode != "OBJECT":
        bpy.ops.object.mode_set(mode="OBJECT")
    bpy.ops.object.select_all(action="DESELECT")
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj

    # Enter edit mode and select all
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.select_all(action="SELECT")

    op = operation.lower()

    if op == "subdivide":
        cuts = params.get("cuts", 1)
        bpy.ops.mesh.subdivide(number_cuts=cuts)
        result_info = {"cuts": cuts}

    elif op == "bevel":
        width = params.get("width", 0.1)
        segments = params.get("segments", 1)
        bpy.ops.mesh.bevel(offset=width, segments=segments, affect='EDGES')
        result_info = {"width": width, "segments": segments}

    elif op == "inset":
        thickness = params.get("thickness", 0.1)
        bpy.ops.mesh.inset(thickness=thickness, use_boundary=True)
        result_info = {"thickness": thickness}

    elif op == "extrude":
        value = params.get("value", 0.5)
        # Extrude along face normals
        bpy.ops.mesh.extrude_region_move(
            TRANSFORM_OT_translate={"value": (0, 0, value)}
        )
        result_info = {"value": value}

    elif op == "merge":
        distance = params.get("distance", 0.001)
        bpy.ops.mesh.remove_doubles(threshold=distance)
        result_info = {"distance": distance}

    elif op == "separate":
        sep_type = params.get("type", "LOOSE")
        bpy.ops.mesh.separate(type=sep_type)
        result_info = {"type": sep_type}

    else:
        bpy.ops.object.mode_set(mode="OBJECT")
        raise ValueError(
            f"Unknown mesh operation '{operation}'. "
            f"Valid: subdivide, bevel, inset, extrude, merge, separate"
        )

    # Return to object mode
    bpy.ops.object.mode_set(mode="OBJECT")

    mesh = obj.data
    return {
        "object": object,
        "operation": operation,
        "params": result_info,
        "mesh_stats": {
            "vertices": len(mesh.vertices),
            "edges": len(mesh.edges),
            "polygons": len(mesh.polygons),
        },
    }


# ---------------------------------------------------------------------------
# Scene controls
# ---------------------------------------------------------------------------

def set_scene(fps: int = None, frame_start: int = None, frame_end: int = None,
              frame_current: int = None, active_camera: str = None) -> dict:
    """Configure scene-level settings."""
    bpy.ops.ed.undo_push(message="MCP set_scene")
    scene = bpy.context.scene

    if fps is not None:
        scene.render.fps = fps
    if frame_start is not None:
        scene.frame_start = frame_start
    if frame_end is not None:
        scene.frame_end = frame_end
    if frame_current is not None:
        scene.frame_set(frame_current)
    if active_camera is not None:
        cam = bpy.data.objects.get(active_camera)
        if not cam or cam.type != "CAMERA":
            raise ValueError(f"'{active_camera}' is not a valid camera")
        scene.camera = cam

    return {
        "fps": scene.render.fps,
        "frame_start": scene.frame_start,
        "frame_end": scene.frame_end,
        "frame_current": scene.frame_current,
        "active_camera": scene.camera.name if scene.camera else None,
    }


# ---------------------------------------------------------------------------
# Operation registry for execute_operations
# ---------------------------------------------------------------------------

def _op_add(op_func, args):
    """Run a bpy.ops add function and return the created object info."""
    op_func(**args)
    return _active_object_info()


# ---------------------------------------------------------------------------
# UV unwrapping
# ---------------------------------------------------------------------------

def uv_unwrap(object: str, method: str = "smart_project", params: dict = None) -> dict:
    """Perform UV unwrapping operations.

    Args:
        object: Object name.
        method: One of: smart_project, cube_project, cylinder_project,
                sphere_project, pack_islands, mark_seam, clear_seam.
        params: Method-specific parameters as dict.
    """
    obj = bpy.data.objects.get(object)
    if not obj:
        raise KeyError(f"Object '{object}' not found")
    if obj.type != "MESH":
        raise ValueError(f"Object '{object}' is {obj.type}, not MESH")

    params = params or {}
    bpy.ops.ed.undo_push(message=f"MCP uv_unwrap {object} {method}")

    # Ensure object mode, select target
    if bpy.context.active_object and bpy.context.active_object.mode != "OBJECT":
        bpy.ops.object.mode_set(mode="OBJECT")
    bpy.ops.object.select_all(action="DESELECT")
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj

    # Auto-create UV layer if none exists
    mesh = obj.data
    if not mesh.uv_layers:
        mesh.uv_layers.new(name="UVMap")

    # Enter edit mode, select all geometry
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.select_all(action="SELECT")

    m = method.lower()

    if m == "smart_project":
        angle = params.get("angle_limit", 66.0)
        margin = params.get("island_margin", 0.02)
        bpy.ops.uv.smart_project(angle_limit=math.radians(angle),
                                 island_margin=margin)
        result_info = {"angle_limit": angle, "island_margin": margin}

    elif m == "cube_project":
        cube_size = params.get("cube_size", 1.0)
        bpy.ops.uv.cube_project(cube_size=cube_size)
        result_info = {"cube_size": cube_size}

    elif m == "cylinder_project":
        direction = params.get("direction", "VIEW_ON_EQUATOR")
        bpy.ops.uv.cylinder_project(direction=direction)
        result_info = {"direction": direction}

    elif m == "sphere_project":
        direction = params.get("direction", "VIEW_ON_EQUATOR")
        bpy.ops.uv.sphere_project(direction=direction)
        result_info = {"direction": direction}

    elif m == "pack_islands":
        margin = params.get("margin", 0.01)
        bpy.ops.uv.pack_islands(margin=margin)
        result_info = {"margin": margin}

    elif m == "mark_seam":
        bpy.ops.mesh.mark_seam(clear=False)
        result_info = {"action": "marked"}

    elif m == "clear_seam":
        bpy.ops.mesh.mark_seam(clear=True)
        result_info = {"action": "cleared"}

    else:
        bpy.ops.object.mode_set(mode="OBJECT")
        raise ValueError(
            f"Unknown UV method '{method}'. "
            f"Valid: smart_project, cube_project, cylinder_project, "
            f"sphere_project, pack_islands, mark_seam, clear_seam"
        )

    bpy.ops.object.mode_set(mode="OBJECT")

    uv_names = [uv.name for uv in mesh.uv_layers]
    return {
        "object": object,
        "method": method,
        "params": result_info,
        "uv_layers": uv_names,
    }


# ---------------------------------------------------------------------------
# Boolean operations
# ---------------------------------------------------------------------------

def boolean_operation(object: str, cutter: str, operation: str = "DIFFERENCE",
                      solver: str = "FAST", apply: bool = True,
                      hide_cutter: bool = True) -> dict:
    """Perform a boolean operation between two objects.

    Args:
        object: Target object name (keeps the result).
        cutter: Tool object name (subtracted/added/intersected).
        operation: DIFFERENCE, UNION, or INTERSECT.
        solver: FAST or EXACT.
        apply: If True, apply the modifier immediately.
        hide_cutter: If True, hide the cutter object after operation.
    """
    obj = bpy.data.objects.get(object)
    if not obj:
        raise KeyError(f"Object '{object}' not found")
    cutter_obj = bpy.data.objects.get(cutter)
    if not cutter_obj:
        raise KeyError(f"Cutter object '{cutter}' not found")
    if obj.type != "MESH":
        raise ValueError(f"Object '{object}' is {obj.type}, not MESH")
    if cutter_obj.type != "MESH":
        raise ValueError(f"Cutter '{cutter}' is {cutter_obj.type}, not MESH")

    op = operation.upper()
    if op not in ("DIFFERENCE", "UNION", "INTERSECT"):
        raise ValueError(f"Invalid operation '{operation}'. Valid: DIFFERENCE, UNION, INTERSECT")

    bpy.ops.ed.undo_push(message=f"MCP boolean {object} {op} {cutter}")

    mod_name = f"Boolean_{op}"
    mod = obj.modifiers.new(name=mod_name, type="BOOLEAN")
    mod.operation = op
    mod.solver = solver.upper()
    mod.object = cutter_obj

    applied = False
    if apply:
        if bpy.context.active_object and bpy.context.active_object.mode != "OBJECT":
            bpy.ops.object.mode_set(mode="OBJECT")
        bpy.ops.object.select_all(action="DESELECT")
        obj.select_set(True)
        bpy.context.view_layer.objects.active = obj
        result = bpy.ops.object.modifier_apply(modifier=mod_name)
        applied = result == {"FINISHED"}

    if hide_cutter:
        cutter_obj.hide_set(True)
        cutter_obj.hide_render = True

    mesh = obj.data
    return {
        "object": object,
        "operation": op,
        "cutter": cutter,
        "applied": applied,
        "mesh_stats": {
            "vertices": len(mesh.vertices),
            "edges": len(mesh.edges),
            "polygons": len(mesh.polygons),
        },
    }


# ---------------------------------------------------------------------------
# Scene units
# ---------------------------------------------------------------------------

def set_dimensions(name: str, x: float = None, y: float = None,
                    z: float = None, unit: str = "m",
                    uniform: bool = False) -> dict:
    """Set object dimensions to exact measurements.

    Args:
        name: Object name.
        x, y, z: Target dimensions. Only specified axes are changed.
        unit: Unit of the given values: mm, cm, m, in, ft.
        uniform: If True, scale uniformly based on the first specified axis.
    """
    obj = bpy.data.objects.get(name)
    if not obj:
        raise KeyError(f"Object '{name}' not found")

    unit_to_meters = {
        "mm": 0.001, "cm": 0.01, "m": 1.0, "in": 0.0254, "ft": 0.3048,
    }
    scale_factor = unit_to_meters.get(unit.lower())
    if scale_factor is None:
        raise ValueError(f"Unknown unit '{unit}'. Valid: mm, cm, m, in, ft")

    bpy.ops.ed.undo_push(message=f"MCP set_dimensions {name}")

    # Convert scene unit scale
    scene_scale = bpy.context.scene.unit_settings.scale_length
    current = list(obj.dimensions)
    targets = [x, y, z]

    if uniform:
        # Find first specified axis and compute uniform scale ratio
        for i, t in enumerate(targets):
            if t is not None and current[i] > 1e-9:
                ratio = (t * scale_factor / scene_scale) / current[i]
                obj.scale = tuple(s * ratio for s in obj.scale)
                break
    else:
        for i, t in enumerate(targets):
            if t is not None:
                target_bu = t * scale_factor / scene_scale
                if current[i] > 1e-9:
                    ratio = target_bu / current[i]
                    s = list(obj.scale)
                    s[i] *= ratio
                    obj.scale = tuple(s)

    dims = obj.dimensions
    return {
        "name": name,
        "dimensions": [round(d, 6) for d in dims],
        "scale": [round(s, 6) for s in obj.scale],
        "unit": unit,
    }


# ---------------------------------------------------------------------------
# Mesh fix / repair
# ---------------------------------------------------------------------------

def fix_mesh(object: str, operation: str, params: dict = None) -> dict:
    """Fix common mesh problems for 3D printing and game assets.

    Args:
        object: Object name.
        operation: One of: recalculate_normals, fill_holes, remove_doubles,
                   make_manifold (runs all three in sequence).
        params: Operation-specific parameters.
    """
    obj = bpy.data.objects.get(object)
    if not obj:
        raise KeyError(f"Object '{object}' not found")
    if obj.type != "MESH":
        raise ValueError(f"Object '{object}' is {obj.type}, not MESH")

    params = params or {}
    mesh = obj.data
    before_stats = {
        "vertices": len(mesh.vertices),
        "edges": len(mesh.edges),
        "polygons": len(mesh.polygons),
    }

    bpy.ops.ed.undo_push(message=f"MCP fix_mesh {object} {operation}")

    # Enter edit mode
    if bpy.context.active_object and bpy.context.active_object.mode != "OBJECT":
        bpy.ops.object.mode_set(mode="OBJECT")
    bpy.ops.object.select_all(action="DESELECT")
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.select_all(action="SELECT")

    op = operation.lower()

    if op == "recalculate_normals":
        bpy.ops.mesh.normals_make_consistent(inside=False)
        result_info = {"action": "normals recalculated outward"}

    elif op == "fill_holes":
        bpy.ops.mesh.select_all(action="DESELECT")
        bpy.ops.mesh.select_non_manifold()
        bpy.ops.mesh.edge_face_add()
        result_info = {"action": "holes filled"}

    elif op == "remove_doubles":
        distance = params.get("distance", 0.001)
        bpy.ops.mesh.remove_doubles(threshold=distance)
        result_info = {"distance": distance}

    elif op == "make_manifold":
        # Run all three in sequence
        bpy.ops.mesh.select_all(action="SELECT")
        bpy.ops.mesh.normals_make_consistent(inside=False)
        bpy.ops.mesh.remove_doubles(threshold=params.get("distance", 0.001))
        bpy.ops.mesh.select_all(action="DESELECT")
        bpy.ops.mesh.select_non_manifold()
        try:
            bpy.ops.mesh.edge_face_add()
        except RuntimeError:
            pass  # No non-manifold edges to fill
        result_info = {"action": "normals + remove_doubles + fill_holes"}

    else:
        bpy.ops.object.mode_set(mode="OBJECT")
        raise ValueError(
            f"Unknown fix operation '{operation}'. "
            f"Valid: recalculate_normals, fill_holes, remove_doubles, make_manifold"
        )

    bpy.ops.object.mode_set(mode="OBJECT")

    mesh = obj.data
    return {
        "object": object,
        "operation": operation,
        "params": result_info,
        "before": before_stats,
        "after": {
            "vertices": len(mesh.vertices),
            "edges": len(mesh.edges),
            "polygons": len(mesh.polygons),
        },
    }


# ---------------------------------------------------------------------------
# Texture loading
# ---------------------------------------------------------------------------

def load_texture(material: str, filepath: str, input_name: str = "Base Color",
                 name: str = None, non_color: bool = False) -> dict:
    """Load an image texture and connect it to a Principled BSDF input.

    Args:
        material: Material name.
        filepath: Path to image file.
        input_name: Principled BSDF input name (Base Color, Metallic,
                    Roughness, Normal, Emission Color, Alpha, etc.).
        name: Optional node name.
        non_color: If True, set colorspace to Non-Color (for normal/roughness maps).
    """
    mat = bpy.data.materials.get(material)
    if not mat:
        raise KeyError(f"Material '{material}' not found")

    bpy.ops.ed.undo_push(message=f"MCP load_texture {material} {input_name}")

    if not mat.use_nodes:
        mat.use_nodes = True

    tree = mat.node_tree

    # Find Principled BSDF
    principled = None
    for node in tree.nodes:
        if node.type == "BSDF_PRINCIPLED":
            principled = node
            break
    if not principled:
        raise ValueError(f"Material '{material}' has no Principled BSDF node")

    # Create image texture node
    tex_node = tree.nodes.new("ShaderNodeTexImage")
    tex_node.image = bpy.data.images.load(filepath)
    if name:
        tex_node.name = name
        tex_node.label = name

    if non_color or input_name.lower() in ("normal", "metallic", "roughness",
                                             "ambient occlusion", "displacement"):
        tex_node.image.colorspace_settings.name = "Non-Color"

    # Position to the left of Principled BSDF, stacked vertically
    existing_tex_nodes = [n for n in tree.nodes if n.type == "TEX_IMAGE"]
    y_offset = -300 * (len(existing_tex_nodes) - 1)
    tex_node.location = (principled.location.x - 400, principled.location.y + y_offset)

    # For Normal input, insert a Normal Map node
    if input_name.lower() == "normal":
        normal_node = tree.nodes.new("ShaderNodeNormalMap")
        normal_node.location = (principled.location.x - 200,
                                tex_node.location.y)
        tree.links.new(tex_node.outputs["Color"], normal_node.inputs["Color"])
        tree.links.new(normal_node.outputs["Normal"], principled.inputs["Normal"])
        connected_to = "Normal (via Normal Map node)"
    else:
        target_input = principled.inputs.get(input_name)
        if not target_input:
            available = [inp.name for inp in principled.inputs]
            raise KeyError(
                f"Input '{input_name}' not found on Principled BSDF. "
                f"Available: {available}"
            )
        tree.links.new(tex_node.outputs["Color"], target_input)
        connected_to = input_name

    return {
        "material": material,
        "node": tex_node.name,
        "image": filepath,
        "connected_to": connected_to,
        "non_color": tex_node.image.colorspace_settings.name == "Non-Color",
    }


# ---------------------------------------------------------------------------
# PBR material presets
# ---------------------------------------------------------------------------

_PBR_PRESETS = {
    "weathered_metal": {
        "base_color": [0.45, 0.42, 0.38, 1.0],
        "metallic": 0.85, "roughness": 0.65, "specular": 0.5,
    },
    "worn_plastic": {
        "base_color": [0.3, 0.3, 0.32, 1.0],
        "metallic": 0.0, "roughness": 0.4, "specular": 0.5,
    },
    "wood": {
        "base_color": [0.4, 0.25, 0.12, 1.0],
        "metallic": 0.0, "roughness": 0.7, "specular": 0.3,
    },
    "fabric": {
        "base_color": [0.35, 0.3, 0.28, 1.0],
        "metallic": 0.0, "roughness": 0.85, "specular": 0.2,
    },
    "rubber": {
        "base_color": [0.05, 0.05, 0.05, 1.0],
        "metallic": 0.0, "roughness": 0.9, "specular": 0.3,
    },
    "concrete": {
        "base_color": [0.55, 0.53, 0.5, 1.0],
        "metallic": 0.0, "roughness": 0.85, "specular": 0.2,
    },
    "glass": {
        "base_color": [0.95, 0.95, 0.95, 1.0],
        "metallic": 0.0, "roughness": 0.05, "specular": 0.5,
        "transmission": 0.95, "ior": 1.45,
    },
}


def create_pbr_material(name: str, preset: str = None,
                        base_color: list = None, metallic: float = None,
                        roughness: float = None, specular: float = None,
                        emission_color: list = None,
                        emission_strength: float = None,
                        alpha: float = None, transmission: float = None,
                        ior: float = None) -> dict:
    """Create a PBR material with optional preset and overrides.

    Args:
        name: Material name.
        preset: Optional preset name: weathered_metal, worn_plastic, wood,
                fabric, rubber, concrete, glass.
        base_color: [R, G, B, A] (0-1).
        metallic, roughness, specular, alpha, transmission, ior: Float values.
        emission_color: [R, G, B, A] (0-1).
        emission_strength: Emission strength.
    """
    bpy.ops.ed.undo_push(message=f"MCP create_pbr_material {name}")

    mat = bpy.data.materials.new(name=name)
    mat.use_nodes = True

    principled = None
    for node in mat.node_tree.nodes:
        if node.type == "BSDF_PRINCIPLED":
            principled = node
            break

    if not principled:
        raise RuntimeError("Failed to create Principled BSDF node")

    # Apply preset values first
    values = {}
    if preset:
        p = preset.lower()
        if p not in _PBR_PRESETS:
            raise ValueError(
                f"Unknown preset '{preset}'. "
                f"Valid: {', '.join(sorted(_PBR_PRESETS.keys()))}"
            )
        values.update(_PBR_PRESETS[p])

    # Override with explicit values
    if base_color is not None:
        values["base_color"] = base_color
    if metallic is not None:
        values["metallic"] = metallic
    if roughness is not None:
        values["roughness"] = roughness
    if specular is not None:
        values["specular"] = specular
    if alpha is not None:
        values["alpha"] = alpha
    if transmission is not None:
        values["transmission"] = transmission
    if ior is not None:
        values["ior"] = ior
    if emission_color is not None:
        values["emission_color"] = emission_color
    if emission_strength is not None:
        values["emission_strength"] = emission_strength

    # Map to Blender 4.0+ Principled BSDF input names
    input_map = {
        "base_color": "Base Color",
        "metallic": "Metallic",
        "roughness": "Roughness",
        "specular": "Specular IOR Level",
        "alpha": "Alpha",
        "transmission": "Transmission Weight",
        "ior": "IOR",
        "emission_color": "Emission Color",
        "emission_strength": "Emission Strength",
    }

    applied = {}
    for key, val in values.items():
        socket_name = input_map.get(key)
        if socket_name and principled.inputs.get(socket_name) is not None:
            inp = principled.inputs[socket_name]
            if isinstance(val, list):
                inp.default_value = tuple(val)
            else:
                inp.default_value = val
            applied[key] = val

    # Handle glass blend mode
    if values.get("transmission", 0) > 0:
        mat.surface_render_method = "BLENDED"

    return {
        "material": mat.name,
        "preset": preset,
        "applied_values": applied,
    }


# ---------------------------------------------------------------------------
# Scene units
# ---------------------------------------------------------------------------

def set_scene_units(system: str = "METRIC", length: str = "MILLIMETERS",
                    scale: float = 1.0) -> dict:
    """Set scene unit system.

    Args:
        system: METRIC, IMPERIAL, or NONE.
        length: ADAPTIVE, KILOMETERS, METERS, CENTIMETERS, MILLIMETERS,
                MICROMETERS, MILES, FEET, INCHES, THOU.
        scale: Unit scale multiplier.
    """
    bpy.ops.ed.undo_push(message="MCP set_scene_units")
    units = bpy.context.scene.unit_settings
    units.system = system.upper()
    units.length_unit = length.upper()
    units.scale_length = scale

    return {
        "system": units.system,
        "length_unit": units.length_unit,
        "scale_length": round(units.scale_length, 6),
    }


# ---------------------------------------------------------------------------
# Mirror modifier convenience
# ---------------------------------------------------------------------------

def add_mirror(object: str, axis: str = "X", use_bisect: bool = False,
               merge_threshold: float = 0.001, apply: bool = False) -> dict:
    """Add a mirror modifier with smart defaults.

    Args:
        object: Object name.
        axis: Axis string, e.g. "X", "Y", "Z", "XY", "XZ", "YZ", "XYZ".
        use_bisect: If True, bisect the mesh at the mirror plane.
        merge_threshold: Distance for merging vertices at the mirror seam.
        apply: If True, apply the modifier immediately.
    """
    obj = bpy.data.objects.get(object)
    if not obj:
        raise KeyError(f"Object '{object}' not found")
    if obj.type != "MESH":
        raise ValueError(f"Object '{object}' is {obj.type}, not MESH")

    bpy.ops.ed.undo_push(message=f"MCP add_mirror {object} {axis}")

    mod = obj.modifiers.new(name="Mirror", type="MIRROR")
    ax = axis.upper()
    mod.use_axis[0] = "X" in ax
    mod.use_axis[1] = "Y" in ax
    mod.use_axis[2] = "Z" in ax

    if use_bisect:
        mod.use_bisect_axis[0] = "X" in ax
        mod.use_bisect_axis[1] = "Y" in ax
        mod.use_bisect_axis[2] = "Z" in ax

    mod.merge_threshold = merge_threshold

    applied = False
    if apply:
        if bpy.context.active_object and bpy.context.active_object.mode != "OBJECT":
            bpy.ops.object.mode_set(mode="OBJECT")
        bpy.ops.object.select_all(action="DESELECT")
        obj.select_set(True)
        bpy.context.view_layer.objects.active = obj
        result = bpy.ops.object.modifier_apply(modifier=mod.name)
        applied = result == {"FINISHED"}

    mesh = obj.data
    return {
        "object": object,
        "modifier": mod.name if not applied else "Mirror (applied)",
        "axis": ax,
        "applied": applied,
        "mesh_stats": {
            "vertices": len(mesh.vertices),
            "edges": len(mesh.edges),
            "polygons": len(mesh.polygons),
        },
    }


# ---------------------------------------------------------------------------
# Array / pattern tools
# ---------------------------------------------------------------------------

def array_pattern(object: str, pattern: str = "linear", count: int = 5,
                  offset: list = None, params: dict = None) -> dict:
    """Create an array pattern (linear or circular).

    Args:
        object: Object name.
        pattern: 'linear' or 'circular'.
        count: Number of copies.
        offset: For linear: relative offset [x, y, z]. Default [1, 0, 0].
        params: For circular: axis ('X', 'Y', or 'Z'). Default 'Z'.
    """
    obj = bpy.data.objects.get(object)
    if not obj:
        raise KeyError(f"Object '{object}' not found")

    params = params or {}
    bpy.ops.ed.undo_push(message=f"MCP array_pattern {object} {pattern}")

    p = pattern.lower()

    if p == "linear":
        offset = offset or [1, 0, 0]
        mod = obj.modifiers.new(name="Array_Linear", type="ARRAY")
        mod.count = count
        mod.use_relative_offset = True
        mod.relative_offset_displace = tuple(offset)

        return {
            "object": object,
            "pattern": "linear",
            "modifier": mod.name,
            "count": count,
            "offset": offset,
        }

    elif p == "circular":
        axis = params.get("axis", "Z").upper()
        axis_index = {"X": 0, "Y": 1, "Z": 2}.get(axis, 2)

        # Create empty at object's location for rotation offset
        empty = bpy.data.objects.new(f"{object}_ArrayEmpty", None)
        bpy.context.collection.objects.link(empty)
        empty.location = obj.location.copy()
        empty.empty_display_size = 0.25

        # Set rotation on empty: 360/count degrees around axis
        rot = [0, 0, 0]
        rot[axis_index] = math.radians(360.0 / count)
        empty.rotation_euler = tuple(rot)

        # Array modifier using object offset
        mod = obj.modifiers.new(name="Array_Circular", type="ARRAY")
        mod.count = count
        mod.use_relative_offset = False
        mod.use_object_offset = True
        mod.offset_object = empty

        return {
            "object": object,
            "pattern": "circular",
            "modifier": mod.name,
            "empty": empty.name,
            "count": count,
            "axis": axis,
            "rotation_per_step": round(math.degrees(rot[axis_index]), 3),
        }

    else:
        raise ValueError(f"Unknown pattern '{pattern}'. Valid: linear, circular")


# ---------------------------------------------------------------------------
# Constraints
# ---------------------------------------------------------------------------

def add_constraint(object: str, type: str, name: str = None,
                   target: str = None, **params) -> dict:
    """Add an object constraint.

    Args:
        object: Object name.
        type: Constraint type (TRACK_TO, COPY_LOCATION, COPY_ROTATION,
              COPY_SCALE, LIMIT_ROTATION, LIMIT_LOCATION, LIMIT_SCALE,
              DAMPED_TRACK, LOCKED_TRACK, CHILD_OF).
        name: Optional constraint name.
        target: Optional target object name.
        **params: Additional constraint properties set via setattr.
    """
    obj = bpy.data.objects.get(object)
    if not obj:
        raise KeyError(f"Object '{object}' not found")

    bpy.ops.ed.undo_push(message=f"MCP add_constraint {object} {type}")

    con = obj.constraints.new(type=type.upper())
    if name:
        con.name = name

    if target:
        target_obj = bpy.data.objects.get(target)
        if not target_obj:
            raise KeyError(f"Target object '{target}' not found")
        con.target = target_obj

    for k, v in params.items():
        if hasattr(con, k):
            setattr(con, k, v)

    return {
        "object": object,
        "constraint": con.name,
        "type": con.type,
        "target": target,
    }


def remove_constraint(object: str, constraint: str) -> dict:
    """Remove a constraint from an object.

    Args:
        object: Object name.
        constraint: Constraint name to remove.
    """
    obj = bpy.data.objects.get(object)
    if not obj:
        raise KeyError(f"Object '{object}' not found")

    con = obj.constraints.get(constraint)
    if not con:
        raise KeyError(f"Constraint '{constraint}' not found on '{object}'")

    bpy.ops.ed.undo_push(message=f"MCP remove_constraint {object} {constraint}")
    obj.constraints.remove(con)

    return {
        "object": object,
        "removed": constraint,
        "remaining": [c.name for c in obj.constraints],
    }


# ---------------------------------------------------------------------------
# Poly Haven integration
# ---------------------------------------------------------------------------

def polyhaven_search(asset_type: str = None, categories: str = None,
                     limit: int = 20) -> dict:
    """Search Poly Haven for free CC0 assets.

    Args:
        asset_type: 'hdris', 'textures', 'models', or None for all.
        categories: Comma-separated category filter.
        limit: Max results (default 20).
    """
    from .integrations.polyhaven import search_assets
    return search_assets(asset_type=asset_type, categories=categories, limit=limit)


def polyhaven_download(asset_id: str, asset_type: str, resolution: str = "1k",
                       format: str = None, apply_to: str = None) -> dict:
    """Download and import a Poly Haven asset into Blender.

    Args:
        asset_id: Asset ID from polyhaven_search results.
        asset_type: 'hdris', 'textures', or 'models'.
        resolution: '1k', '2k', '4k', etc.
        format: File format. Defaults: hdr for HDRIs, jpg for textures, gltf for models.
        apply_to: Object name to assign texture material to (textures only).
    """
    from .integrations import polyhaven as ph

    asset_type = asset_type.lower()
    files_data = ph.get_asset_info(asset_id)

    bpy.ops.ed.undo_push(message=f"MCP polyhaven_download {asset_id}")

    if asset_type == "hdris":
        fmt = format or "hdr"
        url = ph.get_hdri_url(files_data, resolution, fmt)
        tmp_path = ph.download_file(url, suffix=f".{fmt}")

        try:
            # Set up world environment
            world = bpy.context.scene.world
            if not world:
                world = bpy.data.worlds.new("World")
                bpy.context.scene.world = world
            world.use_nodes = True
            tree = world.node_tree
            tree.nodes.clear()

            tex_coord = tree.nodes.new("ShaderNodeTexCoord")
            tex_coord.location = (-800, 0)
            mapping = tree.nodes.new("ShaderNodeMapping")
            mapping.location = (-600, 0)
            env_tex = tree.nodes.new("ShaderNodeTexEnvironment")
            env_tex.location = (-300, 0)
            env_tex.image = bpy.data.images.load(tmp_path)
            env_tex.image.name = f"PH_{asset_id}"
            # Color space
            for cs in ("Linear", "Linear Rec.709", "Non-Color"):
                try:
                    env_tex.image.colorspace_settings.name = cs
                    break
                except TypeError:
                    continue
            env_tex.image.pack()

            background = tree.nodes.new("ShaderNodeBackground")
            background.location = (-100, 0)
            output = tree.nodes.new("ShaderNodeOutputWorld")
            output.location = (100, 0)

            tree.links.new(tex_coord.outputs["Generated"], mapping.inputs["Vector"])
            tree.links.new(mapping.outputs["Vector"], env_tex.inputs["Vector"])
            tree.links.new(env_tex.outputs["Color"], background.inputs["Color"])
            tree.links.new(background.outputs["Background"], output.inputs["Surface"])
        finally:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass

        return {
            "asset_id": asset_id,
            "type": "hdri",
            "resolution": resolution,
            "image": env_tex.image.name,
        }

    elif asset_type == "textures":
        fmt = format or "jpg"
        urls = ph.get_texture_urls(files_data, resolution, fmt)
        if not urls:
            raise ValueError(
                f"No texture maps found for '{asset_id}' at {resolution}/{fmt}"
            )

        # Create PBR material with all available maps
        mat = bpy.data.materials.new(name=f"PH_{asset_id}")
        mat.use_nodes = True
        tree = mat.node_tree
        tree.nodes.clear()

        # Core nodes
        tex_coord = tree.nodes.new("ShaderNodeTexCoord")
        tex_coord.location = (-1000, 0)
        mapping = tree.nodes.new("ShaderNodeMapping")
        mapping.location = (-800, 0)
        tree.links.new(tex_coord.outputs["UV"], mapping.inputs["Vector"])

        principled = tree.nodes.new("ShaderNodeBsdfPrincipled")
        principled.location = (0, 0)
        output = tree.nodes.new("ShaderNodeOutputMaterial")
        output.location = (300, 0)
        tree.links.new(principled.outputs["BSDF"], output.inputs["Surface"])

        # Map types to Principled BSDF inputs
        map_connections = {
            "diffuse": "Base Color", "color": "Base Color", "albedo": "Base Color",
            "rough": "Roughness", "roughness": "Roughness",
            "metal": "Metallic", "metallic": "Metallic",
            "nor_gl": "Normal", "normal": "Normal",
        }

        loaded_maps = []
        y_offset = 300

        for map_type, url in urls.items():
            bsdf_input = None
            for key, inp in map_connections.items():
                if key in map_type.lower():
                    bsdf_input = inp
                    break
            if not bsdf_input:
                continue  # Skip unknown map types

            tmp_path = ph.download_file(url, suffix=f".{fmt}")
            try:
                tex_node = tree.nodes.new("ShaderNodeTexImage")
                tex_node.location = (-400, y_offset)
                tex_node.image = bpy.data.images.load(tmp_path)
                tex_node.image.name = f"PH_{asset_id}_{map_type}"

                is_color = bsdf_input == "Base Color"
                tex_node.image.colorspace_settings.name = "sRGB" if is_color else "Non-Color"
                tex_node.image.pack()

                tree.links.new(mapping.outputs["Vector"], tex_node.inputs["Vector"])

                if bsdf_input == "Normal":
                    normal_node = tree.nodes.new("ShaderNodeNormalMap")
                    normal_node.location = (-200, y_offset)
                    tree.links.new(tex_node.outputs["Color"], normal_node.inputs["Color"])
                    tree.links.new(normal_node.outputs["Normal"], principled.inputs["Normal"])
                else:
                    tree.links.new(tex_node.outputs["Color"], principled.inputs[bsdf_input])

                loaded_maps.append(map_type)
                y_offset -= 300
            finally:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass

        # Apply to object if requested
        if apply_to:
            obj = bpy.data.objects.get(apply_to)
            if obj:
                if obj.data.materials:
                    obj.data.materials[0] = mat
                else:
                    obj.data.materials.append(mat)

        return {
            "asset_id": asset_id,
            "type": "texture",
            "material": mat.name,
            "resolution": resolution,
            "maps_loaded": loaded_maps,
            "applied_to": apply_to,
        }

    elif asset_type == "models":
        import shutil

        fmt = format or "gltf"
        model_info = ph.get_model_files(files_data, resolution, fmt)

        # Download into a temp directory to preserve relative paths
        tmp_dir = tempfile.mkdtemp(prefix="ph_model_")
        try:
            # Download main file
            main_path = os.path.join(tmp_dir, model_info["main_filename"])
            req = urllib.request.Request(model_info["main_url"],
                                         headers={"User-Agent": "blender-mcp/2.0"})
            with urllib.request.urlopen(req, timeout=120) as resp:
                with open(main_path, "wb") as f:
                    while True:
                        chunk = resp.read(65536)
                        if not chunk:
                            break
                        f.write(chunk)

            # Download include files (textures, .bin, etc.)
            for rel_path, inc_url in model_info["includes"].items():
                inc_path = os.path.join(tmp_dir, rel_path)
                os.makedirs(os.path.dirname(inc_path), exist_ok=True)
                req = urllib.request.Request(inc_url,
                                             headers={"User-Agent": "blender-mcp/2.0"})
                with urllib.request.urlopen(req, timeout=120) as resp:
                    with open(inc_path, "wb") as f:
                        while True:
                            chunk = resp.read(65536)
                            if not chunk:
                                break
                            f.write(chunk)

            # Import into Blender
            before_names = set(o.name for o in bpy.data.objects)
            if fmt in ("gltf", "glb"):
                bpy.ops.import_scene.gltf(filepath=main_path)
            elif fmt == "fbx":
                bpy.ops.import_scene.fbx(filepath=main_path)
            elif fmt == "obj":
                bpy.ops.wm.obj_import(filepath=main_path)
            else:
                raise ValueError(f"Unsupported model format: {fmt}")

            after_names = set(o.name for o in bpy.data.objects)
            new_objects = sorted(after_names - before_names)

            # Pack all images so the .blend is portable
            for img in bpy.data.images:
                if img.filepath and tmp_dir in img.filepath.replace("\\", "/"):
                    try:
                        img.pack()
                    except Exception:
                        pass

        finally:
            try:
                shutil.rmtree(tmp_dir)
            except OSError:
                pass

        return {
            "asset_id": asset_id,
            "type": "model",
            "format": fmt,
            "imported_objects": new_objects,
            "includes_downloaded": len(model_info["includes"]),
        }

    else:
        raise ValueError(
            f"Invalid asset_type '{asset_type}'. Valid: hdris, textures, models"
        )


OPERATION_REGISTRY = {
    "add_cube": lambda a: _op_add(bpy.ops.mesh.primitive_cube_add, a),
    "add_sphere": lambda a: _op_add(bpy.ops.mesh.primitive_uv_sphere_add, a),
    "add_plane": lambda a: _op_add(bpy.ops.mesh.primitive_plane_add, a),
    "add_cylinder": lambda a: _op_add(bpy.ops.mesh.primitive_cylinder_add, a),
    "add_cone": lambda a: _op_add(bpy.ops.mesh.primitive_cone_add, a),
    "add_torus": lambda a: _op_add(bpy.ops.mesh.primitive_torus_add, a),
    "shade_smooth": lambda a: (bpy.ops.object.shade_smooth(), "ok")[1],
    "shade_flat": lambda a: (bpy.ops.object.shade_flat(), "ok")[1],
    "subdivide": lambda a: (bpy.ops.mesh.subdivide(**a), "ok")[1],
}

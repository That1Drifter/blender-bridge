# Blender Bridge — Scene Introspection & Diff Engine

import bpy
import bmesh
import hashlib
import json

from .constants import ENGINE_ALIASES, PROTOCOL_VERSION, VALID_ENGINES


def get_capabilities(command_names, defaults, raw_exec=False):
    """Return protocol-v1 capabilities for the currently running bridge."""
    # Import lazily: package initialization imports Dispatcher, which imports this
    # module.  ``bl_info`` has already been defined before that import occurs.
    from . import bl_info

    return {
        # Existing protocol-v1 fields.
        "protocol_version": PROTOCOL_VERSION,
        "commands": sorted(command_names),
        "defaults": defaults,
        # Additive runtime and compatibility details.
        "addon_version": list(bl_info["version"]),
        "blender_version": list(bpy.app.version),
        "blender_version_string": bpy.app.version_string,
        "background": bpy.app.background,
        "supported_protocol_versions": [PROTOCOL_VERSION],
        "supported_render_engines": sorted(VALID_ENGINES),
        "render_engine_aliases": dict(sorted(ENGINE_ALIASES.items())),
        "features": {
            "checkpoints": True,
            "polyhaven": True,
            "raw_exec": bool(raw_exec),
            "screenshots": not bpy.app.background,
            "jobs": True,
        },
    }


# ---------------------------------------------------------------------------
# Snapshot data structures (plain dicts for easy serialization)
# ---------------------------------------------------------------------------

def _round3(v):
    """Round a tuple/list of floats to 3 decimal places."""
    return tuple(round(x, 3) for x in v)


def _hash_str(s):
    return hashlib.md5(s.encode()).hexdigest()[:8]


def _object_snapshot(obj):
    """Capture lightweight snapshot of a single object."""
    snap = {
        "name": obj.name,
        "type": obj.type,
        "location": _round3(obj.location),
        "rotation_euler": _round3(obj.rotation_euler),
        "scale": _round3(obj.scale),
        "visible": obj.visible_get(),
        "render_visible": not obj.hide_render,
        "parent": obj.parent.name if obj.parent else None,
        "material_names": [s.material.name if s.material else "" for s in obj.material_slots],
        "modifier_names": [m.name for m in obj.modifiers],
        "constraint_names": [c.name for c in obj.constraints],
    }

    # Mesh stats for change detection
    if obj.type == "MESH" and obj.data:
        mesh = obj.data
        snap["mesh_stats"] = (len(mesh.vertices), len(mesh.edges), len(mesh.polygons))
    else:
        snap["mesh_stats"] = None

    # Build a hash of key properties for fast comparison
    hash_input = json.dumps(snap, sort_keys=True)
    snap["_hash"] = _hash_str(hash_input)
    return snap


def _material_hash(mat):
    """Hash a material's key properties for change detection."""
    parts = [mat.name, str(mat.use_nodes)]
    if mat.use_nodes and mat.node_tree:
        for node in mat.node_tree.nodes:
            parts.append(f"{node.type}:{node.name}")
            for inp in node.inputs:
                if hasattr(inp, "default_value"):
                    try:
                        parts.append(f"{inp.name}={list(inp.default_value)}")
                    except TypeError:
                        parts.append(f"{inp.name}={inp.default_value}")
        for link in mat.node_tree.links:
            parts.append(f"{link.from_node.name}.{link.from_socket.name}->{link.to_node.name}.{link.to_socket.name}")
    return _hash_str("|".join(parts))


# ---------------------------------------------------------------------------
# Scene snapshot
# ---------------------------------------------------------------------------

def capture_snapshot():
    """Capture a lightweight, comparable snapshot of the current scene state.
    Targets <5ms for scenes with <500 objects.
    """
    scene = bpy.context.scene
    objects = {}
    for obj in scene.objects:
        objects[obj.name] = _object_snapshot(obj)

    materials = {}
    for mat in bpy.data.materials:
        materials[mat.name] = _material_hash(mat)

    return {
        "scene_name": scene.name,
        "frame_current": scene.frame_current,
        "render_engine": scene.render.engine,
        "objects": objects,
        "materials": materials,
    }


# ---------------------------------------------------------------------------
# Diff computation
# ---------------------------------------------------------------------------

def compute_diff(before, after):
    """Compare two snapshots and return a diff dict, or None if identical."""
    diff = {
        "objects_added": [],
        "objects_removed": [],
        "objects_modified": {},
        "materials_added": [],
        "materials_removed": [],
        "materials_modified": [],
    }
    has_changes = False

    # Object diffs
    before_names = set(before["objects"].keys())
    after_names = set(after["objects"].keys())

    added = after_names - before_names
    removed = before_names - after_names

    if added:
        diff["objects_added"] = sorted(added)
        has_changes = True
    if removed:
        diff["objects_removed"] = sorted(removed)
        has_changes = True

    # Check modified objects (same name, different hash)
    for name in before_names & after_names:
        b = before["objects"][name]
        a = after["objects"][name]
        if b["_hash"] != a["_hash"]:
            changes = {}
            for key in ("location", "rotation_euler", "scale", "visible",
                        "render_visible", "parent", "material_names",
                        "modifier_names", "constraint_names", "mesh_stats"):
                if b.get(key) != a.get(key):
                    changes[key] = {"old": b.get(key), "new": a.get(key)}
            if changes:
                diff["objects_modified"][name] = changes
                has_changes = True

    # Material diffs
    before_mats = set(before["materials"].keys())
    after_mats = set(after["materials"].keys())

    mat_added = after_mats - before_mats
    mat_removed = before_mats - after_mats
    if mat_added:
        diff["materials_added"] = sorted(mat_added)
        has_changes = True
    if mat_removed:
        diff["materials_removed"] = sorted(mat_removed)
        has_changes = True

    for name in before_mats & after_mats:
        if before["materials"][name] != after["materials"][name]:
            diff["materials_modified"].append(name)
            has_changes = True

    # Render engine change
    if before["render_engine"] != after["render_engine"]:
        diff["render_engine_changed"] = {
            "old": before["render_engine"],
            "new": after["render_engine"],
        }
        has_changes = True

    return diff if has_changes else None


# ---------------------------------------------------------------------------
# Query handlers (registered with the dispatcher)
# ---------------------------------------------------------------------------

def get_scene_info(limit: int = 50, offset: int = 0):
    """Get scene overview with paginated object list."""
    scene = bpy.context.scene
    all_objects = list(scene.objects)
    total = len(all_objects)
    page = all_objects[offset:offset + limit]

    objects = []
    for obj in page:
        entry = {
            "name": obj.name,
            "type": obj.type,
            "location": list(_round3(obj.location)),
            "parent": obj.parent.name if obj.parent else None,
            "visible": obj.visible_get(),
        }
        objects.append(entry)

    collections = {}
    for col in bpy.data.collections:
        collections[col.name] = [o.name for o in col.objects]

    return {
        "scene_name": scene.name,
        "frame_current": scene.frame_current,
        "frame_range": [scene.frame_start, scene.frame_end],
        "render_engine": scene.render.engine,
        "object_count": total,
        "objects": objects,
        "offset": offset,
        "limit": limit,
        "materials_count": len(bpy.data.materials),
        "collections": collections,
    }


def get_object_info(name: str, include_modifiers: bool = True,
                    include_materials: bool = True,
                    include_constraints: bool = True):
    """Get detailed info about a single object."""
    obj = bpy.data.objects.get(name)
    if not obj:
        raise KeyError(f"Object '{name}' not found")

    info = {
        "name": obj.name,
        "type": obj.type,
        "location": list(_round3(obj.location)),
        "rotation_euler": list(_round3(obj.rotation_euler)),
        "rotation_mode": obj.rotation_mode,
        "scale": list(_round3(obj.scale)),
        "dimensions": list(_round3(obj.dimensions)),
        "visible": obj.visible_get(),
        "render_visible": not obj.hide_render,
        "parent": obj.parent.name if obj.parent else None,
        "children": [c.name for c in obj.children],
        "collections": [c.name for c in obj.users_collection],
    }

    # Mesh data
    if obj.type == "MESH" and obj.data:
        mesh = obj.data
        info["mesh"] = {
            "vertices": len(mesh.vertices),
            "edges": len(mesh.edges),
            "polygons": len(mesh.polygons),
            "has_uv": len(mesh.uv_layers) > 0,
            "uv_layers": [uv.name for uv in mesh.uv_layers],
            "has_vertex_colors": len(mesh.color_attributes) > 0,
        }

    # Bounding box
    if hasattr(obj, "bound_box") and obj.bound_box:
        corners = [obj.matrix_world @ __import__("mathutils").Vector(c) for c in obj.bound_box]
        xs = [c.x for c in corners]
        ys = [c.y for c in corners]
        zs = [c.z for c in corners]
        info["bounding_box"] = {
            "min": [round(min(xs), 3), round(min(ys), 3), round(min(zs), 3)],
            "max": [round(max(xs), 3), round(max(ys), 3), round(max(zs), 3)],
        }

    # Modifiers
    if include_modifiers:
        mods = []
        for m in obj.modifiers:
            mod_info = {
                "name": m.name,
                "type": m.type,
                "show_viewport": m.show_viewport,
                "show_render": m.show_render,
            }
            # Common modifier properties
            if m.type == "SUBSURF":
                mod_info["levels"] = m.levels
                mod_info["render_levels"] = m.render_levels
            elif m.type == "MIRROR":
                mod_info["use_axis"] = [m.use_axis[0], m.use_axis[1], m.use_axis[2]]
            elif m.type == "ARRAY":
                mod_info["count"] = m.count
            elif m.type == "SOLIDIFY":
                mod_info["thickness"] = round(m.thickness, 4)
            elif m.type == "BEVEL":
                mod_info["width"] = round(m.width, 4)
                mod_info["segments"] = m.segments
            mods.append(mod_info)
        info["modifiers"] = mods

    # Materials
    if include_materials:
        mats = []
        for i, slot in enumerate(obj.material_slots):
            mat = slot.material
            if mat:
                mat_entry = {
                    "name": mat.name,
                    "slot_index": i,
                }
                # Extract Principled BSDF basics if present
                if mat.use_nodes and mat.node_tree:
                    for node in mat.node_tree.nodes:
                        if node.type == "BSDF_PRINCIPLED":
                            bc = node.inputs.get("Base Color")
                            if bc:
                                try:
                                    mat_entry["base_color"] = list(round(v, 3) for v in bc.default_value)
                                except TypeError:
                                    pass
                            met = node.inputs.get("Metallic")
                            if met:
                                mat_entry["metallic"] = round(met.default_value, 3)
                            rough = node.inputs.get("Roughness")
                            if rough:
                                mat_entry["roughness"] = round(rough.default_value, 3)
                            break
                mats.append(mat_entry)
        info["materials"] = mats

    # Constraints
    if include_constraints:
        cons = []
        for c in obj.constraints:
            cons.append({
                "name": c.name,
                "type": c.type,
                "enabled": c.enabled,
                "influence": round(c.influence, 3),
            })
        info["constraints"] = cons

    return info


def get_object_bounds(name: str):
    """Get world-space bounding box for an object."""
    obj = bpy.data.objects.get(name)
    if not obj:
        raise KeyError(f"Object '{name}' not found")

    if not hasattr(obj, "bound_box") or not obj.bound_box:
        raise ValueError(f"Object '{name}' has no bounding box")

    import mathutils
    corners = [obj.matrix_world @ mathutils.Vector(c) for c in obj.bound_box]
    xs = [c.x for c in corners]
    ys = [c.y for c in corners]
    zs = [c.z for c in corners]

    return {
        "name": name,
        "min": [round(min(xs), 3), round(min(ys), 3), round(min(zs), 3)],
        "max": [round(max(xs), 3), round(max(ys), 3), round(max(zs), 3)],
        "center": [
            round((min(xs) + max(xs)) / 2, 3),
            round((min(ys) + max(ys)) / 2, 3),
            round((min(zs) + max(zs)) / 2, 3),
        ],
        "dimensions": [
            round(max(xs) - min(xs), 3),
            round(max(ys) - min(ys), 3),
            round(max(zs) - min(zs), 3),
        ],
    }


def get_material_info(name: str):
    """Get full shader node tree for a material."""
    mat = bpy.data.materials.get(name)
    if not mat:
        raise KeyError(f"Material '{name}' not found")

    info = {
        "name": mat.name,
        "use_nodes": mat.use_nodes,
    }

    if not mat.use_nodes or not mat.node_tree:
        return info

    nodes = []
    for node in mat.node_tree.nodes:
        node_info = {
            "name": node.name,
            "type": node.type,
            "bl_idname": node.bl_idname,
            "location": list(_round3(node.location)),
            "inputs": {},
        }
        for inp in node.inputs:
            inp_data = {"type": inp.type, "linked": inp.is_linked}
            if not inp.is_linked and hasattr(inp, "default_value"):
                try:
                    inp_data["value"] = list(inp.default_value)
                except TypeError:
                    inp_data["value"] = inp.default_value
                # Round floats
                if isinstance(inp_data["value"], float):
                    inp_data["value"] = round(inp_data["value"], 4)
                elif isinstance(inp_data["value"], list):
                    inp_data["value"] = [round(v, 4) if isinstance(v, float) else v for v in inp_data["value"]]
            node_info["inputs"][inp.name] = inp_data
        nodes.append(node_info)

    links = []
    for link in mat.node_tree.links:
        links.append({
            "from_node": link.from_node.name,
            "from_socket": link.from_socket.name,
            "to_node": link.to_node.name,
            "to_socket": link.to_socket.name,
        })

    info["nodes"] = nodes
    info["links"] = links
    return info


def get_world_info():
    """Get world/environment settings."""
    world = bpy.context.scene.world
    if not world:
        return {"name": None, "use_nodes": False}

    info = {
        "name": world.name,
        "use_nodes": world.use_nodes,
    }

    if world.use_nodes and world.node_tree:
        nodes = []
        for node in world.node_tree.nodes:
            node_info = {"name": node.name, "type": node.type}
            if node.type == "BACKGROUND":
                color_inp = node.inputs.get("Color")
                strength_inp = node.inputs.get("Strength")
                if color_inp and not color_inp.is_linked:
                    try:
                        node_info["color"] = list(round(v, 3) for v in color_inp.default_value)
                    except TypeError:
                        pass
                if strength_inp and not strength_inp.is_linked:
                    node_info["strength"] = round(strength_inp.default_value, 3)
            nodes.append(node_info)
        info["nodes"] = nodes

    return info


def get_render_settings():
    """Get current render settings."""
    scene = bpy.context.scene
    render = scene.render
    return {
        "engine": render.engine,
        "resolution_x": render.resolution_x,
        "resolution_y": render.resolution_y,
        "resolution_percentage": render.resolution_percentage,
        "film_transparent": render.film_transparent,
        "fps": render.fps,
        "frame_range": [scene.frame_start, scene.frame_end],
        "frame_current": scene.frame_current,
    }


def list_objects(type_filter: str = None, collection: str = None,
                 limit: int = 100, offset: int = 0):
    """List objects with optional filtering."""
    if collection:
        col = bpy.data.collections.get(collection)
        if not col:
            raise KeyError(f"Collection '{collection}' not found")
        objects = list(col.objects)
    else:
        objects = list(bpy.context.scene.objects)

    if type_filter:
        type_upper = type_filter.upper()
        objects = [o for o in objects if o.type == type_upper]

    total = len(objects)
    page = objects[offset:offset + limit]

    return {
        "total": total,
        "offset": offset,
        "limit": limit,
        "objects": [
            {
                "name": o.name,
                "type": o.type,
                "location": list(_round3(o.location)),
                "visible": o.visible_get(),
            }
            for o in page
        ],
    }


# ---------------------------------------------------------------------------
# Mesh validation (3D printing / game asset QA)
# ---------------------------------------------------------------------------

def validate_mesh(name: str) -> dict:
    """Validate mesh geometry for 3D printing and game asset readiness.

    Checks: manifold edges/verts, loose geometry, flipped normals,
    zero-area faces, volume, surface area.

    Args:
        name: Object name.
    """
    obj = bpy.data.objects.get(name)
    if not obj:
        raise KeyError(f"Object '{name}' not found")
    if obj.type != "MESH":
        raise ValueError(f"Object '{name}' is {obj.type}, not MESH")

    mesh = obj.data
    bm = bmesh.new()
    try:
        bm.from_mesh(mesh)
        bm.edges.ensure_lookup_table()
        bm.verts.ensure_lookup_table()
        bm.faces.ensure_lookup_table()

        non_manifold_edges = sum(1 for e in bm.edges if not e.is_manifold)
        non_manifold_verts = sum(1 for v in bm.verts if not v.is_manifold)
        loose_verts = sum(1 for v in bm.verts if not v.link_edges)
        loose_edges = sum(1 for e in bm.edges if not e.link_faces)
        zero_area_faces = sum(1 for f in bm.faces if f.calc_area() < 1e-6)

        # Check normals consistency
        flipped_normals = 0
        if bm.faces:
            from mathutils import Vector
            obj_center = Vector((0, 0, 0))
            for f in bm.faces:
                obj_center += f.calc_center_median()
            obj_center /= len(bm.faces)
            for f in bm.faces:
                to_face = f.calc_center_median() - obj_center
                if to_face.length > 1e-6 and to_face.dot(f.normal) < 0:
                    flipped_normals += 1

        is_manifold = (non_manifold_edges == 0 and non_manifold_verts == 0
                       and loose_verts == 0 and loose_edges == 0)

        volume = None
        if is_manifold:
            try:
                volume = round(bm.calc_volume(), 6)
            except Exception:
                pass

        surface_area = round(sum(f.calc_area() for f in bm.faces), 6)

        is_printable = (is_manifold and flipped_normals == 0
                        and zero_area_faces == 0)

    finally:
        bm.free()

    return {
        "name": name,
        "is_manifold": is_manifold,
        "is_printable": is_printable,
        "non_manifold_edges": non_manifold_edges,
        "non_manifold_verts": non_manifold_verts,
        "loose_verts": loose_verts,
        "loose_edges": loose_edges,
        "flipped_normals": flipped_normals,
        "zero_area_faces": zero_area_faces,
        "volume": volume,
        "surface_area": surface_area,
        "mesh_stats": {
            "vertices": len(mesh.vertices),
            "edges": len(mesh.edges),
            "polygons": len(mesh.polygons),
        },
    }


# ---------------------------------------------------------------------------
# Dimensions / measurement query
# ---------------------------------------------------------------------------

def get_dimensions(name: str) -> dict:
    """Get object dimensions in scene units and millimeters.

    Args:
        name: Object name.
    """
    obj = bpy.data.objects.get(name)
    if not obj:
        raise KeyError(f"Object '{name}' not found")

    dims = obj.dimensions
    units = bpy.context.scene.unit_settings

    # Convert to meters (Blender internal unit), then to mm
    scale = units.scale_length
    dims_m = [d * scale for d in dims]
    dims_mm = [d * 1000 for d in dims_m]

    return {
        "name": name,
        "dimensions": list(_round3(dims)),
        "dimensions_meters": [round(d, 6) for d in dims_m],
        "dimensions_mm": [round(d, 3) for d in dims_mm],
        "unit_system": units.system,
        "unit_length": units.length_unit,
        "unit_scale": round(scale, 6),
        "bounding_box": {
            "min": list(_round3([min(c[i] for c in obj.bound_box) for i in range(3)])),
            "max": list(_round3([max(c[i] for c in obj.bound_box) for i in range(3)])),
        },
    }


# ---------------------------------------------------------------------------
# Texture queries
# ---------------------------------------------------------------------------

def get_textures() -> dict:
    """List all image textures in the scene with metadata.

    Returns info about every loaded image: path, dimensions, colorspace,
    whether it's packed, and how many users reference it.
    """
    textures = []
    for img in bpy.data.images:
        if img.name in ("Render Result", "Viewer Node"):
            continue
        textures.append({
            "name": img.name,
            "filepath": img.filepath or "",
            "width": img.size[0],
            "height": img.size[1],
            "colorspace": img.colorspace_settings.name,
            "packed": img.packed_file is not None,
            "users": img.users,
            "file_format": img.file_format,
        })

    return {
        "total": len(textures),
        "textures": textures,
    }

"""High-level, terminal asset-recipe workflows for Blender Bridge.

Recipes intentionally call the lower-level handler functions directly rather
than re-entering :class:`Dispatcher`.  A recipe therefore has one bridge
history/diff/error boundary and always returns one versioned manifest.
"""

import hashlib
import math
import os
from contextlib import contextmanager
from pathlib import Path

import bpy
import mathutils

from . import introspection
from . import executor as _executor
from .capture import render_image
from .executor import create_pbr_material, export_scene, frame_camera, load_texture


SCHEMA_VERSION = "1"
RECIPE_VERSION = "1"
COORDINATE_SYSTEM = "Z_UP_RIGHT_HANDED"

_TEXTURE_SUFFIXES = {
    "base_color": ("basecolor", "base_color", "albedo", "diffuse", "color", "col"),
    "roughness": ("roughness", "rough", "rgh"),
    "metallic": ("metallic", "metalness", "metal", "mtl"),
    "normal": ("normal", "norm", "nrm"),
    "height": ("height", "displacement", "disp", "bump"),
}
_IMAGE_EXTENSIONS = {".bmp", ".exr", ".hdr", ".jpeg", ".jpg", ".png", ".tga", ".tif", ".tiff", ".webp"}


def _sha256(path):
    """Return the SHA-256 of an on-disk file, or ``None`` for a missing path."""
    if not path or not os.path.isfile(path):
        return None
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _file_entry(path, kind=None):
    entry = {"path": str(path), "sha256": _sha256(str(path))}
    if kind:
        entry["kind"] = kind
    return entry


def _mesh_summary(obj):
    if obj is None or obj.type != "MESH":
        return []
    mesh = obj.data
    return [{
        "name": obj.name,
        "dimensions": [round(value, 6) for value in obj.dimensions],
        "vertices": len(mesh.vertices),
        "triangles": sum(max(0, len(poly.vertices) - 2) for poly in mesh.polygons),
        "materials": [slot.material.name for slot in obj.material_slots if slot.material],
    }]


def _manifest(recipe, source_object, obj=None, *, phases=None, textures=None,
              generated_files=None, export=None, warnings=None, errors=None,
              status="succeeded", validation=None):
    """Assemble the stable terminal manifest shared by every recipe."""
    units = bpy.context.scene.unit_settings
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "recipe": recipe,
        "recipe_version": RECIPE_VERSION,
        "source_object": source_object,
        "objects": _mesh_summary(obj),
        "textures": textures or [],
        "generated_files": generated_files or [],
        "units": {"system": units.system, "scale_length": units.scale_length},
        "coordinate_system": COORDINATE_SYSTEM,
        "warnings": warnings or [],
        "errors": errors or [],
        "status": status,
        "phases": phases or [],
    }
    if export is not None:
        manifest["export"] = export
    if validation is not None:
        manifest["validation"] = validation
    return manifest


def _object(name):
    obj = bpy.data.objects.get(name)
    if not obj:
        raise KeyError(f"Object '{name}' not found")
    if obj.type != "MESH":
        raise ValueError(f"Object '{name}' is {obj.type}, not MESH")
    return obj


def _start_transaction(recipe):
    """Create the recipe's single, explicit undo checkpoint."""
    bpy.ops.ed.undo_push(message=f"Blender Bridge recipe: {recipe}")


class _EditorOpsWithoutUndoPush:
    """Proxy the editor ops namespace but make a nested helper's undo a no-op."""

    def __init__(self, editor_ops):
        self._editor_ops = editor_ops

    def undo_push(self, **_kwargs):
        return {"FINISHED"}

    def __getattr__(self, name):
        return getattr(self._editor_ops, name)


class _OpsWithoutUndoPush:
    def __init__(self, ops):
        self._ops = ops
        self.ed = _EditorOpsWithoutUndoPush(ops.ed)

    def __getattr__(self, name):
        return getattr(self._ops, name)


class _BpyWithoutNestedUndoPush:
    def __init__(self, bpy_module):
        self._bpy_module = bpy_module
        self.ops = _OpsWithoutUndoPush(bpy_module.ops)

    def __getattr__(self, name):
        return getattr(self._bpy_module, name)


@contextmanager
def _recipe_helper_context():
    """Use existing executor handlers without allowing them to create undo steps.

    The executor's public handlers predate recipes and each push their own undo
    entry. Rebinding only that module's ``bpy`` global preserves their operation
    logic while keeping one recipe-level transaction boundary.
    """
    original_bpy = _executor.bpy
    _executor.bpy = _BpyWithoutNestedUndoPush(original_bpy)
    try:
        yield
    finally:
        _executor.bpy = original_bpy


def _phase(phases, name, state, detail=None):
    item = {"name": name, "status": state}
    if detail is not None:
        item["detail"] = detail
    phases.append(item)


def _recipe_failure(recipe, source_object, obj, phases, warnings, errors,
                    textures=None, generated_files=None, export=None,
                    validation=None):
    status = "partial" if any(phase["status"] == "completed" for phase in phases) else "failed"
    return _manifest(
        recipe, source_object, obj, phases=phases, textures=textures,
        generated_files=generated_files, export=export, warnings=warnings,
        errors=errors, status=status, validation=validation,
    )


def _texture_paths(base_color=None, roughness=None, metallic=None, normal=None,
                   height=None, texture_directory=None):
    explicit = {
        "base_color": base_color, "roughness": roughness, "metallic": metallic,
        "normal": normal, "height": height,
    }
    resolved = {kind: path for kind, path in explicit.items() if path}
    if texture_directory:
        directory = Path(texture_directory)
        if not directory.is_dir():
            raise ValueError(f"Texture directory does not exist: {texture_directory}")
        candidates = [path for path in directory.iterdir()
                      if path.is_file() and path.suffix.lower() in _IMAGE_EXTENSIONS]
        for kind, suffixes in _TEXTURE_SUFFIXES.items():
            if kind in resolved:
                continue
            match = next((path for path in candidates
                          if any(suffix in path.stem.lower() for suffix in suffixes)), None)
            if match:
                resolved[kind] = str(match)
    for kind, path in resolved.items():
        if not os.path.isfile(path):
            raise ValueError(f"{kind} texture does not exist: {path}")
    if not resolved:
        raise ValueError("Provide at least one texture path or a texture_directory with recognized map suffixes")
    return resolved


def _connect_height_texture(material_name, filepath):
    """Add the height-map portion that Principled's texture helper does not expose."""
    material = bpy.data.materials[material_name]
    tree = material.node_tree
    output = next((node for node in tree.nodes if node.type == "OUTPUT_MATERIAL"), None)
    if output is None:
        raise RuntimeError(f"Material '{material_name}' has no Material Output node")
    texture = tree.nodes.new("ShaderNodeTexImage")
    texture.image = bpy.data.images.load(filepath)
    texture.image.colorspace_settings.name = "Non-Color"
    displacement = tree.nodes.new("ShaderNodeDisplacement")
    texture.location = (output.location.x - 600, output.location.y - 400)
    displacement.location = (output.location.x - 300, output.location.y - 400)
    tree.links.new(texture.outputs["Color"], displacement.inputs["Height"])
    tree.links.new(displacement.outputs["Displacement"], output.inputs["Displacement"])


def apply_pbr_material_set(object: str, base_color: str = None,
                           roughness: str = None, metallic: str = None,
                           normal: str = None, height: str = None,
                           texture_directory: str = None,
                           material_name: str = None) -> dict:
    """Create one PBR material from a set of texture maps and assign it."""
    obj = _object(object)
    textures = _texture_paths(base_color, roughness, metallic, normal, height, texture_directory)
    phases, warnings, errors = [], [], []
    texture_entries = [_file_entry(path, kind) for kind, path in textures.items()]
    _start_transaction("apply_pbr_material_set")
    try:
        _phase(phases, "resolve_textures", "completed", sorted(textures))
        with _recipe_helper_context():
            material_result = create_pbr_material(material_name or f"{obj.name}_PBR")
        material = material_result["material"]
        _phase(phases, "create_material", "completed", material)
        input_names = {"base_color": "Base Color", "roughness": "Roughness", "metallic": "Metallic", "normal": "Normal"}
        for kind, path in textures.items():
            if kind == "height":
                _connect_height_texture(material, path)
            else:
                with _recipe_helper_context():
                    load_texture(material, path, input_names[kind], name=kind, non_color=kind != "base_color")
        _phase(phases, "connect_textures", "completed")
        obj.data.materials.clear()
        obj.data.materials.append(bpy.data.materials[material])
        _phase(phases, "assign_material", "completed", material)
    except Exception as exc:
        _phase(phases, "failed", "failed", str(exc))
        errors.append(str(exc))
        return _recipe_failure("apply_pbr_material_set", object, obj, phases, warnings, errors, texture_entries)
    return _manifest("apply_pbr_material_set", object, obj, phases=phases, textures=texture_entries,
                     warnings=warnings, errors=errors)


def _validate_asset(obj, max_tris):
    """Compose the existing introspection calls into recipe-specific checks."""
    phases, warnings, errors = [], [], []
    validation = introspection.validate_mesh(obj.name)
    dimensions = introspection.get_dimensions(obj.name)
    _phase(phases, "inspect_mesh", "completed")
    _phase(phases, "inspect_dimensions", "completed", dimensions["dimensions"])
    mesh = obj.data
    triangles = sum(max(0, len(poly.vertices) - 2) for poly in mesh.polygons)
    ngon_count = sum(1 for poly in mesh.polygons if len(poly.vertices) > 4)
    if ngon_count:
        warnings.append(f"{ngon_count} n-gon(s) found")
    if validation["loose_verts"] or validation["loose_edges"]:
        errors.append(f"Loose geometry: {validation['loose_verts']} loose verts, {validation['loose_edges']} loose edges")
    if not mesh.uv_layers:
        warnings.append("Mesh has no UV map")
    if not any(slot.material for slot in obj.material_slots):
        warnings.append("Mesh has no assigned material")
    if any(abs(component - 1.0) > 1e-6 for component in obj.scale):
        warnings.append("Object scale is not applied")
    if max_tris is not None:
        if not isinstance(max_tris, int) or max_tris < 0:
            raise ValueError("max_tris must be a non-negative integer")
        if triangles > max_tris:
            errors.append(f"Triangle budget exceeded: {triangles} > {max_tris}")
    _phase(phases, "apply_game_checks", "completed")
    status = "failed" if errors else "succeeded"
    return phases, warnings, errors, validation, status


def validate_game_asset(object: str, max_tris: int = None) -> dict:
    """Read-only game-asset validation with a standardized recipe manifest."""
    obj = _object(object)
    phases, warnings, errors, validation, status = _validate_asset(obj, max_tris)
    return _manifest("validate_game_asset", object, obj, phases=phases, warnings=warnings,
                     errors=errors, status=status, validation=validation)


def _select_only(obj):
    selected = [candidate for candidate in bpy.context.selected_objects]
    active = bpy.context.view_layer.objects.active
    if active and active.mode != "OBJECT":
        bpy.ops.object.mode_set(mode="OBJECT")
    bpy.ops.object.select_all(action="DESELECT")
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj
    return selected, active


def _restore_selection(selected, active):
    bpy.ops.object.select_all(action="DESELECT")
    for obj in selected:
        if obj.name in bpy.context.view_layer.objects:
            obj.select_set(True)
    bpy.context.view_layer.objects.active = active if active and active.name in bpy.context.view_layer.objects else None


def export_game_asset(object: str, out_path: str, preset: str = "godot",
                      max_tris: int = None) -> dict:
    """Validate one object, then export only that object using a named preset."""
    obj = _object(object)
    if preset.lower() not in {"dayz", "godot", "print"}:
        raise ValueError("preset must be one of: dayz, godot, print")
    phases, warnings, errors = [], [], []
    _start_transaction("export_game_asset")
    validation_phases, validation_warnings, validation_errors, validation, validation_status = _validate_asset(obj, max_tris)
    validation_manifest = _manifest(
        "validate_game_asset", obj.name, obj, phases=validation_phases,
        warnings=validation_warnings, errors=validation_errors,
        status=validation_status, validation=validation,
    )
    warnings.extend(validation_warnings)
    errors.extend(validation_errors)
    _phase(phases, "validate", "completed" if not errors else "failed")
    if errors:
        return _recipe_failure("export_game_asset", object, obj, phases, warnings, errors,
                               validation=validation_manifest)
    selected, active = _select_only(obj)
    try:
        with _recipe_helper_context():
            result = export_scene(out_path, selected_only=True, preset=preset)
        _phase(phases, "export", "completed", result["format"])
    except Exception as exc:
        _phase(phases, "export", "failed", str(exc))
        errors.append(str(exc))
        return _recipe_failure("export_game_asset", object, obj, phases, warnings, errors,
                               validation=validation_manifest)
    finally:
        _restore_selection(selected, active)
    export = {"path": out_path, "format": result["format"], "preset": preset.lower()}
    files = [_file_entry(out_path, "export")]
    if files[0]["sha256"] is None:
        errors.append(f"Export did not create expected file: {out_path}")
        _phase(phases, "verify_export", "failed", errors[-1])
        return _recipe_failure("export_game_asset", object, obj, phases, warnings, errors,
                               generated_files=files, export=export, validation=validation_manifest)
    _phase(phases, "verify_export", "completed")
    return _manifest("export_game_asset", object, obj, phases=phases, generated_files=files,
                     export=export, warnings=warnings, errors=errors, validation=validation_manifest)


def _preview_directory(out_path):
    path = Path(out_path)
    if path.suffix:
        return path.parent / path.stem
    return path


def create_preview_sheet(object: str, out_path: str, resolution: int = 256) -> dict:
    """Render four named preview angles as individual PNG files (no compositing)."""
    obj = _object(object)
    if not isinstance(resolution, int) or resolution < 16:
        raise ValueError("resolution must be an integer of at least 16")
    output_dir = _preview_directory(out_path)
    phases, warnings, errors, generated_files = [], [], [], []
    _start_transaction("create_preview_sheet")
    try:
        output_dir.mkdir(parents=True, exist_ok=True)
        with _recipe_helper_context():
            frame = frame_camera(target=obj.name)
        camera = bpy.data.objects[frame["camera"]]
        center = mathutils.Vector(frame["target_center"])
        distance = frame["distance"]
        _phase(phases, "frame_camera", "completed", camera.name)
        view_offsets = {
            "front": (0.0, -1.0, 0.0),
            "side": (1.0, 0.0, 0.0),
            "top": (0.0, 0.0, 1.0),
            "three_quarter": (1.0, -1.0, 0.7),
        }
        for name, offset in view_offsets.items():
            direction = mathutils.Vector(offset).normalized()
            camera.location = center + direction * distance
            camera.rotation_euler = (center - camera.location).to_track_quat("-Z", "Y").to_euler()
            filepath = output_dir / f"{name}.png"
            render_image(engine="WORKBENCH", samples=1, resolution=[resolution, resolution],
                         format="PNG", save_to=str(filepath), async_mode=False)
            entry = _file_entry(filepath, name)
            generated_files.append(entry)
            if entry["sha256"] is None:
                raise RuntimeError(f"Preview render did not create expected file: {filepath}")
            _phase(phases, f"render_{name}", "completed", str(filepath))
    except Exception as exc:
        _phase(phases, "failed", "failed", str(exc))
        errors.append(str(exc))
        return _recipe_failure("create_preview_sheet", object, obj, phases, warnings, errors,
                               generated_files=generated_files)
    return _manifest("create_preview_sheet", object, obj, phases=phases,
                     generated_files=generated_files, warnings=warnings, errors=errors)

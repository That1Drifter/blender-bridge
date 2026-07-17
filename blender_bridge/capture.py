# Blender Bridge — Viewport Screenshot & Render Capture

import bpy
import tempfile
import base64
import os

from .constants import ENGINE_ALIASES, ERR_UNSUPPORTED_IN_BACKGROUND, VALID_ENGINES


def _resolve_engine(engine: str) -> str:
    """Resolve engine name aliases and validate."""
    eng = ENGINE_ALIASES.get(engine.upper(), engine.upper())
    if eng not in VALID_ENGINES:
        raise ValueError(
            f"Unknown engine '{engine}'. Valid engines: EEVEE, CYCLES, WORKBENCH "
            f"(or full names: {sorted(VALID_ENGINES)})"
        )
    return eng


def _make_thumbnail(source_path: str, thumbnail_size: int = 256) -> str:
    """Create a small JPEG thumbnail from an image file, return base64 string."""
    thumb_path = tempfile.mktemp(suffix=".jpg", prefix="bbridge_thumb_")
    try:
        img = bpy.data.images.load(source_path)
        try:
            width, height = img.size
            if max(width, height) > thumbnail_size:
                scale = thumbnail_size / max(width, height)
                width = int(width * scale)
                height = int(height * scale)
                img.scale(width, height)

            # Save as low-quality JPEG for small thumbnail
            scene = bpy.context.scene
            orig_format = scene.render.image_settings.file_format
            orig_quality = scene.render.image_settings.quality
            scene.render.image_settings.file_format = "JPEG"
            scene.render.image_settings.quality = 60
            img.save_render(filepath=thumb_path)
            scene.render.image_settings.file_format = orig_format
            scene.render.image_settings.quality = orig_quality
        finally:
            bpy.data.images.remove(img)

        with open(thumb_path, "rb") as f:
            return base64.b64encode(f.read()).decode("ascii")
    finally:
        if os.path.exists(thumb_path):
            os.remove(thumb_path)


def viewport_screenshot(max_size: int = 512, format: str = "PNG",
                        save_to: str = None, thumbnail_size: int = 256) -> dict:
    """Capture the 3D viewport.

    If save_to is provided, saves full image to that path and returns the path (no base64).
    Otherwise saves full-res to a temp file and returns a small JPEG thumbnail as base64
    plus the temp file path for the full image.
    """
    if bpy.app.background:
        # The server recognizes this standard error envelope and returns it as
        # the command response rather than wrapping it as a successful result.
        return {
            "status": "error",
            "error": {
                "code": ERR_UNSUPPORTED_IN_BACKGROUND,
                "message": "Viewport screenshots are unavailable in Blender background mode.",
            },
        }

    # Find the active 3D viewport
    area = None
    for a in bpy.context.screen.areas:
        if a.type == "VIEW_3D":
            area = a
            break
    if not area:
        raise RuntimeError("No 3D viewport found")

    fmt = format.upper()
    ext = ".png" if fmt == "PNG" else ".jpg"

    # Determine output path
    if save_to:
        out_path = save_to
    else:
        out_path = tempfile.mktemp(suffix=ext, prefix="bbridge_")

    # Use opengl render for clean 3D-only capture (no UI chrome)
    scene = bpy.context.scene
    orig_filepath = scene.render.filepath
    orig_format = scene.render.image_settings.file_format
    orig_res_x = scene.render.resolution_x
    orig_res_y = scene.render.resolution_y
    orig_pct = scene.render.resolution_percentage

    try:
        # Compute aspect ratio from the 3D viewport
        vw = area.width
        vh = area.height
        aspect = vw / max(1, vh)
        if aspect >= 1:
            res_x = max_size
            res_y = int(max_size / aspect)
        else:
            res_y = max_size
            res_x = int(max_size * aspect)

        scene.render.filepath = out_path
        scene.render.image_settings.file_format = fmt
        scene.render.resolution_x = res_x
        scene.render.resolution_y = res_y
        scene.render.resolution_percentage = 100

        with bpy.context.temp_override(area=area):
            bpy.ops.render.opengl(write_still=True)

        width, height = res_x, res_y
    except Exception:
        # Fallback to screenshot_area if opengl render fails
        raw_tmp = tempfile.mktemp(suffix=ext)
        try:
            with bpy.context.temp_override(area=area):
                bpy.ops.screen.screenshot_area(filepath=raw_tmp)
            img = bpy.data.images.load(raw_tmp)
            try:
                width, height = img.size
                if max(width, height) > max_size:
                    sc = max_size / max(width, height)
                    width = int(width * sc)
                    height = int(height * sc)
                    img.scale(width, height)
                img.file_format = fmt
                img.save_render(filepath=out_path)
                width, height = img.size
            finally:
                bpy.data.images.remove(img)
        finally:
            if os.path.exists(raw_tmp) and raw_tmp != out_path:
                os.remove(raw_tmp)
    finally:
        scene.render.filepath = orig_filepath
        scene.render.image_settings.file_format = orig_format
        scene.render.resolution_x = orig_res_x
        scene.render.resolution_y = orig_res_y
        scene.render.resolution_percentage = orig_pct

    result = {
        "file_path": out_path,
        "width": width,
        "height": height,
        "format": fmt,
    }

    if not save_to:
        # Return a small JPEG thumbnail as base64
        result["base64"] = _make_thumbnail(out_path, thumbnail_size)
        result["format"] = "JPEG"

    return result


def render_image(engine: str = None, samples: int = None,
                 resolution: list = None, format: str = "PNG",
                 save_to: str = None, thumbnail_size: int = 256) -> dict:
    """Render the scene.

    If save_to is provided, saves full render to that path and returns the path (no base64).
    Otherwise saves full-res to a temp file and returns a small JPEG thumbnail as base64
    plus the temp file path for the full render.

    This works in background mode. EEVEE Next may require a GPU there; Cycles
    with its CPU device is the safe fallback for headless rendering.
    """
    import time

    scene = bpy.context.scene
    render_settings = scene.render

    # Save original settings
    orig = {
        "engine": render_settings.engine,
        "resolution_x": render_settings.resolution_x,
        "resolution_y": render_settings.resolution_y,
        "resolution_percentage": render_settings.resolution_percentage,
        "filepath": render_settings.filepath,
        "file_format": render_settings.image_settings.file_format,
    }
    orig_samples = None
    if hasattr(scene, "cycles"):
        orig_samples = scene.cycles.samples
    orig_eevee_samples = None
    if hasattr(scene, "eevee"):
        orig_eevee_samples = scene.eevee.taa_render_samples

    fmt = format.upper()
    ext = ".png" if fmt == "PNG" else ".jpg"

    # Determine output path
    if save_to:
        out_path = save_to
    else:
        out_path = tempfile.mktemp(suffix=ext, prefix="bbridge_")

    try:
        # Apply overrides
        if engine:
            render_settings.engine = _resolve_engine(engine)

        if resolution:
            render_settings.resolution_x = resolution[0]
            render_settings.resolution_y = resolution[1]
            render_settings.resolution_percentage = 100

        if samples:
            if render_settings.engine == "CYCLES" and hasattr(scene, "cycles"):
                scene.cycles.samples = samples
            elif hasattr(scene, "eevee"):
                scene.eevee.taa_render_samples = samples

        render_settings.filepath = out_path
        render_settings.image_settings.file_format = fmt

        # Render
        t0 = time.perf_counter()
        bpy.ops.render.render(write_still=True)
        render_time_ms = int((time.perf_counter() - t0) * 1000)

        result = {
            "file_path": out_path,
            "width": render_settings.resolution_x,
            "height": render_settings.resolution_y,
            "format": fmt,
            "render_time_ms": render_time_ms,
            "engine": render_settings.engine,
        }

        if not save_to:
            # Return a small JPEG thumbnail as base64
            result["base64"] = _make_thumbnail(out_path, thumbnail_size)

        return result
    finally:
        # Restore original settings
        render_settings.engine = orig["engine"]
        render_settings.resolution_x = orig["resolution_x"]
        render_settings.resolution_y = orig["resolution_y"]
        render_settings.resolution_percentage = orig["resolution_percentage"]
        render_settings.filepath = orig["filepath"]
        render_settings.image_settings.file_format = orig["file_format"]
        if orig_samples is not None and hasattr(scene, "cycles"):
            scene.cycles.samples = orig_samples
        if orig_eevee_samples is not None and hasattr(scene, "eevee"):
            scene.eevee.taa_render_samples = orig_eevee_samples

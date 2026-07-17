# Blender Bridge — Viewport Screenshot & Render Capture

import bpy
import tempfile
import base64
import os
import time

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


_active_interactive_render = None


def _create_render_context(engine, samples, resolution, format, save_to, thumbnail_size):
    """Capture settings and prepare the values shared by sync and async renders."""
    scene = bpy.context.scene
    render_settings = scene.render
    fmt = format.upper()
    ext = ".png" if fmt == "PNG" else ".jpg"
    return {
        "scene": scene,
        "render_settings": render_settings,
        "orig": {
            "engine": render_settings.engine,
            "resolution_x": render_settings.resolution_x,
            "resolution_y": render_settings.resolution_y,
            "resolution_percentage": render_settings.resolution_percentage,
            "filepath": render_settings.filepath,
            "file_format": render_settings.image_settings.file_format,
        },
        "orig_samples": scene.cycles.samples if hasattr(scene, "cycles") else None,
        "orig_eevee_samples": (
            scene.eevee.taa_render_samples if hasattr(scene, "eevee") else None
        ),
        "engine": engine,
        "samples": samples,
        "resolution": resolution,
        "fmt": fmt,
        "save_to": save_to,
        "out_path": save_to or tempfile.mktemp(suffix=ext, prefix="bbridge_"),
        "thumbnail_size": thumbnail_size,
        "started_at": None,
    }


def _apply_render_settings(context):
    scene = context["scene"]
    render_settings = context["render_settings"]

    if context["engine"]:
        render_settings.engine = _resolve_engine(context["engine"])

    if context["resolution"]:
        render_settings.resolution_x = context["resolution"][0]
        render_settings.resolution_y = context["resolution"][1]
        render_settings.resolution_percentage = 100

    if context["samples"]:
        if render_settings.engine == "CYCLES" and hasattr(scene, "cycles"):
            scene.cycles.samples = context["samples"]
        elif hasattr(scene, "eevee"):
            scene.eevee.taa_render_samples = context["samples"]

    render_settings.filepath = context["out_path"]
    render_settings.image_settings.file_format = context["fmt"]


def _render_result(context):
    """Build the exact payload shared by synchronous and asynchronous renders."""
    render_settings = context["render_settings"]
    result = {
        "file_path": context["out_path"],
        "width": render_settings.resolution_x,
        "height": render_settings.resolution_y,
        "format": context["fmt"],
        "render_time_ms": int((time.perf_counter() - context["started_at"]) * 1000),
        "engine": render_settings.engine,
    }
    if not context["save_to"]:
        result["base64"] = _make_thumbnail(context["out_path"], context["thumbnail_size"])
    return result


def _restore_render_settings(context):
    scene = context["scene"]
    render_settings = context["render_settings"]
    orig = context["orig"]
    render_settings.engine = orig["engine"]
    render_settings.resolution_x = orig["resolution_x"]
    render_settings.resolution_y = orig["resolution_y"]
    render_settings.resolution_percentage = orig["resolution_percentage"]
    render_settings.filepath = orig["filepath"]
    render_settings.image_settings.file_format = orig["file_format"]
    if context["orig_samples"] is not None and hasattr(scene, "cycles"):
        scene.cycles.samples = context["orig_samples"]
    if context["orig_eevee_samples"] is not None and hasattr(scene, "eevee"):
        scene.eevee.taa_render_samples = context["orig_eevee_samples"]


def _run_background_render(job_manager, job_id, render_args):
    """Execute one queued render on the headless main-thread pump.

    The render operator is synchronous in background mode, so it can block the
    request pump while executing. Cancellation is cooperative-only there and
    may not interrupt an operation which has already entered Blender's renderer.
    """
    job = job_manager.get(job_id)
    if job["state"] != "queued":
        return

    context = _create_render_context(**render_args)
    job_manager.mark_running(job_id, progress=0.0)
    try:
        _apply_render_settings(context)
        context["started_at"] = time.perf_counter()
        bpy.ops.render.render(write_still=True)
        if job_manager.get(job_id)["cancel_requested"]:
            job_manager.mark_cancelled(job_id)
        else:
            job_manager.mark_succeeded(job_id, _render_result(context))
    except Exception as exc:
        job_manager.mark_failed(job_id, str(exc))
    finally:
        _restore_render_settings(context)


def _remove_render_handler(handler_list, callback):
    if callback in handler_list:
        handler_list.remove(callback)


def _start_interactive_render(job_manager, job_id, render_args):
    """Start an interactive render and let Blender's handlers finish the job."""
    global _active_interactive_render

    job = job_manager.get(job_id)
    if job["state"] != "queued":
        return None
    if _active_interactive_render is not None:
        return 0.1

    context = _create_render_context(**render_args)

    def cleanup():
        global _active_interactive_render
        _remove_render_handler(bpy.app.handlers.render_complete, on_complete)
        _remove_render_handler(bpy.app.handlers.render_cancel, on_cancel)
        _remove_render_handler(bpy.app.handlers.render_write, on_write)
        job_manager.clear_cancel_callback(job_id)
        _restore_render_settings(context)
        _active_interactive_render = None

    def on_write(_scene, *_args):
        try:
            if job_manager.get(job_id)["state"] == "running":
                job_manager.set_progress(job_id, 1.0)
        except Exception:
            pass

    def on_cancel(_scene, *_args):
        try:
            job_manager.mark_cancelled(job_id, "Blender render cancelled")
        finally:
            cleanup()

    def on_complete(_scene, *_args):
        try:
            if job_manager.get(job_id)["cancel_requested"]:
                job_manager.mark_cancelled(job_id, "Blender render cancelled")
            else:
                job_manager.mark_succeeded(job_id, _render_result(context))
        except Exception as exc:
            job_manager.mark_failed(job_id, str(exc))
        finally:
            cleanup()

    def request_blender_cancel():
        window = getattr(bpy.context, "window", None)
        if window is not None:
            window.event_simulate(type="ESC", value="PRESS")

    _active_interactive_render = job_id
    try:
        _apply_render_settings(context)
        context["started_at"] = time.perf_counter()
        bpy.app.handlers.render_complete.append(on_complete)
        bpy.app.handlers.render_cancel.append(on_cancel)
        bpy.app.handlers.render_write.append(on_write)
        job_manager.mark_running(job_id, progress=0.0)
        job_manager.set_cancel_callback(job_id, request_blender_cancel)
        outcome = bpy.ops.render.render("INVOKE_DEFAULT", write_still=True)
        if "CANCELLED" in outcome and job_manager.get(job_id)["state"] == "running":
            job_manager.mark_failed(job_id, "Blender could not start the render")
            cleanup()
        elif "FINISHED" in outcome and job_manager.get(job_id)["state"] == "running":
            on_complete(context["scene"])
    except Exception as exc:
        if job_manager.get(job_id)["state"] in {"queued", "running"}:
            job_manager.mark_failed(job_id, str(exc))
        cleanup()
    return None


def render_image(engine: str = None, samples: int = None,
                 resolution: list = None, format: str = "PNG",
                 save_to: str = None, thumbnail_size: int = 256,
                 async_mode: bool = False, _job_manager=None, **kwargs) -> dict:
    """Render the scene.

    If save_to is provided, saves full render to that path and returns the path (no base64).
    Otherwise saves full-res to a temp file and returns a small JPEG thumbnail as base64
    plus the temp file path for the full render.

    With ``async_mode=True`` (or the wire-level ``async`` alias), return a queued
    job immediately. Interactive Blender starts it through ``INVOKE_DEFAULT``;
    background Blender runs it in a later main-thread pump slice. The background
    render itself can block that pump, and cancellation after it starts is
    cooperative-only and may not interrupt Blender's renderer.

    Synchronous behavior is unchanged. EEVEE Next may require a GPU in background
    mode; Cycles with its CPU device is the safe fallback for headless rendering.
    """
    if "async" in kwargs:
        alias_value = kwargs.pop("async")
        if async_mode not in (False, alias_value):
            raise ValueError("async and async_mode must not conflict")
        async_mode = alias_value
    if kwargs:
        key = next(iter(kwargs))
        raise TypeError(f"render_image() got an unexpected keyword argument '{key}'")
    if not isinstance(async_mode, bool):
        raise ValueError("async_mode must be a boolean")

    render_args = {
        "engine": engine,
        "samples": samples,
        "resolution": resolution,
        "format": format,
        "save_to": save_to,
        "thumbnail_size": thumbnail_size,
    }

    if async_mode:
        if _job_manager is None:
            raise RuntimeError("Async render requires a job manager")
        job = _job_manager.create("render_image", render_args)
        job_id = job["id"]
        if bpy.app.background:
            _job_manager.enqueue(
                job_id,
                lambda: _run_background_render(_job_manager, job_id, render_args),
            )
        else:
            bpy.app.timers.register(
                lambda: _start_interactive_render(_job_manager, job_id, render_args),
                first_interval=0.0,
            )
        return {"job_id": job_id, "state": "queued"}

    context = _create_render_context(**render_args)
    try:
        _apply_render_settings(context)
        context["started_at"] = time.perf_counter()
        bpy.ops.render.render(write_still=True)
        return _render_result(context)
    finally:
        _restore_render_settings(context)

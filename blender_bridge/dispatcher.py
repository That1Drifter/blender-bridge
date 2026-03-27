# Blender Bridge — Command Dispatcher

import time
import traceback

from .constants import (
    PROTOCOL_VERSION, ERR_UNKNOWN_COMMAND, ERR_EXECUTION_ERROR,
    ERR_OBJECT_NOT_FOUND, ERR_INVALID_PARAMS,
    DEFAULT_INCLUDE_DIFF, DEFAULT_INCLUDE_SCREENSHOT, DEFAULT_SCREENSHOT_SIZE,
)
from .protocol import make_response, make_error_response
from .executor import (
    execute_code, execute_code_safe, execute_operations, SandboxViolation,
    add_object, delete_object, set_transform, set_parent, duplicate_object,
    set_visibility, create_material, set_material, add_modifier, remove_modifier,
    set_render_settings as exec_set_render_settings,
    frame_camera, apply_modifier, join_objects, export_scene, set_world,
    set_light, set_camera, select_objects, set_keyframe, create_mesh, batch_transform,
    add_shader_node, connect_shader_nodes, remove_shader_node,
    create_collection, move_to_collection, set_collection_visibility,
    edit_mesh, set_scene,
    # Phase 5: Game asset & 3D printing tools
    uv_unwrap, boolean_operation, set_scene_units, set_dimensions,
    fix_mesh, load_texture, create_pbr_material, generate_lods,
    add_mirror, array_pattern, add_constraint, remove_constraint,
    # Phase 6: Poly Haven
    polyhaven_search, polyhaven_download,
)
from . import introspection
from .capture import viewport_screenshot, render_image
from .checkpoint import CheckpointManager
from .history import CommandHistory


# Commands that mutate the scene and should trigger diff computation
_MUTATING_COMMANDS = {
    "execute_code", "batch", "execute_operations",
    "add_object", "delete_object", "set_transform", "set_parent",
    "duplicate_object", "set_visibility", "create_material", "set_material",
    "add_modifier", "remove_modifier", "set_render_settings",
    "frame_camera", "apply_modifier", "join_objects", "set_world",
    "set_light", "set_camera", "select_objects", "set_keyframe",
    "create_mesh", "batch_transform",
    "add_shader_node", "connect_shader_nodes", "remove_shader_node",
    "create_collection", "move_to_collection", "set_collection_visibility",
    "edit_mesh", "set_scene",
    # Phase 5
    "uv_unwrap", "boolean_operation", "set_scene_units", "set_dimensions",
    "fix_mesh", "load_texture", "create_pbr_material", "generate_lods",
    "add_mirror", "array_pattern", "add_constraint", "remove_constraint",
    # Phase 6
    "polyhaven_download",
}


class Dispatcher:
    def __init__(self):
        self.defaults = {
            "include_diff": DEFAULT_INCLUDE_DIFF,
            "include_screenshot": DEFAULT_INCLUDE_SCREENSHOT,
            "screenshot_size": DEFAULT_SCREENSHOT_SIZE,
        }
        self._command_count = 0
        self._handlers = {}
        self._checkpoint_mgr = CheckpointManager()
        self._history = CommandHistory()

        # Core handlers
        self._handlers["ping"] = self._handle_ping
        self._handlers["execute_code"] = self._handle_execute_code
        self._handlers["get_capabilities"] = self._handle_get_capabilities
        self._handlers["set_defaults"] = self._handle_set_defaults

        # Introspection handlers
        self._handlers["get_scene_info"] = introspection.get_scene_info
        self._handlers["get_object_info"] = introspection.get_object_info
        self._handlers["get_material_info"] = introspection.get_material_info
        self._handlers["get_world_info"] = introspection.get_world_info
        self._handlers["get_render_settings"] = introspection.get_render_settings
        self._handlers["list_objects"] = introspection.list_objects

        # Capture handlers
        self._handlers["get_viewport_screenshot"] = viewport_screenshot
        self._handlers["render_image"] = render_image

        # Checkpoint handlers
        self._handlers["create_checkpoint"] = self._checkpoint_mgr.create
        self._handlers["list_checkpoints"] = self._checkpoint_mgr.list_checkpoints
        self._handlers["restore_checkpoint"] = self._checkpoint_mgr.restore

        # History handlers
        self._handlers["get_history"] = self._history.get_history

        # Execution handlers
        self._handlers["execute_operations"] = execute_operations

        # Structured manipulation handlers
        self._handlers["add_object"] = add_object
        self._handlers["delete_object"] = delete_object
        self._handlers["set_transform"] = set_transform
        self._handlers["set_parent"] = set_parent
        self._handlers["duplicate_object"] = duplicate_object
        self._handlers["set_visibility"] = set_visibility
        self._handlers["create_material"] = create_material
        self._handlers["set_material"] = set_material
        self._handlers["add_modifier"] = add_modifier
        self._handlers["remove_modifier"] = remove_modifier
        self._handlers["set_render_settings"] = exec_set_render_settings
        self._handlers["frame_camera"] = frame_camera
        self._handlers["apply_modifier"] = apply_modifier
        self._handlers["join_objects"] = join_objects
        self._handlers["export_scene"] = export_scene
        self._handlers["set_world"] = set_world
        self._handlers["get_object_bounds"] = introspection.get_object_bounds
        self._handlers["set_light"] = set_light
        self._handlers["set_camera"] = set_camera
        self._handlers["select_objects"] = select_objects
        self._handlers["set_keyframe"] = set_keyframe
        self._handlers["create_mesh"] = create_mesh
        self._handlers["batch_transform"] = batch_transform
        self._handlers["add_shader_node"] = add_shader_node
        self._handlers["connect_shader_nodes"] = connect_shader_nodes
        self._handlers["remove_shader_node"] = remove_shader_node
        self._handlers["create_collection"] = create_collection
        self._handlers["move_to_collection"] = move_to_collection
        self._handlers["set_collection_visibility"] = set_collection_visibility
        self._handlers["edit_mesh"] = edit_mesh
        self._handlers["set_scene"] = set_scene

        # Phase 5: Game asset & 3D printing tools
        self._handlers["uv_unwrap"] = uv_unwrap
        self._handlers["boolean_operation"] = boolean_operation
        self._handlers["set_scene_units"] = set_scene_units
        self._handlers["set_dimensions"] = set_dimensions
        self._handlers["fix_mesh"] = fix_mesh
        self._handlers["load_texture"] = load_texture
        self._handlers["create_pbr_material"] = create_pbr_material
        self._handlers["generate_lods"] = generate_lods
        self._handlers["add_mirror"] = add_mirror
        self._handlers["array_pattern"] = array_pattern
        self._handlers["add_constraint"] = add_constraint
        self._handlers["remove_constraint"] = remove_constraint

        # Phase 5: Read-only handlers
        self._handlers["validate_mesh"] = introspection.validate_mesh
        self._handlers["get_dimensions"] = introspection.get_dimensions

        # Phase 6: Poly Haven + texture queries
        self._handlers["polyhaven_search"] = polyhaven_search
        self._handlers["polyhaven_download"] = polyhaven_download
        self._handlers["get_textures"] = introspection.get_textures

    def register_handler(self, command_type: str, handler):
        """Register a command handler. Used by other modules to extend the dispatcher."""
        self._handlers[command_type] = handler

    def register_handlers(self, handlers: dict):
        """Register multiple handlers at once."""
        self._handlers.update(handlers)

    def dispatch(self, request: dict) -> dict:
        """Route a validated request to its handler and wrap with auto-feedback."""
        request_id = request.get("id")
        cmd_type = request.get("type")
        params = request.get("params", {})
        opts = {**self.defaults, **(request.get("options") or {})}

        # Capture scene snapshot before execution (for diff)
        want_diff = opts.get("include_diff", False) and cmd_type in _MUTATING_COMMANDS
        before_snapshot = introspection.capture_snapshot() if want_diff else None

        t0 = time.perf_counter()

        # Handle batch commands
        if cmd_type == "batch":
            result = self._handle_batch(request, opts)
        else:
            result = self._execute_single(cmd_type, params)

        elapsed_ms = int((time.perf_counter() - t0) * 1000)

        # Compute diff from before/after snapshots
        diff = None
        if before_snapshot is not None and result["status"] == "success":
            after_snapshot = introspection.capture_snapshot()
            diff = introspection.compute_diff(before_snapshot, after_snapshot)

        # Capture screenshot if requested
        screenshot = None
        if opts.get("include_screenshot", False) and result["status"] == "success":
            try:
                shot = viewport_screenshot(
                    max_size=opts.get("screenshot_size", DEFAULT_SCREENSHOT_SIZE)
                )
                screenshot = shot.get("base64")
            except Exception as e:
                print(f"[MCP] Screenshot capture failed: {e}")

        self._command_count += 1

        # Track undo steps for checkpoint manager
        if cmd_type in _MUTATING_COMMANDS and result["status"] == "success":
            self._checkpoint_mgr.increment_steps()

        # Log to history
        hist_index = self._history.log(cmd_type, params, result["status"], elapsed_ms)

        if result["status"] == "error":
            resp = result
            resp["v"] = PROTOCOL_VERSION
            resp["id"] = request_id
            resp["timing_ms"] = elapsed_ms
            resp["history_index"] = hist_index
            return resp

        return make_response(
            request_id=request_id,
            status="success",
            result=result.get("result"),
            diff=diff,
            screenshot=screenshot,
            timing_ms=elapsed_ms,
            history_index=hist_index,
        )

    def _execute_single(self, cmd_type: str, params: dict) -> dict:
        """Execute a single command via handler lookup."""
        handler = self._handlers.get(cmd_type)
        if not handler:
            return {
                "status": "error",
                "error": {"code": ERR_UNKNOWN_COMMAND, "message": f"Unknown command: {cmd_type}"},
            }
        try:
            result = handler(**params)
            return {"status": "success", "result": result}
        except TypeError as e:
            return {
                "status": "error",
                "error": {"code": ERR_INVALID_PARAMS, "message": str(e)},
            }
        except KeyError as e:
            return {
                "status": "error",
                "error": {"code": ERR_OBJECT_NOT_FOUND, "message": str(e)},
            }
        except ValueError as e:
            return {
                "status": "error",
                "error": {"code": ERR_INVALID_PARAMS, "message": str(e)},
            }
        except SandboxViolation as e:
            return {
                "status": "error",
                "error": {"code": "SANDBOX_VIOLATION", "message": str(e)},
            }
        except Exception as e:
            traceback.print_exc()
            return {
                "status": "error",
                "error": {"code": ERR_EXECUTION_ERROR, "message": str(e)},
            }

    def _handle_batch(self, request: dict, opts: dict) -> dict:
        """Execute a batch of commands sequentially."""
        commands = request.get("commands", [])
        stop_on_error = opts.get("stop_on_error", True)
        results = []

        for cmd in commands:
            r = self._execute_single(cmd.get("type", ""), cmd.get("params", {}))
            results.append(r)
            if r["status"] == "error" and stop_on_error:
                break

        all_ok = all(r["status"] == "success" for r in results)
        return {
            "status": "success" if all_ok else "error",
            "result": {"results": results},
        }

    # --- Built-in handlers ---

    def _handle_ping(self):
        return "pong"

    def _handle_execute_code(self, code: str, mode: str = "exec"):
        if mode == "safe":
            return execute_code_safe(code, history_index=self._command_count)
        elif mode == "exec":
            return execute_code(code, history_index=self._command_count)
        else:
            raise ValueError(f"Unsupported execution mode: {mode}. Use 'exec' or 'safe'.")

    def _handle_get_capabilities(self):
        return {
            "protocol_version": PROTOCOL_VERSION,
            "commands": sorted(self._handlers.keys()),
            "defaults": self.defaults,
        }

    def _handle_set_defaults(self, **kwargs):
        for key in ("include_diff", "include_screenshot", "screenshot_size"):
            if key in kwargs:
                self.defaults[key] = kwargs[key]
        return self.defaults

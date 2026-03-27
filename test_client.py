# Test client for Blender MCP v2
# Run this AFTER connecting the addon in Blender's sidebar panel.
#
# Usage: python test_client.py

import socket
import struct
import json
import sys
import time

HOST = "localhost"
PORT = 9876
HEADER_SIZE = 4


def encode(obj):
    payload = json.dumps(obj, separators=(",", ":")).encode("utf-8")
    return struct.pack(">I", len(payload)) + payload


def recv_message(sock):
    # Read header
    header = b""
    while len(header) < HEADER_SIZE:
        chunk = sock.recv(HEADER_SIZE - len(header))
        if not chunk:
            raise ConnectionError("Connection closed while reading header")
        header += chunk

    length = struct.unpack(">I", header)[0]

    # Read payload
    payload = b""
    while len(payload) < length:
        chunk = sock.recv(length - len(payload))
        if not chunk:
            raise ConnectionError("Connection closed while reading payload")
        payload += chunk

    return json.loads(payload.decode("utf-8"))


def send_and_recv(sock, request):
    print(f"\n>>> Sending: {request['type']}")
    sock.sendall(encode(request))
    response = recv_message(sock)
    print(f"<<< Status: {response.get('status')}")
    print(f"    Result: {json.dumps(response.get('result'), indent=2)[:500]}")
    if response.get("error"):
        print(f"    Error: {response['error']}")
    if response.get("timing_ms"):
        print(f"    Time: {response['timing_ms']}ms")
    return response


def main():
    port = int(sys.argv[1]) if len(sys.argv) > 1 else PORT

    print(f"Connecting to {HOST}:{port}...")
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.connect((HOST, port))
        print("Connected!\n")

        # Test 1: Ping
        print("=" * 40)
        print("TEST 1: Ping")
        r = send_and_recv(sock, {"v": 1, "id": "t1", "type": "ping", "params": {}})
        assert r["status"] == "success" and r["result"] == "pong", "Ping failed!"
        print("PASS")

        # Test 2: Get capabilities
        print("\n" + "=" * 40)
        print("TEST 2: Get capabilities")
        r = send_and_recv(sock, {"v": 1, "id": "t2", "type": "get_capabilities", "params": {}})
        assert r["status"] == "success", "get_capabilities failed!"
        assert "commands" in r["result"], "No commands list!"
        print(f"    Commands: {r['result']['commands']}")
        print("PASS")

        # Test 3: Execute code — list objects in scene
        print("\n" + "=" * 40)
        print("TEST 3: Execute code — list scene objects")
        r = send_and_recv(sock, {
            "v": 1, "id": "t3", "type": "execute_code",
            "params": {"code": "for obj in bpy.data.objects:\n    print(f'{obj.name} ({obj.type})')"}
        })
        assert r["status"] == "success", f"execute_code failed: {r.get('error')}"
        assert r["result"]["executed"] is True, "Code didn't execute!"
        print(f"    stdout: {r['result']['stdout']}")
        print("PASS")

        # Test 4: Execute code — create a cube
        print("\n" + "=" * 40)
        print("TEST 4: Execute code — create a cube")
        r = send_and_recv(sock, {
            "v": 1, "id": "t4", "type": "execute_code",
            "params": {"code": "bpy.ops.mesh.primitive_cube_add(location=(3, 0, 0))\nbpy.context.active_object.name = 'TestCube'\nprint(f'Created {bpy.context.active_object.name}')"}
        })
        assert r["status"] == "success", f"Create cube failed: {r.get('error')}"
        print(f"    stdout: {r['result']['stdout']}")
        print("PASS")

        # Test 5: Execute code — verify cube exists
        print("\n" + "=" * 40)
        print("TEST 5: Verify TestCube exists")
        r = send_and_recv(sock, {
            "v": 1, "id": "t5", "type": "execute_code",
            "params": {"code": "obj = bpy.data.objects['TestCube']\nprint(f'Found: {obj.name} at {list(obj.location)}')"}
        })
        assert r["status"] == "success", f"Verify failed: {r.get('error')}"
        print(f"    stdout: {r['result']['stdout']}")
        print("PASS")

        # Test 6: Execute code — use enriched namespace (Vector, math)
        print("\n" + "=" * 40)
        print("TEST 6: Enriched namespace (Vector, math)")
        r = send_and_recv(sock, {
            "v": 1, "id": "t6", "type": "execute_code",
            "params": {"code": "v = Vector((1, 2, 3))\nprint(f'Vector length: {v.length:.4f}')\nprint(f'Pi: {math.pi:.4f}')"}
        })
        assert r["status"] == "success", f"Namespace test failed: {r.get('error')}"
        print(f"    stdout: {r['result']['stdout']}")
        print("PASS")

        # Test 7: Batch execution
        print("\n" + "=" * 40)
        print("TEST 7: Batch execution")
        r = send_and_recv(sock, {
            "v": 1, "id": "t7", "type": "batch",
            "commands": [
                {"type": "ping", "params": {}},
                {"type": "execute_code", "params": {"code": "print('batch item 2')"}},
                {"type": "ping", "params": {}},
            ],
            "options": {"stop_on_error": True}
        })
        assert r["status"] == "success", f"Batch failed: {r.get('error')}"
        results = r["result"]["results"]
        assert len(results) == 3, f"Expected 3 results, got {len(results)}"
        print(f"    Sub-results: {[x['status'] for x in results]}")
        print("PASS")

        # Test 8: Unknown command
        print("\n" + "=" * 40)
        print("TEST 8: Unknown command (should error)")
        r = send_and_recv(sock, {"v": 1, "id": "t8", "type": "nonexistent_cmd", "params": {}})
        assert r["status"] == "error", "Should have errored!"
        print(f"    Error code: {r['error']['code']}")
        print("PASS")

        # Test 9: Bad code (should error)
        print("\n" + "=" * 40)
        print("TEST 9: Bad code execution (should error)")
        r = send_and_recv(sock, {
            "v": 1, "id": "t9", "type": "execute_code",
            "params": {"code": "raise ValueError('intentional error')"}
        })
        assert r["status"] == "error", "Should have errored!"
        print(f"    Error: {r['error']['message']}")
        print("PASS")

        # Test 10: Set defaults
        print("\n" + "=" * 40)
        print("TEST 10: Set defaults")
        r = send_and_recv(sock, {
            "v": 1, "id": "t10", "type": "set_defaults",
            "params": {"include_screenshot": True, "screenshot_size": 256}
        })
        assert r["status"] == "success", f"set_defaults failed: {r.get('error')}"
        assert r["result"]["include_screenshot"] is True, "Default not updated!"
        print(f"    New defaults: {r['result']}")
        print("PASS")

        # =============================================
        # Phase 2 Tests: Introspection + Diff
        # =============================================

        # Test 11: get_scene_info
        print("\n" + "=" * 40)
        print("TEST 11: get_scene_info")
        r = send_and_recv(sock, {"v": 1, "id": "t11", "type": "get_scene_info", "params": {}})
        assert r["status"] == "success", f"get_scene_info failed: {r.get('error')}"
        result = r["result"]
        assert "objects" in result, "No objects in scene info!"
        assert "collections" in result, "No collections in scene info!"
        assert result["object_count"] > 0, "No objects in scene!"
        print(f"    Scene: {result['scene_name']}, {result['object_count']} objects, engine: {result['render_engine']}")
        for obj in result["objects"]:
            print(f"      - {obj['name']} ({obj['type']}) at {obj['location']}")
        print("PASS")

        # Test 12: get_object_info (on default Cube or first mesh)
        print("\n" + "=" * 40)
        print("TEST 12: get_object_info")
        # Find a mesh object name from scene info
        mesh_name = None
        for obj in result["objects"]:
            if obj["type"] == "MESH":
                mesh_name = obj["name"]
                break
        if mesh_name:
            r = send_and_recv(sock, {
                "v": 1, "id": "t12", "type": "get_object_info",
                "params": {"name": mesh_name}
            })
            assert r["status"] == "success", f"get_object_info failed: {r.get('error')}"
            obj_info = r["result"]
            assert obj_info["name"] == mesh_name
            assert "mesh" in obj_info, "No mesh data!"
            assert "modifiers" in obj_info, "No modifiers list!"
            assert "materials" in obj_info, "No materials list!"
            print(f"    Object: {obj_info['name']}")
            print(f"    Mesh: {obj_info['mesh']}")
            print(f"    Modifiers: {obj_info['modifiers']}")
            print(f"    Materials: {obj_info['materials']}")
            print(f"    Bounding box: {obj_info.get('bounding_box')}")
            print("PASS")
        else:
            print("    SKIP (no mesh objects in scene)")

        # Test 13: get_object_info — nonexistent object (should error)
        print("\n" + "=" * 40)
        print("TEST 13: get_object_info — nonexistent (should error)")
        r = send_and_recv(sock, {
            "v": 1, "id": "t13", "type": "get_object_info",
            "params": {"name": "NonExistentObject_XYZ"}
        })
        assert r["status"] == "error", "Should have errored for missing object!"
        print(f"    Error: {r['error']['message']}")
        print("PASS")

        # Test 14: get_render_settings
        print("\n" + "=" * 40)
        print("TEST 14: get_render_settings")
        r = send_and_recv(sock, {"v": 1, "id": "t14", "type": "get_render_settings", "params": {}})
        assert r["status"] == "success", f"get_render_settings failed: {r.get('error')}"
        print(f"    Render: {r['result']}")
        print("PASS")

        # Test 15: get_world_info
        print("\n" + "=" * 40)
        print("TEST 15: get_world_info")
        r = send_and_recv(sock, {"v": 1, "id": "t15", "type": "get_world_info", "params": {}})
        assert r["status"] == "success", f"get_world_info failed: {r.get('error')}"
        print(f"    World: {r['result']}")
        print("PASS")

        # Test 16: list_objects with type filter
        print("\n" + "=" * 40)
        print("TEST 16: list_objects (type_filter=MESH)")
        r = send_and_recv(sock, {
            "v": 1, "id": "t16", "type": "list_objects",
            "params": {"type_filter": "MESH"}
        })
        assert r["status"] == "success", f"list_objects failed: {r.get('error')}"
        print(f"    Found {r['result']['total']} mesh objects: {[o['name'] for o in r['result']['objects']]}")
        print("PASS")

        # Test 17: Diff — create object and check diff in response
        print("\n" + "=" * 40)
        print("TEST 17: Scene diff — create object")
        r = send_and_recv(sock, {
            "v": 1, "id": "t17", "type": "execute_code",
            "params": {"code": "bpy.ops.mesh.primitive_uv_sphere_add(location=(5, 0, 0))\nbpy.context.active_object.name = 'DiffTestSphere'"},
            "options": {"include_diff": True}
        })
        assert r["status"] == "success", f"Create sphere failed: {r.get('error')}"
        diff = r.get("diff")
        print(f"    Diff: {json.dumps(diff, indent=2)}")
        assert diff is not None, "Expected a diff but got None!"
        assert "DiffTestSphere" in diff.get("objects_added", []), "Sphere not in objects_added!"
        print("PASS")

        # Test 18: Diff — modify object and check diff
        print("\n" + "=" * 40)
        print("TEST 18: Scene diff — move object")
        r = send_and_recv(sock, {
            "v": 1, "id": "t18", "type": "execute_code",
            "params": {"code": "bpy.data.objects['DiffTestSphere'].location = (10, 5, 2)"},
            "options": {"include_diff": True}
        })
        assert r["status"] == "success", f"Move failed: {r.get('error')}"
        diff = r.get("diff")
        print(f"    Diff: {json.dumps(diff, indent=2)}")
        assert diff is not None, "Expected a diff but got None!"
        assert "DiffTestSphere" in diff.get("objects_modified", {}), "Sphere not in objects_modified!"
        mod = diff["objects_modified"]["DiffTestSphere"]
        assert "location" in mod, "Location change not detected!"
        print("PASS")

        # Test 19: Diff — delete object and check diff
        print("\n" + "=" * 40)
        print("TEST 19: Scene diff — delete object")
        r = send_and_recv(sock, {
            "v": 1, "id": "t19", "type": "execute_code",
            "params": {"code": "obj = bpy.data.objects['DiffTestSphere']\nbpy.data.objects.remove(obj, do_unlink=True)"},
            "options": {"include_diff": True}
        })
        assert r["status"] == "success", f"Delete failed: {r.get('error')}"
        diff = r.get("diff")
        print(f"    Diff: {json.dumps(diff, indent=2)}")
        assert diff is not None, "Expected a diff but got None!"
        assert "DiffTestSphere" in diff.get("objects_removed", []), "Sphere not in objects_removed!"
        print("PASS")

        # Test 20: Diff — no changes = null diff
        print("\n" + "=" * 40)
        print("TEST 20: Scene diff — no changes = null diff")
        r = send_and_recv(sock, {
            "v": 1, "id": "t20", "type": "execute_code",
            "params": {"code": "x = 1 + 1"},
            "options": {"include_diff": True}
        })
        assert r["status"] == "success", f"No-op failed: {r.get('error')}"
        diff = r.get("diff")
        print(f"    Diff: {diff}")
        assert diff is None, f"Expected null diff for no-op, got: {diff}"
        print("PASS")

        # Test 21: get_material_info (if materials exist)
        print("\n" + "=" * 40)
        print("TEST 21: get_material_info")
        # First check if any materials exist
        r = send_and_recv(sock, {"v": 1, "id": "t21a", "type": "get_scene_info", "params": {}})
        if r["result"]["materials_count"] > 0:
            # Get first material name via execute_code
            r2 = send_and_recv(sock, {
                "v": 1, "id": "t21b", "type": "execute_code",
                "params": {"code": "print(bpy.data.materials[0].name)"}
            })
            mat_name = r2["result"]["stdout"].strip()
            r3 = send_and_recv(sock, {
                "v": 1, "id": "t21c", "type": "get_material_info",
                "params": {"name": mat_name}
            })
            assert r3["status"] == "success", f"get_material_info failed: {r3.get('error')}"
            print(f"    Material: {r3['result']['name']}, use_nodes: {r3['result']['use_nodes']}")
            if r3['result'].get('nodes'):
                print(f"    Nodes: {[n['name'] for n in r3['result']['nodes']]}")
            print("PASS")
        else:
            print("    SKIP (no materials in scene)")

        # =============================================
        # Phase 3 Tests: Capture + Checkpoints + History
        # =============================================

        # Test 22: Viewport screenshot
        print("\n" + "=" * 40)
        print("TEST 22: get_viewport_screenshot")
        r = send_and_recv(sock, {
            "v": 1, "id": "t22", "type": "get_viewport_screenshot",
            "params": {"max_size": 256, "format": "PNG"}
        })
        assert r["status"] == "success", f"Screenshot failed: {r.get('error')}"
        shot = r["result"]
        assert "base64" in shot, "No base64 in screenshot!"
        assert shot["width"] > 0 and shot["height"] > 0, "Invalid dimensions!"
        b64_len = len(shot["base64"])
        print(f"    Size: {shot['width']}x{shot['height']}, format: {shot['format']}, base64 length: {b64_len}")
        assert b64_len > 100, "Base64 data suspiciously small!"
        print("PASS")

        # Test 23: Auto-screenshot in response (via options)
        print("\n" + "=" * 40)
        print("TEST 23: Auto-screenshot in execute_code response")
        r = send_and_recv(sock, {
            "v": 1, "id": "t23", "type": "execute_code",
            "params": {"code": "print('screenshot test')"},
            "options": {"include_screenshot": True, "screenshot_size": 256}
        })
        assert r["status"] == "success", f"Failed: {r.get('error')}"
        assert r.get("screenshot") is not None, "Expected screenshot in response!"
        print(f"    Screenshot base64 length: {len(r['screenshot'])}")
        print("PASS")

        # Test 24: Render image
        print("\n" + "=" * 40)
        print("TEST 24: render_image (Eevee, small)")
        r = send_and_recv(sock, {
            "v": 1, "id": "t24", "type": "render_image",
            "params": {"resolution": [128, 128], "format": "PNG"}
        })
        assert r["status"] == "success", f"Render failed: {r.get('error')}"
        rend = r["result"]
        assert "base64" in rend, "No base64 in render!"
        assert "render_time_ms" in rend, "No render time!"
        print(f"    Rendered {rend['width']}x{rend['height']} in {rend['render_time_ms']}ms, engine: {rend['engine']}")
        print("PASS")

        # Test 25: Create checkpoint
        print("\n" + "=" * 40)
        print("TEST 25: create_checkpoint")
        r = send_and_recv(sock, {
            "v": 1, "id": "t25", "type": "create_checkpoint",
            "params": {"label": "before_test_changes"}
        })
        assert r["status"] == "success", f"Create checkpoint failed: {r.get('error')}"
        cp_id = r["result"]["checkpoint_id"]
        print(f"    Checkpoint: {r['result']}")
        print("PASS")

        # Test 26: Make changes after checkpoint
        print("\n" + "=" * 40)
        print("TEST 26: Make changes after checkpoint")
        r = send_and_recv(sock, {
            "v": 1, "id": "t26a", "type": "execute_code",
            "params": {"code": "bpy.ops.mesh.primitive_cone_add(location=(7, 0, 0))\nbpy.context.active_object.name = 'CheckpointCone'"},
            "options": {"include_diff": True}
        })
        assert r["status"] == "success", f"Add cone failed: {r.get('error')}"
        assert "CheckpointCone" in (r.get("diff") or {}).get("objects_added", []), "Cone not in diff!"
        # Verify it exists
        r = send_and_recv(sock, {
            "v": 1, "id": "t26b", "type": "execute_code",
            "params": {"code": "print('CheckpointCone' in bpy.data.objects)"}
        })
        assert "True" in r["result"]["stdout"], "Cone doesn't exist!"
        print("    Created CheckpointCone, verified it exists")
        print("PASS")

        # Test 27: List checkpoints
        print("\n" + "=" * 40)
        print("TEST 27: list_checkpoints")
        r = send_and_recv(sock, {"v": 1, "id": "t27", "type": "list_checkpoints", "params": {}})
        assert r["status"] == "success", f"List checkpoints failed: {r.get('error')}"
        cps = r["result"]["checkpoints"]
        assert len(cps) >= 1, "Expected at least 1 checkpoint!"
        print(f"    Checkpoints: {cps}")
        print("PASS")

        # Test 28: Restore checkpoint (should undo the cone)
        print("\n" + "=" * 40)
        print("TEST 28: restore_checkpoint")
        r = send_and_recv(sock, {
            "v": 1, "id": "t28", "type": "restore_checkpoint",
            "params": {"checkpoint_id": cp_id}
        })
        assert r["status"] == "success", f"Restore failed: {r.get('error')}"
        print(f"    Restore result: {r['result']}")
        assert r["result"]["undone_steps"] > 0, "Expected at least 1 undo step!"
        # Verify cone is gone
        r = send_and_recv(sock, {
            "v": 1, "id": "t28b", "type": "execute_code",
            "params": {"code": "print('CheckpointCone' in bpy.data.objects)"}
        })
        assert "False" in r["result"]["stdout"], "Cone still exists after restore!"
        print("    CheckpointCone successfully undone")
        print("PASS")

        # Test 29: Get history
        print("\n" + "=" * 40)
        print("TEST 29: get_history")
        r = send_and_recv(sock, {
            "v": 1, "id": "t29", "type": "get_history",
            "params": {"limit": 5}
        })
        assert r["status"] == "success", f"get_history failed: {r.get('error')}"
        hist = r["result"]
        assert hist["total"] > 0, "Expected history entries!"
        print(f"    Total entries: {hist['total']}, showing {len(hist['entries'])}:")
        for e in hist["entries"]:
            print(f"      #{e['index']} {e['type']} — {e['status']} ({e['timing_ms']}ms)")
        print("PASS")

        # Test 30: history_index in responses
        print("\n" + "=" * 40)
        print("TEST 30: history_index present in responses")
        r = send_and_recv(sock, {"v": 1, "id": "t30", "type": "ping", "params": {}})
        assert "history_index" in r, "No history_index in response!"
        assert r["history_index"] > 0, "history_index should be positive!"
        print(f"    history_index: {r['history_index']}")
        print("PASS")

        # =============================================
        # Phase 4 Tests: Safe Mode + Structured Commands
        # =============================================

        # Test 31: Safe mode — normal code works
        print("\n" + "=" * 40)
        print("TEST 31: Safe mode — normal bpy code")
        r = send_and_recv(sock, {
            "v": 1, "id": "t31", "type": "execute_code",
            "params": {"code": "print(len(bpy.data.objects))", "mode": "safe"}
        })
        assert r["status"] == "success", f"Safe mode normal code failed: {r.get('error')}"
        assert r["result"]["mode"] == "safe", "Mode not reported as safe!"
        print(f"    stdout: {r['result']['stdout']}")
        print("PASS")

        # Test 32: Safe mode — blocks import os
        print("\n" + "=" * 40)
        print("TEST 32: Safe mode — blocks import os")
        r = send_and_recv(sock, {
            "v": 1, "id": "t32", "type": "execute_code",
            "params": {"code": "import os\nos.listdir('.')", "mode": "safe"}
        })
        assert r["status"] == "error", "Should have blocked import os!"
        assert "SANDBOX_VIOLATION" in str(r.get("error", {})), f"Expected SANDBOX_VIOLATION, got: {r['error']}"
        print(f"    Blocked: {r['error']['message']}")
        print("PASS")

        # Test 33: Safe mode — blocks from subprocess import
        print("\n" + "=" * 40)
        print("TEST 33: Safe mode — blocks from subprocess import")
        r = send_and_recv(sock, {
            "v": 1, "id": "t33", "type": "execute_code",
            "params": {"code": "from subprocess import call\ncall(['echo', 'pwned'])", "mode": "safe"}
        })
        assert r["status"] == "error", "Should have blocked subprocess!"
        assert "SANDBOX_VIOLATION" in str(r.get("error", {}))
        print(f"    Blocked: {r['error']['message']}")
        print("PASS")

        # Test 34: Safe mode — blocks open()
        print("\n" + "=" * 40)
        print("TEST 34: Safe mode — blocks open()")
        r = send_and_recv(sock, {
            "v": 1, "id": "t34", "type": "execute_code",
            "params": {"code": "f = open('C:/test.txt', 'w')", "mode": "safe"}
        })
        assert r["status"] == "error", "Should have blocked open()!"
        print(f"    Blocked: {r['error']['message']}")
        print("PASS")

        # Test 35: add_object
        print("\n" + "=" * 40)
        print("TEST 35: add_object (cube)")
        r = send_and_recv(sock, {
            "v": 1, "id": "t35", "type": "add_object",
            "params": {"type": "CUBE", "name": "StructCube", "location": [4, 0, 0]},
            "options": {"include_diff": True}
        })
        assert r["status"] == "success", f"add_object failed: {r.get('error')}"
        assert r["result"]["name"] == "StructCube"
        diff = r.get("diff")
        assert diff and "StructCube" in diff.get("objects_added", [])
        print(f"    Created: {r['result']}")
        print("PASS")

        # Test 36: set_transform
        print("\n" + "=" * 40)
        print("TEST 36: set_transform")
        r = send_and_recv(sock, {
            "v": 1, "id": "t36", "type": "set_transform",
            "params": {"name": "StructCube", "location": [0, 5, 0], "scale": [2, 2, 2]},
            "options": {"include_diff": True}
        })
        assert r["status"] == "success", f"set_transform failed: {r.get('error')}"
        assert r["result"]["location"] == [0.0, 5.0, 0.0]
        assert r["result"]["scale"] == [2.0, 2.0, 2.0]
        print(f"    Transform: {r['result']}")
        print("PASS")

        # Test 37: create_material + set_material
        print("\n" + "=" * 40)
        print("TEST 37: create_material + set_material")
        r = send_and_recv(sock, {
            "v": 1, "id": "t37a", "type": "create_material",
            "params": {"name": "TestRed", "base_color": [1, 0, 0, 1], "metallic": 0.5, "roughness": 0.3}
        })
        assert r["status"] == "success", f"create_material failed: {r.get('error')}"
        print(f"    Material created: {r['result']}")
        r = send_and_recv(sock, {
            "v": 1, "id": "t37b", "type": "set_material",
            "params": {"object": "StructCube", "material": "TestRed"}
        })
        assert r["status"] == "success", f"set_material failed: {r.get('error')}"
        print(f"    Material assigned: {r['result']}")
        print("PASS")

        # Test 38: add_modifier
        print("\n" + "=" * 40)
        print("TEST 38: add_modifier (SUBSURF)")
        r = send_and_recv(sock, {
            "v": 1, "id": "t38", "type": "add_modifier",
            "params": {"object": "StructCube", "type": "SUBSURF", "name": "SubD", "levels": 2}
        })
        assert r["status"] == "success", f"add_modifier failed: {r.get('error')}"
        print(f"    Modifier: {r['result']}")
        print("PASS")

        # Test 39: duplicate_object
        print("\n" + "=" * 40)
        print("TEST 39: duplicate_object")
        r = send_and_recv(sock, {
            "v": 1, "id": "t39", "type": "duplicate_object",
            "params": {"name": "StructCube", "new_name": "StructCubeCopy"},
            "options": {"include_diff": True}
        })
        assert r["status"] == "success", f"duplicate failed: {r.get('error')}"
        print(f"    Duplicated: {r['result']}")
        print("PASS")

        # Test 40: set_visibility
        print("\n" + "=" * 40)
        print("TEST 40: set_visibility")
        r = send_and_recv(sock, {
            "v": 1, "id": "t40", "type": "set_visibility",
            "params": {"name": "StructCubeCopy", "visible": False, "render_visible": False}
        })
        assert r["status"] == "success", f"set_visibility failed: {r.get('error')}"
        assert r["result"]["visible"] is False
        assert r["result"]["render_visible"] is False
        print(f"    Visibility: {r['result']}")
        print("PASS")

        # Test 41: remove_modifier
        print("\n" + "=" * 40)
        print("TEST 41: remove_modifier")
        r = send_and_recv(sock, {
            "v": 1, "id": "t41", "type": "remove_modifier",
            "params": {"object": "StructCube", "modifier": "SubD"}
        })
        assert r["status"] == "success", f"remove_modifier failed: {r.get('error')}"
        print(f"    Removed: {r['result']}")
        print("PASS")

        # Test 42: execute_operations (whitelist)
        print("\n" + "=" * 40)
        print("TEST 42: execute_operations (whitelist)")
        r = send_and_recv(sock, {
            "v": 1, "id": "t42", "type": "execute_operations",
            "params": {"operations": [
                {"op": "add_sphere", "args": {"location": [0, 0, 5]}},
                {"op": "add_cone", "args": {"location": [0, 0, -5]}},
            ]}
        })
        assert r["status"] == "success", f"execute_operations failed: {r.get('error')}"
        results = r["result"]["results"]
        assert all(x["status"] == "success" for x in results), f"Some ops failed: {results}"
        print(f"    Results: {results}")
        print("PASS")

        # Test 43: delete_object
        print("\n" + "=" * 40)
        print("TEST 43: delete_object")
        r = send_and_recv(sock, {
            "v": 1, "id": "t43", "type": "delete_object",
            "params": {"name": "StructCubeCopy"}
        })
        assert r["status"] == "success", f"delete_object failed: {r.get('error')}"
        print(f"    Deleted: {r['result']}")
        print("PASS")

        # Test 44: delete_object — nonexistent (should error with OBJECT_NOT_FOUND)
        print("\n" + "=" * 40)
        print("TEST 44: delete_object — nonexistent (should error)")
        r = send_and_recv(sock, {
            "v": 1, "id": "t44", "type": "delete_object",
            "params": {"name": "NoSuchObject_XYZ"}
        })
        assert r["status"] == "error", "Should have errored!"
        assert r["error"]["code"] == "OBJECT_NOT_FOUND", f"Expected OBJECT_NOT_FOUND, got: {r['error']['code']}"
        print(f"    Error: {r['error']}")
        print("PASS")

        # Test 45: set_render_settings
        print("\n" + "=" * 40)
        print("TEST 45: set_render_settings")
        r = send_and_recv(sock, {
            "v": 1, "id": "t45", "type": "set_render_settings",
            "params": {"resolution_x": 800, "resolution_y": 600}
        })
        assert r["status"] == "success", f"set_render_settings failed: {r.get('error')}"
        print(f"    Settings: {r['result']}")
        print("PASS")

        # Cleanup
        print("\n" + "=" * 40)
        print("CLEANUP")
        for name in ["TestCube", "StructCube"]:
            send_and_recv(sock, {
                "v": 1, "id": "cleanup", "type": "execute_code",
                "params": {"code": f"obj = bpy.data.objects.get('{name}')\nif obj: bpy.data.objects.remove(obj, do_unlink=True)"}
            })
        # Clean up ops-created objects (Sphere, Cone from test 42)
        send_and_recv(sock, {
            "v": 1, "id": "cleanup2", "type": "execute_code",
            "params": {"code": "for name in ['Sphere', 'Cone']:\n    obj = bpy.data.objects.get(name)\n    if obj: bpy.data.objects.remove(obj, do_unlink=True)"}
        })
        # Clean up material
        send_and_recv(sock, {
            "v": 1, "id": "cleanup3", "type": "execute_code",
            "params": {"code": "mat = bpy.data.materials.get('TestRed')\nif mat: bpy.data.materials.remove(mat)"}
        })
        print("    Cleaned up test objects and materials")

        print("\n" + "=" * 40)
        print("ALL TESTS PASSED (Phase 1 + Phase 2 + Phase 3 + Phase 4)")
        print("=" * 40)

    except ConnectionRefusedError:
        print("ERROR: Could not connect. Is Blender running with the addon connected?")
        sys.exit(1)
    except AssertionError as e:
        print(f"\nFAILED: {e}")
        sys.exit(1)
    finally:
        sock.close()


if __name__ == "__main__":
    main()

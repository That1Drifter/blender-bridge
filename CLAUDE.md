# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Blender MCP v2 — a production assistant bridge between Claude Code and Blender 3D. **Not a modeling tool** — it handles texturing, scene setup, export pipelines, mesh QA, and asset management while the user does the actual modeling.

1. **Blender Addon** (`blender_mcp/`): Python package installed as a Blender addon. Runs a TCP server on `localhost:9876` inside Blender's process.
2. **MCP Server** (`mcp_server.py`): FastMCP wrapper that exposes Blender commands as MCP tools over stdio. Persistent TCP connection with retry.

Data flow: `Claude Code → mcp_server.py (stdio) → TCP socket → blender_mcp addon → Blender Python API (bpy)`

### Best Used For
- Texturing & materials (Poly Haven PBR, shader node setup, batch assignment)
- Scene setup (HDRI, lighting, camera, render settings)
- Export with presets (DayZ FBX, Godot GLTF, 3D print STL, LOD generation)
- Mesh validation (manifold, dimensions, printability)
- Batch operations across many objects

### Not Good For
- Freeform modeling, sculpting, retopology — use Blender's native tools for that

## Build & Deploy

There is no build system. The addon is a plain Python package distributed as a zip.

**Rebuild the addon zip after any code change:**
```bash
cd "C:/Users/Drifter/Desktop/Claude-Blender"
python -c "import zipfile, os; z=zipfile.ZipFile('blender_mcp.zip','w'); [z.write(os.path.join(r,f), os.path.join(os.path.relpath(r,'.'),f)) for r,d,fs in os.walk('blender_mcp') for f in fs if f.endswith('.py')]; z.close()"
```

After rebuilding: user reinstalls via Blender Preferences > Add-ons > Install from Disk, then toggles MCP server restart in Claude Code.

**Syntax check before zipping:**
```bash
python -c "import py_compile; import glob; [py_compile.compile(f, doraise=True) for f in glob.glob('blender_mcp/**/*.py', recursive=True)]"
```

## Testing

```bash
python test_client.py [port]    # default port: 9876
```
Requires Blender running with the addon connected. The test suite is a sequential 10-test TCP protocol exerciser — not a unit test framework.

## Architecture

### Wire Protocol (`protocol.py`)
Length-prefixed JSON over TCP. 4-byte big-endian uint32 header + UTF-8 JSON payload. Max message: 50 MB.

Request shape: `{"v": 1, "id": "...", "type": "command_name", "params": {...}, "options": {...}}`

### Thread Model
Blender is single-threaded for scene operations. The TCP server runs in a daemon thread; all Blender API calls are marshaled to the main thread via `bpy.app.timers.register()`. One client connection at a time.

### Key Modules

| Module | Role |
|--------|------|
| `__init__.py` | Addon entry point, singleton server lifecycle |
| `server.py` | TCP socket server, client handling, main-thread dispatch |
| `dispatcher.py` | Command routing, handler registry, auto-diff/checkpoint wrapping |
| `executor.py` | All mutating command implementations (~1200 lines, 30+ handlers) |
| `introspection.py` | Read-only queries + scene snapshot/diff engine |
| `capture.py` | Viewport screenshots and render capture with thumbnail generation |
| `checkpoint.py` | Undo checkpoint create/restore via Blender's undo stack |
| `history.py` | Command execution log (last 500 entries) |
| `constants.py` | Ports, protocol version, engine aliases, error codes |
| `ui.py` | Blender sidebar panel and operators |

### Command Execution Pattern
Every mutating command in `executor.py` follows the same pattern:
1. Validate params, look up objects by name
2. `bpy.ops.ed.undo_push(message=...)` before mutation
3. Perform bpy operations
4. Return result dict

The dispatcher wraps this with auto-diff (before/after scene snapshots) and history logging.

### Auto-Diff System (`introspection.py`)
`capture_snapshot()` takes a lightweight scene fingerprint. The dispatcher captures before/after snapshots for mutating commands when `include_diff: true` (default). `compute_diff()` returns added/removed/modified objects and materials.

### MCP Server (`mcp_server.py`)
Thin translation layer: each `@mcp.tool()` function maps params to a TCP request, sends it, and returns the response. No business logic lives here.

## Key Constants

- Protocol version: `1`
- Default port: `9876`
- Valid render engines: `CYCLES`, `BLENDER_EEVEE_NEXT`, `BLENDER_WORKBENCH`
- Engine aliases: `EEVEE` → `BLENDER_EEVEE_NEXT`
- Requires Blender 4.0+

## Working with the Addon

- The addon runs in **Blender's Python**, not the system Python. You cannot import bpy outside Blender.
- `mcp_server.py` runs in system Python and only needs the `mcp` package.
- `addon copy.py` is the legacy v1 single-file implementation — kept for reference, not active.
- The `integrations/` directory is a placeholder for future work (e.g., Poly Haven).

# Blender Bridge

A production assistant bridge between Claude Code and Blender. Gives Claude direct control over texturing, scene setup, export pipelines, and asset management. Built for production workflows — not modeling from scratch.

## What This Is For

You model in Blender. Claude handles the tedious parts:

- **Texturing** — Pull CC0 PBR materials from Poly Haven, build shader node trees, assign across objects
- **Scene setup** — HDRI lighting, camera positioning, render settings in one sentence instead of 20 panels
- **Export pipelines** — Correct FBX settings for DayZ, GLTF for Godot, STL for 3D printing, LOD generation
- **Mesh QA** — Validate manifold geometry, check dimensions in real units, verify printability
- **Batch operations** — Rename, transform, organize, and assign materials across many objects at once
- **Asset management** — Search and import Poly Haven HDRIs, textures, and models

## What This Is NOT For

Claude can't sculpt, retopologize, or do freeform modeling. If you need a character or hard-surface model, build it yourself (or import one), then use this tool to texture it, light it, validate it, and export it.

## Setup

### Requirements
- Blender 4.2-4.5 tested (4.5.8 LTS verified); 4.0+ expected
- Python 3.10+ (system Python, for the MCP server)
- Claude Code CLI

### Install the Blender Addon

1. Download `blender_bridge.zip` from this repo
2. In Blender: Edit > Preferences > Add-ons > Install from Disk > select the zip
3. Enable "Blender Bridge" in the addon list
4. In the 3D Viewport sidebar (N panel), find "MCP v2" and click "Start Server"

### Configure Claude Code

Add to your Claude Code settings (`~/.claude/settings.json`):

```json
{
  "mcpServers": {
    "blender-mcp": {
      "command": "python",
      "args": ["path/to/bridge_server.py"]
    }
  }
}
```

Replace `python` with your Python path and `path/to/bridge_server.py` with the actual path.

### Verify

In Claude Code, the Blender Bridge tools should appear. Test with:
> "Get the current Blender scene info"

## Example Workflows

### Texture an imported model with Poly Haven materials

```
1. Import your model into Blender manually
2. "Search Poly Haven for wood textures"
3. "Download the oak_veneer texture at 2k and apply it to MyModel"
4. "Set up a studio HDRI from Poly Haven"
5. "Render at 1080p with EEVEE"
```

### Prepare a model for 3D printing

```
1. Model or import your object
2. "Set scene units to millimeters"
3. "Set the object dimensions to 45mm tall"
4. "Validate the mesh for 3D printing"
5. "Fix mesh — make manifold"
6. "Export as STL with print preset"
```

### Export a game asset for DayZ

```
1. Model your item in Blender
2. "UV unwrap with smart project"
3. "Create a weathered metal PBR material and assign it"
4. "Generate LODs at ratios 1.0, 0.5, 0.25"
5. "Export with the DayZ preset"
```

### Set up a product shot

```
1. Import/place your model
2. "Search Poly Haven for studio HDRIs"
3. "Download blocky_photo_studio at 2k"
4. "Frame the camera on MyObject with 1.3 padding"
5. "Set camera lens to 85mm, enable DOF at f/2.8"
6. "Render at 2k with Cycles, 256 samples"
```

### Batch material assignment

```
"Create a concrete PBR material"
"Assign it to Wall_01, Wall_02, Wall_03, and Floor"
"Load the grunge texture from C:/textures/grunge_roughness.png as the roughness map on the concrete material"
```

## Tool Reference

### Scene Queries (read-only)
| Tool | Description |
|------|-------------|
| `get_scene_info` | Scene overview — objects, collections, render engine, frame range |
| `list_objects` | List objects filtered by type or collection |
| `get_object_info` | Object details — transforms, modifiers, materials, mesh stats |
| `get_object_bounds` | World-space bounding box |
| `get_dimensions` | Dimensions in scene units and millimeters |
| `get_material_info` | Shader node tree with inputs and connections |
| `get_world_info` | Environment settings |
| `get_render_settings` | Engine, resolution, samples, frame range |
| `validate_mesh` | Manifold check, volume, surface area, printability |
| `get_textures` | All image textures with path, size, colorspace |

### Materials & Textures
| Tool | Description |
|------|-------------|
| `create_material` | New material with Principled BSDF |
| `create_pbr_material` | Material from preset (weathered_metal, wood, rubber, glass, etc.) |
| `set_material` | Assign material to object |
| `load_texture` | Load image, connect to BSDF input (auto Normal Map node) |
| `add_shader_node` | Add any shader node with properties |
| `connect_shader_nodes` | Link shader node outputs to inputs |
| `remove_shader_node` | Remove a shader node |

### Poly Haven Assets (CC0, free)
| Tool | Description |
|------|-------------|
| `polyhaven_search` | Search HDRIs, textures, and 3D models |
| `polyhaven_download` | Download and import — sets up HDRI environment, creates PBR materials, or imports models |

### Mesh Operations
| Tool | Description |
|------|-------------|
| `edit_mesh` | Subdivide, bevel, inset, extrude, merge, separate |
| `boolean_operation` | Difference, union, intersect between two objects |
| `uv_unwrap` | Smart project, cube/cylinder/sphere projection, pack islands, seams |
| `fix_mesh` | Recalculate normals, fill holes, remove doubles, make manifold |
| `validate_mesh` | Check printability, manifold status, volume |
| `join_objects` | Merge multiple meshes into one |

### Modifiers & Constraints
| Tool | Description |
|------|-------------|
| `add_modifier` | Any Blender modifier with property kwargs |
| `add_mirror` | Mirror modifier with axis and bisect options |
| `array_pattern` | Linear or circular array |
| `apply_modifier` | Bake modifier into mesh |
| `add_constraint` | Track To, Copy Location, Child Of, etc. |
| `remove_modifier` / `remove_constraint` | Remove by name |

### Scene Setup
| Tool | Description |
|------|-------------|
| `set_world` | Background color, strength, HDRI path |
| `set_light` | Type, energy, color, size, shadows |
| `set_camera` | Lens, DOF, sensor, clip range |
| `frame_camera` | Auto-aim at targets with padding |
| `set_render_settings` | Engine, resolution, samples, transparency |
| `set_scene` | FPS, frame range, active camera |
| `set_scene_units` | Metric/imperial, mm/cm/m |

### Object Management
| Tool | Description |
|------|-------------|
| `add_object` | Primitives (cube, sphere, cylinder, etc.) |
| `delete_object` | Remove by name |
| `duplicate_object` | Copy with optional linked mesh |
| `set_transform` | Location, rotation, scale |
| `set_visibility` | Viewport and render visibility |
| `set_parent` | Parent/unparent objects |
| `select_objects` | Multi-select with active |
| `batch_transform` | Transform multiple objects in one call |
| `set_dimensions` | Exact sizing in mm/cm/m/in/ft |

### Export & LOD
| Tool | Description |
|------|-------------|
| `export_scene` | GLB, GLTF, FBX, OBJ, STL with presets (dayz, godot, print) |
| `generate_lods` | Auto-decimate copies at specified ratios |

### Capture
| Tool | Description |
|------|-------------|
| `get_viewport_screenshot` | 3D viewport capture |
| `render_image` | Full render with engine/resolution control |

### Checkpoints & History
| Tool | Description |
|------|-------------|
| `create_checkpoint` | Named undo point |
| `restore_checkpoint` | Roll back to checkpoint |
| `list_checkpoints` | Show all checkpoints |
| `get_command_history` | Recent operations log |

## Architecture

```
Claude Code  -->  bridge_server.py (stdio)  -->  TCP :9876  -->  Blender addon  -->  bpy
```

- **bridge_server.py** — MCP tool wrappers, persistent TCP connection with retry
- **blender_bridge/** — Blender addon package (installed as zip)
  - `executor.py` — All mutating command handlers
  - `introspection.py` — Read-only queries, mesh validation, texture listing
  - `dispatcher.py` — Command routing, auto-diff, history logging
  - `capture.py` — Viewport screenshots and renders
  - `integrations/polyhaven.py` — Poly Haven API client

Every mutating command automatically captures before/after scene snapshots and returns a diff showing what changed.

## Privacy

Zero telemetry. No data leaves your machine. The only network traffic is localhost TCP between the MCP server and Blender, plus Poly Haven API calls when you explicitly request assets.

## Credits

Original concept by [Siddharth Ahuja](https://github.com/ahujasid/blender-mcp). Rebuilt from scratch as a modular package with 63 structured tools, auto-diff, checkpoints, and no telemetry.

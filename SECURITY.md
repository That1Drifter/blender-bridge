# Security

## Threat model

Blender Bridge listens on `localhost:9876`. Binding to localhost limits remote
network exposure, but it is not authentication: any process running as a local
user that can reach the port can send commands to the bridge.

Commands execute inside Blender's Python process with Blender's operating-system
permissions. In-process Python cannot be sandboxed from itself, so Blender Bridge
does not claim to enforce per-module filesystem, network, or subprocess
permissions.

## Execution modes

Prefer `execute_operations`, which exposes a limited set of structured commands.
`execute_code` with `mode="safe"` remains available for cases that need Python.
Safe mode blocks selected imports and builtins as a guardrail against accidental
foot-guns. It is not a security sandbox and is bypassable by construction.

Raw `execute_code` with `mode="exec"` is disabled by default. To opt in, enable
**Allow Raw Exec** in Blender's **Bridge** sidebar panel. Raw code then runs with
the full permissions of the Blender process. Disable the option again when it is
not needed.

## Audit logging

Every invocation of raw code, safe code, or structured operations appends a JSON
line to:

`<Blender user CONFIG directory>/blender_bridge_audit.jsonl`

The CONFIG directory is resolved with `bpy.utils.user_resource('CONFIG')`. Each
record contains a UTC timestamp, command, mode, SHA-256 hash of the payload,
UTF-8 payload size in bytes, success flag, and error text when applicable. Full
payload text is omitted by default; setting `AUDIT_LOG_FULL_CODE = True` in
`blender_bridge/constants.py` opts in to storing it.

Audit writes are best effort so a logging failure does not break command
execution. Log records are never deleted automatically. Operators are responsible
for protecting, rotating, and deleting the log according to their retention
requirements, especially when full-payload logging is enabled.

## Operational recommendations

- Keep raw execution disabled unless it is actively required.
- Prefer `execute_operations` over either code execution mode.
- Run Blender under an operating-system account with only the permissions needed
  for the current project.
- Stop the bridge server when it is not in use.

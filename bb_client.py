#!/usr/bin/env python
"""Command-line client for the bpy-free Blender Bridge TCP package."""

import argparse
import json
import sys
from pathlib import Path

from bridge_client import BridgeClient, BridgeTransportError


class _ArgumentParser(argparse.ArgumentParser):
    """Argument parser whose usage errors use the bridge CLI's exit code."""

    def error(self, message: str) -> None:
        self.print_usage(sys.stderr)
        self.exit(3, f"{self.prog}: error: {message}\n")


def _json_object(value: str, option_name: str) -> dict:
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError as exc:
        raise argparse.ArgumentTypeError(
            f"{option_name} must be valid JSON: {exc.msg}"
        ) from exc
    if not isinstance(parsed, dict):
        raise argparse.ArgumentTypeError(f"{option_name} must be a JSON object")
    return parsed


def _positive_port(value: str) -> int:
    try:
        port = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("port must be an integer") from exc
    if not 1 <= port <= 65535:
        raise argparse.ArgumentTypeError("port must be between 1 and 65535")
    return port


def _positive_timeout(value: str) -> float:
    try:
        timeout = float(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("timeout must be a number") from exc
    if timeout <= 0:
        raise argparse.ArgumentTypeError("timeout must be greater than zero")
    return timeout


def build_parser() -> argparse.ArgumentParser:
    parser = _ArgumentParser(description="Send one command to Blender Bridge.")
    parser.add_argument("--host", default="localhost", help="Bridge host (default: localhost)")
    parser.add_argument("--port", type=_positive_port, default=9876, help="Bridge port")
    parser.add_argument(
        "--timeout", type=_positive_timeout, default=30.0,
        help="Default command and connection timeout in seconds",
    )
    subparsers = parser.add_subparsers(dest="subcommand", required=True)

    subparsers.add_parser("get_scene_info", help="Get an overview of the Blender scene")

    execute = subparsers.add_parser("execute", help="Execute a Blender Python script")
    execute.add_argument("--file", required=True, help="Path to a UTF-8 Python script")

    export = subparsers.add_parser("export", help="Export the current scene")
    export.add_argument("--preset", required=True, help="Export preset name")
    export.add_argument("--out", required=True, help="Output path for the exported file")

    call = subparsers.add_parser("call", help="Call any bridge command")
    call.add_argument("command", help="Bridge command name")
    call.add_argument(
        "--json", dest="params", default="{}", metavar="PARAMS_JSON",
        help="Command parameters as a JSON object",
    )
    call.add_argument(
        "--options", default=None, metavar="OPTIONS_JSON",
        help="Command options as a JSON object",
    )
    return parser


def request_from_args(args: argparse.Namespace) -> tuple[str, dict, dict | None]:
    if args.subcommand == "get_scene_info":
        return "get_scene_info", {}, None
    if args.subcommand == "execute":
        try:
            code = Path(args.file).read_text(encoding="utf-8")
        except OSError as exc:
            raise argparse.ArgumentTypeError(f"cannot read --file {args.file!r}: {exc}") from exc
        return "execute_code", {"code": code}, None
    if args.subcommand == "export":
        return "export_scene", {"preset": args.preset, "filepath": args.out}, None
    return (
        args.command,
        _json_object(args.params, "--json"),
        _json_object(args.options, "--options") if args.options is not None else None,
    )


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        command, params, options = request_from_args(args)
    except argparse.ArgumentTypeError as exc:
        parser.error(str(exc))

    client = BridgeClient(
        host=args.host,
        port=args.port,
        timeout=args.timeout,
        connect_timeout=args.timeout,
    )
    try:
        response = client.send(command, params, options)
    except BridgeTransportError as exc:
        print(f"Connection/transport failure: {exc}", file=sys.stderr)
        return 2
    except (OSError, ValueError) as exc:
        print(f"Connection/transport failure: {exc}", file=sys.stderr)
        return 2
    finally:
        client.close()

    json.dump(response, sys.stdout, separators=(",", ":"))
    sys.stdout.write("\n")
    return 1 if isinstance(response, dict) and response.get("status") == "error" else 0


if __name__ == "__main__":
    raise SystemExit(main())

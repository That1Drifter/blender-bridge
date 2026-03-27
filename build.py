#!/usr/bin/env python
"""Build the Blender addon zip for installation."""

import glob
import os
import py_compile
import sys
import zipfile


def main():
    root = os.path.dirname(os.path.abspath(__file__))
    addon_dir = os.path.join(root, "blender_bridge")
    zip_path = os.path.join(root, "blender_bridge.zip")

    # Syntax check all files first
    print("Checking syntax...")
    py_files = glob.glob(os.path.join(addon_dir, "**", "*.py"), recursive=True)
    for f in py_files:
        try:
            py_compile.compile(f, doraise=True)
        except py_compile.PyCompileError as e:
            print(f"SYNTAX ERROR: {e}")
            sys.exit(1)
    print(f"  {len(py_files)} files OK")

    # Build zip
    print(f"Building {zip_path}...")
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
        for f in py_files:
            arcname = os.path.relpath(f, root)
            z.write(f, arcname)
            print(f"  + {arcname}")

    size_kb = os.path.getsize(zip_path) / 1024
    print(f"Done: {zip_path} ({size_kb:.0f} KB)")


if __name__ == "__main__":
    main()

#!/usr/bin/env python
"""Backward-compatible shim for :mod:`bridge_client.cli`."""

from bridge_client.cli import main


if __name__ == "__main__":
    raise SystemExit(main())

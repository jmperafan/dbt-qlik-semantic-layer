#!/usr/bin/env python3
"""PostToolUse hook: re-run the portability check the moment the semantic
layer YAML changes, instead of waiting for CI.

Reads a PostToolUse hook payload from stdin (tool_name, tool_input.file_path,
cwd). Only acts on models/marts/_marts.yml and models/metrics/_metrics.yml.
Runs `python3 scripts/semantic_bridge.py --check`; on failure, prints its
output to stderr and exits 2, which surfaces the check's error back to Claude
immediately. `dbt parse` must already have been run in this session for the
check to see the latest manifest; the check itself says so if target/ is stale
or missing.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

WATCHED_FILES = ("models/marts/_marts.yml", "models/metrics/_metrics.yml")


def main() -> None:
    try:
        payload = json.load(sys.stdin)
    except json.JSONDecodeError:
        sys.exit(0)

    if payload.get("tool_name") not in ("Write", "Edit"):
        sys.exit(0)

    tool_input = payload.get("tool_input") or {}
    file_path = tool_input.get("file_path") or ""
    if not any(file_path.endswith(watched) for watched in WATCHED_FILES):
        sys.exit(0)

    project_dir = Path(payload.get("cwd") or ".")
    result = subprocess.run(
        [sys.executable, str(project_dir / "scripts" / "semantic_bridge.py"), "--check"],
        cwd=project_dir,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        print(
            f"The portability check failed after this edit to {file_path!r}:\n"
            f"{result.stdout}{result.stderr}",
            file=sys.stderr,
        )
        sys.exit(2)

    sys.exit(0)


if __name__ == "__main__":
    main()

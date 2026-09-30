#!/usr/bin/env python3
"""PreToolUse hook: block a hand-rolled aggregation formula outside the
semantic layer.

Metric logic lives in models/marts/, models/metrics/ (MetricFlow YAML) and
macros/ (the Qlik translation). A sum(), count(), avg() written anywhere else
-- a scratch SQL file, a new Qlik script, a pandas snippet -- is a new,
unreviewed metric. This is a heuristic pattern match, not a parser: it will
miss creative rewrites and can false-positive on unrelated words. It's a nudge
that costs a retry, not a proof.

Reads a PreToolUse hook payload from stdin (tool_name, tool_input.file_path,
tool_input.content for Write, tool_input.new_string for Edit). Exits 2 to
block the write, with the reason on stderr, which Claude sees. Exits 0 to
allow it.
"""
from __future__ import annotations

import json
import re
import sys

ALLOWED_PATH_PARTS = ("models/marts/", "models/metrics/", "macros/")
# Anchored to a path boundary (start of string or after a "/"), not a bare
# substring: "custom_models/marts/x.sql" contains "models/marts/" too, and
# shouldn't count as the real models/marts/.
ALLOWED_PATH_PATTERN = re.compile("(?:^|/)(?:" + "|".join(re.escape(p) for p in ALLOWED_PATH_PARTS) + ")")
AGGREGATION_PATTERN = re.compile(r"\b(sum|count|avg|min|max|stddev|variance)\s*\(", re.IGNORECASE)


def main() -> None:
    try:
        payload = json.load(sys.stdin)
    except json.JSONDecodeError:
        sys.exit(0)  # can't read the payload, don't block on a guess

    if payload.get("tool_name") not in ("Write", "Edit"):
        sys.exit(0)

    tool_input = payload.get("tool_input") or {}
    file_path = tool_input.get("file_path") or ""
    if not file_path:
        sys.exit(0)

    if file_path.endswith((".yml", ".yaml")) or ALLOWED_PATH_PATTERN.search(file_path):
        sys.exit(0)  # the semantic layer and its translation macros are the allowed home

    new_text = tool_input.get("content") or tool_input.get("new_string") or ""
    match = AGGREGATION_PATTERN.search(new_text)
    if match:
        print(
            f"Blocked: {file_path!r} would contain a hand-rolled `{match.group(0)}...)`. "
            "That's a new, unreviewed metric. Use the governed metric tool instead "
            "(dbt MCP list_metrics/query_metrics, or the Snowflake metrics tool), or "
            "add it to models/marts/_marts.yml or models/metrics/_metrics.yml if it "
            "doesn't exist yet.",
            file=sys.stderr,
        )
        sys.exit(2)

    sys.exit(0)


if __name__ == "__main__":
    main()

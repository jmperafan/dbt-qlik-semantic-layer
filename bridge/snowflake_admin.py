# /// script
# requires-python = ">=3.11"
# dependencies = ["snowflake-connector-python", "pyyaml"]
# ///
"""
Two things that used to be dbt Jinja macros (macros/metrics/qlik.sql,
macros/metrics/snowflake_mcp.sql) -- moved here, as plain Python, because a
Jinja macro can't be unit tested without a running dbt project and these
were the only two pieces of logic in this repo with no test coverage.
Everything in bridge/ already has it.

  1. qlik-table   Translate every metric in target/semantic_manifest.json
                   into a Qlik expression (same two shapes as before: a
                   bare-column aggregation, or a ratio of two mirrored
                   metrics), write the result as a Snowflake table
                   (qlik_metric_definitions -- the table
                   bridge/qlik/load_metric_definitions.qvs reads), and
                   optionally the same rows as JSON for bridge/qlik/sync.py,
                   so there's no separate "export it back out of Snowflake"
                   step in between.

  2. mcp-server    Create/replace the Snowflake-managed MCP server over the
                   semantic view and its grants (former deploy_mcp_server).

Each subcommand's --check or --dry-run runs with no Snowflake connection, the
same split semantic_bridge.py and bridge/qlik/sync.py already use.

  uv run bridge/snowflake_admin.py qlik-table --check
  uv run bridge/snowflake_admin.py qlik-table --dry-run
  uv run bridge/snowflake_admin.py qlik-table --write-json target/qlik_metric_definitions.json
  uv run bridge/snowflake_admin.py mcp-server --dry-run
  uv run bridge/snowflake_admin.py mcp-server --oauth-integration
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import snowflake_connection

AGG_TEMPLATES = {
    "sum": "Sum({})",
    "count": "Count({})",
    "count_distinct": "Count(DISTINCT {})",
    "average": "Avg({})",
    "min": "Min({})",
    "max": "Max({})",
}


class AdminError(Exception):
    pass


# ---------------------------------------------------------------------------
# qlik-table: former macros/metrics/qlik.sql
# ---------------------------------------------------------------------------


def qlik_expression(metric: dict, metrics_by_name: dict) -> str:
    """Translate one metric into a Qlik expression, or '' if it can't be
    mirrored safely. Only two shapes are mirrored: a simple, unfiltered
    aggregation on a bare column, or a ratio of two metrics that are
    themselves mirrored. Everything else stays '' on purpose, so nobody
    assumes coverage that isn't there."""
    params = metric["type_params"]

    if metric["type"] == "simple" and not metric.get("filter"):
        agg = (params.get("metric_aggregation_params") or {}).get("agg")
        expr = params.get("expr")
        if expr and agg in AGG_TEMPLATES and " " not in expr and "(" not in expr:
            return AGG_TEMPLATES[agg].format(expr)
        return ""

    if metric["type"] == "ratio":
        num_ref, den_ref = params.get("numerator"), params.get("denominator")
        if not num_ref or not den_ref:
            return ""
        num, den = metrics_by_name.get(num_ref["name"]), metrics_by_name.get(den_ref["name"])
        if (
            not num_ref.get("filter")
            and not den_ref.get("filter")
            and num
            and den
            and qlik_expression(num, metrics_by_name)
            and qlik_expression(den, metrics_by_name)
        ):
            return f"($(m_{num['name']})) / ($(m_{den['name']}))"
        return ""

    return ""


def qlik_not_mirrored_reason(metric: dict) -> str:
    """Why qlik_expression returned '' for this metric. Only called when it
    did, so every elif here matches something qlik_expression already
    rejected -- order matters, same as the macro it replaces."""
    params = metric["type_params"]

    if metric["type"] == "simple":
        if metric.get("filter"):
            return "Filtered metric: needs set analysis, build it by hand in Qlik"
        agg = (params.get("metric_aggregation_params") or {}).get("agg")
        if agg not in AGG_TEMPLATES:
            return f"{agg} has no Qlik equivalent here: build it by hand in Qlik"
        return "Expression is not a plain column: build it by hand in Qlik"

    if metric["type"] == "ratio":
        num_ref, den_ref = params.get("numerator") or {}, params.get("denominator") or {}
        if num_ref.get("filter") or den_ref.get("filter"):
            return "Filtered numerator or denominator: build it by hand in Qlik"
        return "Numerator or denominator is not mirrored"

    return f"{metric['type'].capitalize()} metric: query it through the dbt Semantic Layer"


def build_qlik_rows(semantic_manifest: dict) -> list[dict]:
    """One row per metric, same columns models/qlik/qlik_metric_definitions.sql
    used to generate -- sorted by name, same as the old model's `sort(attribute='name')`."""
    metrics = {m["name"]: m for m in semantic_manifest["metrics"]}
    rows = []
    for name in sorted(metrics):
        metric = metrics[name]
        expr = qlik_expression(metric, metrics)
        rows.append(
            {
                "metric_name": name,
                "label": metric.get("label") or name,
                "description": metric.get("description"),
                "metric_type": metric["type"],
                "qlik_expression": expr or None,
                "sync_status": "mirrored" if expr else "not_mirrored",
                "sync_note": None if expr else qlik_not_mirrored_reason(metric),
            }
        )
    return rows


def default_qlik_schema() -> str | None:
    """Where qlik_metric_definitions has always landed: dbt's own target
    schema (profiles.yml's SNOWFLAKE_SCHEMA, default 'jaffle_shop'), not
    semantic_schema -- that one's for the semantic view only. Kept the same
    on purpose so an existing Qlik app's vMetricsTable needs no change."""
    database = os.environ.get("SNOWFLAKE_DATABASE")
    schema = os.environ.get("SNOWFLAKE_SCHEMA", "jaffle_shop")
    return f"{database}.{schema}" if database else None


def write_qlik_table(conn, schema: str, rows: list[dict]) -> None:
    cursor = conn.cursor()
    cursor.execute(f"CREATE SCHEMA IF NOT EXISTS {schema}")
    cursor.execute(
        f"""
        CREATE OR REPLACE TABLE {schema}.qlik_metric_definitions (
            metric_name STRING, label STRING, description STRING, metric_type STRING,
            qlik_expression STRING, sync_status STRING, sync_note STRING,
            generated_at TIMESTAMP_NTZ
        )
        """
    )
    if rows:
        cursor.executemany(
            f"""
            INSERT INTO {schema}.qlik_metric_definitions
            (metric_name, label, description, metric_type, qlik_expression, sync_status, sync_note, generated_at)
            VALUES (%(metric_name)s, %(label)s, %(description)s, %(metric_type)s, %(qlik_expression)s,
                    %(sync_status)s, %(sync_note)s, CURRENT_TIMESTAMP())
            """,
            rows,
        )


def run_qlik_table(args: argparse.Namespace) -> None:
    semantic_manifest = json.loads((args.target_dir / "semantic_manifest.json").read_text())
    rows = build_qlik_rows(semantic_manifest)

    if args.write_json:
        args.write_json.write_text(json.dumps(rows, indent=2))

    if args.check:
        mirrored = sum(1 for r in rows if r["sync_status"] == "mirrored")
        print(f"OK: {mirrored}/{len(rows)} metrics mirror to Qlik")
        return

    schema = args.schema or default_qlik_schema()
    if not schema:
        raise AdminError("--schema DATABASE.SCHEMA is required unless --check (SNOWFLAKE_DATABASE isn't set)")

    if args.dry_run:
        print(f"-- would write {len(rows)} rows to {schema}.qlik_metric_definitions")
        print(json.dumps(rows, indent=2))
        return

    conn = snowflake_connection.connect()
    try:
        write_qlik_table(conn, schema, rows)
    finally:
        conn.close()
    print(f"{schema}.qlik_metric_definitions: {len(rows)} rows")


# ---------------------------------------------------------------------------
# mcp-server: former macros/metrics/snowflake_mcp.sql
# ---------------------------------------------------------------------------


def dbt_project_vars(project_path: Path = Path("dbt_project.yml")) -> dict:
    import yaml

    return (yaml.safe_load(project_path.read_text()) or {}).get("vars") or {}


def mcp_server_statements(
    database: str, semantic_schema: str, semantic_view: str, role: str, warehouse: str, oauth_integration: bool = False
) -> list[str]:
    """Same five (or six) statements deploy_mcp_server used to build: create
    the MCP server from a spec naming the semantic view as its one tool,
    then grant metrics_reader_role everything it needs to call it."""
    schema = f"{database}.{semantic_schema}"
    view = f"{schema}.{semantic_view}"
    server = f"{view}_mcp"
    tool_name = semantic_view.replace("_", "-") + "-metrics"

    statements = [
        f"""create or replace mcp server {server} from specification $$
    tools:
      - name: "{tool_name}"
        type: "CORTEX_ANALYST_MESSAGE"
        identifier: "{view}"
        title: "Governed metrics"
        description: "Governed metrics, generated from the dbt semantic layer. Use this for any business number."
  $$""",
        f"grant usage on database {database} to role {role}",
        f"grant usage on schema {schema} to role {role}",
        f"grant usage on mcp server {server} to role {role}",
        f"grant select on semantic view {view} to role {role}",
        f"grant usage on warehouse {warehouse} to role {role}",
    ]
    if oauth_integration:
        # The redirect URI must match the one Claude shows during connector
        # setup. Read the client ID/secret once afterward with
        # system$show_oauth_client_secrets and never save them to the repo.
        statements.append(
            "create security integration if not exists claude_mcp_oauth\n"
            "  type = oauth oauth_client = custom enabled = true\n"
            "  oauth_client_type = 'CONFIDENTIAL'\n"
            "  oauth_redirect_uri = 'https://claude.ai/api/mcp/auth_callback'"
        )
    return statements


def run_mcp_server(args: argparse.Namespace) -> None:
    project_vars = dbt_project_vars()
    database = args.database or os.environ.get("SNOWFLAKE_DATABASE")
    if not database:
        raise AdminError("--database or SNOWFLAKE_DATABASE is required")

    statements = mcp_server_statements(
        database=database,
        semantic_schema=args.semantic_schema or project_vars.get("semantic_schema", "semantic"),
        semantic_view=args.semantic_view or project_vars.get("semantic_view", "jaffle_shop"),
        role=args.role or project_vars.get("metrics_reader_role", "metrics_reader"),
        warehouse=args.warehouse or project_vars.get("mcp_warehouse", "transforming"),
        oauth_integration=args.oauth_integration,
    )

    if args.dry_run:
        for statement in statements:
            print(statement + ";\n")
        return

    conn = snowflake_connection.connect(role=args.grant_role, warehouse=args.warehouse)
    try:
        cursor = conn.cursor()
        for statement in statements:
            cursor.execute(statement)
    finally:
        conn.close()
    print(f"{len(statements)} statements run")


# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    qlik_table = sub.add_parser("qlik-table", help="Build and deploy the Qlik metric-definitions table")
    qlik_table.add_argument("--target-dir", type=Path, default=Path("target"))
    qlik_table.add_argument("--schema", help="DATABASE.SCHEMA (default: SNOWFLAKE_DATABASE + SNOWFLAKE_SCHEMA/'jaffle_shop')")
    qlik_table.add_argument("--write-json", type=Path, help="Also write the rows as JSON, for bridge/qlik/sync.py")
    qlik_table.add_argument("--check", action="store_true", help="Build the rows only, no Snowflake connection")
    qlik_table.add_argument("--dry-run", action="store_true", help="Print the rows, no Snowflake connection")
    qlik_table.set_defaults(handler=run_qlik_table)

    mcp = sub.add_parser("mcp-server", help="Deploy the Snowflake-managed MCP server and its grants")
    mcp.add_argument("--database", help="default: SNOWFLAKE_DATABASE")
    mcp.add_argument("--semantic-schema", help="default: dbt_project.yml's semantic_schema var")
    mcp.add_argument("--semantic-view", help="default: dbt_project.yml's semantic_view var")
    mcp.add_argument("--role", help="metrics_reader_role to grant to (default: dbt_project.yml's metrics_reader_role var)")
    mcp.add_argument("--warehouse", help="default: dbt_project.yml's mcp_warehouse var")
    mcp.add_argument("--grant-role", help="role to connect as when running the grants (default: SNOWFLAKE_ROLE/account default)")
    mcp.add_argument("--oauth-integration", action="store_true")
    mcp.add_argument("--dry-run", action="store_true")
    mcp.set_defaults(handler=run_mcp_server)

    args = parser.parse_args()
    try:
        args.handler(args)
    except AdminError as error:
        sys.exit(f"[error] {error}")


if __name__ == "__main__":
    main()

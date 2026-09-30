# Semantic layer sketch: one definition, three consumers

A minimal Jaffle Shop project showing a recommended setup for governing
metrics across dbt, Snowflake and Qlik: metrics are defined once in MetricFlow
YAML, and Claude, Snowflake and Qlik all read from that one definition. To move
it into a real project, see [MIGRATION.md](MIGRATION.md).

```
                     models/marts/_marts.yml + models/metrics/_metrics.yml
                        (MetricFlow YAML: the only place metric logic lives)
                                             │
         ┌───────────────────────────────────┼───────────────────────────────────┐
         │ Path A (Starter or above)         │ Path B (any plan)                 │ Qlik
         ▼                                   ▼                                   ▼
  dbt Semantic Layer              scripts/semantic_bridge.py          models/qlik/qlik_metric_definitions
         │                         gate → Ossie → + Cortex extras       (generated from the graph)
         │                                   │                                   │
         │                                   ▼                                   ▼
         │                     Snowflake semantic view                qlik/load_metric_definitions.qvs
         │                     + deploy_mcp_server macro               (one variable per metric)
         ▼                                   ▼
   dbt MCP ───────────► Claude Code / Claude Desktop ◄─────────── Snowflake MCP
                           CLAUDE.md, claude/desktop_project_instructions.md
```

## What's where

| File | What it shows |
|------|---------------|
| [models/marts/_marts.yml](models/marts/_marts.yml) | Semantic models, entities, dimensions and simple metrics. `config.meta.snowflake` holds Cortex synonyms and instructions, never a formula |
| [models/metrics/_metrics.yml](models/metrics/_metrics.yml) | A ratio (portable everywhere) and a cumulative metric marked `dbt_only` |
| [scripts/semantic_bridge.py](scripts/semantic_bridge.py) | The Path B pipeline and CI gate: fails on lossy metrics, gets an Ossie document, checks Snowflake can load it, merges Cortex extras, writes the deploy SQL |
| [scripts/ossie-0.1.1-schema.json](scripts/ossie-0.1.1-schema.json) | The official Ossie 0.1.1 JSON schema (Apache-2.0, from `apache/ossie` at tag `osi-0.1.1-rc1`), the version Snowflake accepts |
| [models/qlik/qlik_metric_definitions.sql](models/qlik/qlik_metric_definitions.sql) | The modern version of the old Qlik macro: the definitions table, built from the same YAML |
| [macros/qlik.sql](macros/qlik.sql) | SQL to Qlik translation. Mirrors simple and ratio metrics only, and flags the rest |
| [qlik/load_metric_definitions.qvs](qlik/load_metric_definitions.qvs) | Qlik load script that turns each mirrored row into a variable |
| [tests/reconcile_semantic_view.sql](tests/reconcile_semantic_view.sql) | Reconciliation: the semantic view against plain SQL. Fails on any drift |
| [macros/snowflake_mcp.sql](macros/snowflake_mcp.sql) | `deploy_mcp_server`: the Snowflake-managed MCP server over the semantic view, its grants, and optionally the OAuth integration Claude Desktop connects through |
| [.github/workflows/semantic-layer-checks.yml](.github/workflows/semantic-layer-checks.yml) | Runs the gate and the Snowflake readiness check on every pull request, with no warehouse |
| [CLAUDE.md](CLAUDE.md), [claude/](claude/) | The instruction layer: use governed metrics, never improvise a formula |
| [.mcp.json](.mcp.json), [.env.example](.env.example) | Project-scoped MCP servers for Claude Code, with a `.env` for Path A or Path B. No credentials committed |

Names that change per environment (schema, view, role, warehouse) are dbt vars
in [dbt_project.yml](dbt_project.yml).

## Run it locally (DuckDB, no Snowflake needed)

```bash
dbt build                                               # seeds, models, Qlik table, tests
duckdb jaffle_shop.duckdb -c "from qlik_metric_definitions"
python3 scripts/semantic_bridge.py --check              # portability gate, standard library only
uv run scripts/semantic_bridge.py --schema ANALYTICS.SEMANTIC   # full bridge, Snowflake checks included
```

The Qlik table comes out as:

| metric_name | qlik_expression | sync_status | sync_note |
|-------------|-----------------|-------------|-----------|
| average_order_value | `($(m_revenue)) / ($(m_order_count))` | mirrored | |
| completed_revenue | `Sum(completed_amount)` | mirrored | |
| new_customers | `Count(DISTINCT customer_id)` | mirrored | |
| order_count | `Count(DISTINCT order_id)` | mirrored | |
| revenue | `Sum(amount)` | mirrored | |
| revenue_mtd | | not_mirrored | Cumulative metric: query it through the dbt Semantic Layer |

## Getting Ossie out of dbt

dbt v2 doesn't write Ossie documents yet. dbt Labs plans to add it, but hasn't
committed to a date. Until then there are two working routes, and the bridge
takes either:

| Route | How | Status |
|-------|-----|--------|
| dbt v2 + Apache converter | `dbt parse`, then `uv run scripts/semantic_bridge.py` | The default. The pinned converter writes Ossie 0.2.0.dev0; the bridge rewraps it as 0.1.1 and validates it against the official schema |
| dbt v1.12, only for this step | `uvx --from 'dbt-core>=1.12,<1.13' --with dbt-snowflake dbt parse --target snowflake`, then the bridge with `--source dbt-v1` | dbt writes `osi_document.json` natively, already 0.1.1. uvx runs v1 in its own environment, like a container would. It keeps lossy metrics with only an I078 warning; the bridge drops the `dbt_only` ones |
| dbt v2, native | Wait | On dbt Labs' roadmap, no committed date |

`require-dbt-version` allows both v1.12 and v2, so the same project serves
either route without edits.

## Run it against Snowflake (Path B)

```bash
export SNOWFLAKE_ACCOUNT=... SNOWFLAKE_USER=...          # see profiles.yml
dbt build --target snowflake
dbt parse --target snowflake                              # so Ossie sources point at Snowflake tables
uv run scripts/semantic_bridge.py --schema ANALYTICS.SEMANTIC
snow sql -f target/deploy_semantic_views.sql              # creates ANALYTICS.SEMANTIC.JAFFLE_SHOP
dbt run-operation deploy_mcp_server --target snowflake    # --args '{dry_run: true}' to print only
dbt test --target snowflake --select tag:reconciliation --vars '{semantic_views_deployed: true}'
```

In CI, [.github/workflows/semantic-layer-checks.yml](.github/workflows/semantic-layer-checks.yml)
runs the gate and the Snowflake readiness check on every pull request without
touching the warehouse. The reconciliation test runs after a deploy.

## Connect Claude

- **Claude Code:** copy `.env.example` to `.env` and fill in the Path A or
  Path B block. Export `DBT_PROJECT_DIR`, `SNOWFLAKE_ACCOUNT_URL` and
  `SNOWFLAKE_PAT` in your shell (plus `SNOWFLAKE_DATABASE`, `SEMANTIC_SCHEMA`
  and `SNOWFLAKE_MCP_SERVER` if they differ from the defaults), then open
  Claude Code in this folder
- **Claude Desktop:** add a custom connector, the remote dbt MCP server on
  Path A (OAuth needs Starter or above) or the Snowflake MCP server on Path B.
  Then paste [claude/desktop_project_instructions.md](claude/desktop_project_instructions.md)
  into the project's instructions. On Claude Team and Enterprise, only an Owner
  can add the connector
- **Snowflake OAuth:** `dbt run-operation deploy_mcp_server --args '{oauth_integration: true}'`
  creates the security integration, with a role that has CREATE INTEGRATION.
  Read its client ID and secret once with `system$show_oauth_client_secrets`
  and paste them into the connector; never commit them

## Design rules this sketch enforces

- **Logic in one place.** The bridge rejects any `config.meta.snowflake` key
  other than `synonyms`, `instructions` and `examples`, so a second copy of a
  formula can't hide in meta
- **Nothing lossy reaches Snowflake silently.** Cumulative, conversion and
  private metrics, offsets and Jinja filters fail the gate unless marked
  `dbt_only: true`, because the converter drops offsets and filters without
  warning. Before writing the deploy SQL, the bridge validates the document
  against the Ossie 0.1.1 schema and fails on any field or metric with no
  Snowflake or ANSI SQL expression, which Snowflake would omit without an error
- **Metric `expr` is a plain column.** Row-level logic goes in the model, like
  `completed_amount`, and aggregation in the YAML. A plain column reaches
  Snowflake table-qualified, which its metric expressions require, and mirrors
  cleanly to Qlik. The bridge fails on any metric with no table-qualified column
- **Qlik mirrors definitions, not values**, and only the shapes that translate
  cleanly. Everything else says so in `sync_note`

## Tested so far, and what isn't yet

Tested locally:

- **dbt 2.0.6 with DuckDB:** the build, the Qlik definitions table (including
  filtered, expression-less and ratio-on-unmirrored metrics), the portability
  gate on the pass and the fail path, and the reconciliation comparison logic
- **The Apache Ossie converter** at the pinned commit, end to end through the
  bridge: rewrapped as 0.1.1, schema-valid, every metric table-qualified, Cortex
  extras merged. The Snowflake check caught a version mismatch, an unqualified
  expression and an unsupported dialect when fed broken documents
- **dbt v1.12.5:** parses the project unchanged and writes `osi_document.json`
  as Ossie 0.1.1. The bridge's `--source dbt-v1` route passes the same checks
- **The CI workflow's commands**, locally: v1.12 with dbt-snowflake parses
  with dummy credentials, and both bridge steps pass
- **`deploy_mcp_server`** in dry-run mode, rendering the statements from the vars

Checked against vendor docs, not run: the Snowflake procedure, metric
qualification, `SEMANTIC_VIEW()` naming and query privileges; Qlik's LET, SET
and nested dollar-sign expansion; Claude Code's `.mcp.json` variables; Claude
custom connectors.

Not run yet:

- **Everything on Snowflake**: the deploy, the MCP server, the grants and the
  OAuth integration
- **Qlik**: the load script in a real app, and a chart using
  `$(m_average_order_value)`. Qlik documents nested expansion, but check it once
- **Semantic validation.** `dbt parse` skips it without a dbt platform
  connection, and `dbt sl validate` needs one

Answered while building this, from an open pre-session question list:

- dbt v2 doesn't write `osi_document.json` (2.0.6, local parse). dbt v1.12
  does, as Ossie 0.1.1
- Snowflake marks `SYSTEM$CREATE_SEMANTIC_VIEW_FROM_OSI_YAML` as deprecated.
  Use `..._FROM_OSSIE_YAML`

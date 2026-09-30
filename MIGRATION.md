# Moving this into your environment

This project is a sketch on Jaffle Shop. In your environment, the usual move is
to copy the pieces into your existing dbt project, point them at your own
semantic models, and set a handful of names. This page covers what to copy,
what to rename, and the order to switch things on.

## 1. Copy the pieces

| From this project | Into your project | Notes |
|-------------------|-------------------|-------|
| `macros/qlik.sql`, `macros/snowflake_mcp.sql` | `macros/` | No changes needed |
| `models/qlik/` | `models/qlik/` | Reads your metrics from the project graph, so it needs no edits |
| `scripts/semantic_bridge.py`, `scripts/ossie-0.1.1-schema.json` | `scripts/` | Keep the two files side by side |
| `scripts/qlik_sync.py` | `scripts/` | Optional: automates the master-measure wiring in "6. Qlik" below. No changes needed |
| `tests/reconcile_semantic_view.sql` | `tests/` | Rewrite the `reference` CTEs for your key metrics, independently of the YAML |
| `tests/fixtures/qlik_metric_definitions.sample.json` | Not copied | Sample rows for testing `qlik_sync.py` against this sketch. Once you have a real `qlik_metric_definitions` table, export that instead |
| `.github/workflows/semantic-layer-checks.yml` | `.github/workflows/` | Adjust the dummy credentials to your profile |
| `CLAUDE.md` | Merge into your repo's `CLAUDE.md` | Update the file paths |
| `claude/desktop_project_instructions.md` | The Claude Desktop project | Replace the example metric |
| `.mcp.json`, `.env.example` | Repo root | `.env` stays out of git |
| `.claude/skills/governed-metrics/`, `.claude/hooks/`, `.claude/settings.json` | `.claude/` | The guardrail layer: a skill for the conversational half, hooks for anything written to a file. No changes needed beyond the tool name if you rename `jaffle-shop-metrics` |
| `qlik/load_metric_definitions.qvs` | The Qlik app's load script | Set the two variables at the top |
| The `vars:` block in `dbt_project.yml` | Your `dbt_project.yml` | See the names below |

Leave behind the seeds, the Jaffle Shop models and `profiles.yml`: on the dbt
platform, connections come from the environment, not a profiles file. Keep your
own `metricflow_time_spine` if you already have one.

If you want the dbt v1.12 route for Ossie, also copy
`require-dbt-version: [">=1.12.0", "<3.0.0"]`.

## 2. Set the names

| Where | Default here | Set to |
|-------|--------------|--------|
| dbt var `semantic_schema` | `semantic` | The schema for semantic views |
| dbt var `semantic_view` | `jaffle_shop` | The semantic view's name |
| dbt var `metrics_reader_role` | `metrics_reader` | The role Claude Desktop users query as |
| dbt var `mcp_warehouse` | `transforming` | The warehouse Cortex Analyst runs on |
| Bridge `--schema`, `--model-name` | `ANALYTICS.SEMANTIC`, `jaffle_shop` | `<database>.<semantic_schema>` and `<semantic_view>` |
| Qlik `vConnection`, `vMetricsTable` | `Snowflake`, `ANALYTICS.JAFFLE_SHOP.QLIK_METRIC_DEFINITIONS` | Your Qlik connection and where `qlik_metric_definitions` builds |
| Env `SNOWFLAKE_DATABASE`, `SEMANTIC_SCHEMA`, `SNOWFLAKE_MCP_SERVER` | `ANALYTICS`, `SEMANTIC`, `JAFFLE_SHOP_MCP` | Your database, schema, and `<SEMANTIC_VIEW>_MCP` |
| `CLAUDE.md` tool name | `jaffle-shop-metrics` | `<semantic_view>-metrics`, with dashes |

## 3. Switch it on, in this order

1. **Build in development.** `dbt build`, then look at `qlik_metric_definitions`.
   Every metric is either mirrored or says why not
2. **Run the portability check.** `python3 scripts/semantic_bridge.py --check`.
   Fix each failure, or mark the metric `config.meta.dbt_only: true` as a
   reviewed decision
3. **Path A, Starter or above:** the dbt Semantic Layer needs a production job
   that parses the project; `dbt build` does. Configure the Semantic Layer for
   that environment, then fill the Path A block of `.env` for dbt MCP
4. **Path B:** `dbt parse --target <prod>`, then
   `uv run scripts/semantic_bridge.py --schema <database>.<semantic_schema>`,
   run `target/deploy_semantic_views.sql`, then
   `dbt run-operation deploy_mcp_server`. An admin runs it once more with
   `--args '{oauth_integration: true}'` for Claude Desktop
5. **Reconcile.** `dbt test --select tag:reconciliation --vars '{semantic_views_deployed: true}'`
6. **Qlik.** Paste the load script, set the two variables, reload, and check
   one chart using `$(m_average_order_value)`. Wire each master measure to
   its variable by hand once, or push them with
   `uv run scripts/qlik_sync.py --definitions <export>.json` once you have a
   Qlik Cloud tenant and API key
7. **CI.** Add the workflow, and open a pull request that breaks a metric on
   purpose to see it fail
8. **Claude.** Merge `CLAUDE.md`, add the Desktop connector and paste the
   project instructions

## Getting Ossie out of dbt

dbt v2 doesn't write Ossie yet; it's on dbt Labs' roadmap with no committed
date. Until then, use the Apache converter (the bridge's default) or run dbt
v1.12 just for the parse step (`--source dbt-v1`). Both are described in the
[README](README.md#getting-ossie-out-of-dbt).

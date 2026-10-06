# Adapting this to your own project

This is a sketch on Jaffle Shop, meant to be studied and rebuilt against your
own dbt project and metrics, not forked and copied wholesale.
[README.md](README.md)'s "What's where" table maps every file. This page
covers what's actually project-specific if you're building your own version,
and the order the pieces depend on each other.

## What varies per project

| Where | Default here | Set to |
|-------|--------------|--------|
| dbt var `semantic_schema` | `semantic` | The schema for semantic views |
| dbt var `semantic_view` | `jaffle_shop` | The semantic view's name |
| dbt var `metrics_reader_role` | `metrics_reader` | The role Claude Desktop users query as |
| dbt var `mcp_warehouse` | `transforming` | The warehouse Cortex Analyst runs on |
| Qlik `vConnection`, `vMetricsTable` | `Snowflake`, `ANALYTICS.JAFFLE_SHOP.QLIK_METRIC_DEFINITIONS` | Your Qlik connection and where `qlik_metric_definitions` builds |
| Env `SNOWFLAKE_DATABASE`, `SEMANTIC_SCHEMA`, `SNOWFLAKE_MCP_SERVER` | `ANALYTICS`, `SEMANTIC`, `JAFFLE_SHOP_MCP` | Your database, schema, and `<SEMANTIC_VIEW>_MCP` |
| `CLAUDE.md` tool name | `jaffle-shop-metrics` | `<semantic_view>-metrics`, with dashes |

Everything else -- the bridge, the hooks, `bridge/snowflake_admin.py`, the
skill -- reads these names or the project graph directly, nothing else here
is Jaffle-Shop-specific.

`profiles.yml` and the seeds are local-dev/CI scaffolding, not something the
dbt platform itself needs: there, connections come from the environment you
configure in its own UI, not a profiles file. Skip both if you're only ever
running on the platform.

## The order things depend on each other

1. **Build in development.** `dbt build`, then `dbt parse` and
   `uv run bridge/snowflake_admin.py qlik-table --check` to see
   `qlik_metric_definitions`'s rows without touching Snowflake. Every metric
   is either mirrored or says why not
2. **Run the portability check.** `python3 bridge/semantic_bridge.py --check`.
   Fix each failure, or mark the metric `config.meta.dbt_only: true` as a
   reviewed decision
3. **Path A, Starter or above:** the dbt Semantic Layer needs a production job
   that parses the project; `dbt build` does. Configure the Semantic Layer for
   that environment, then fill the Path A block of `.env` for dbt MCP
4. **Path B:** `dbt parse --target <prod>`, then `uv run bridge/semantic_bridge.py`
   (it defaults `--schema`/`--model-name` from `semantic_schema`/`semantic_view`
   and the resolved database, so the bridge and `snowflake_admin.py mcp-server`
   always agree on where the view lives), run `target/deploy_semantic_views.sql`,
   then `uv run bridge/snowflake_admin.py mcp-server`. An admin runs it once more
   with `--oauth-integration` for Claude Desktop
5. **Reconcile.** `RECONCILE_LIVE=true uv run --with pytest --with
   snowflake-connector-python --with pyyaml pytest
   bridge/tests/test_reconcile_semantic_view.py -v`
6. **Qlik.** `uv run bridge/snowflake_admin.py qlik-table` writes the table;
   paste the load script, set the two variables, reload, and check one chart
   using `$(m_average_order_value)`. Wire each master measure to its variable
   by hand once, or push them with `uv run bridge/qlik/sync.py --definitions
   <export>.json` once you have a Qlik Cloud tenant and API key
7. **CI.** Add semantic-layer-checks.yml, and open a pull request that breaks
   a metric on purpose to see it fail. Add semantic-layer-deploy.yml for the
   merge-to-main side
8. **Claude.** Merge `CLAUDE.md`, add the Desktop connector and paste the
   project instructions

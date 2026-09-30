# macros/metrics

dbt only looks for macros under `macros/` (`macro-paths` in `dbt_project.yml`,
default `["macros"]`), and scans it recursively -- so this subfolder needs no
config change, and groups the two macros that exist purely to serve the
governed-metrics pipeline, rather than leaving them as two unrelated-looking
files directly under `macros/`.

| File | Macros | What they're for |
|------|--------|-------------------|
| [qlik.sql](qlik.sql) | `qlik_expression`, `qlik_not_mirrored_reason`, `sql_string` | MetricFlow-metric-to-Qlik-expression translation. Used by [models/qlik/qlik_metric_definitions.sql](../../models/qlik/qlik_metric_definitions.sql) |
| [snowflake_mcp.sql](snowflake_mcp.sql) | `deploy_mcp_server` | Deploys the Snowflake-managed MCP server over the semantic view, its grants, and the optional Claude Desktop OAuth integration |

Full argument documentation for all four macros is in [_macros.yml](_macros.yml)
(dbt's native macro-property format -- run `dbt docs generate` and they show
up in the generated docs site same as any model). The header comment in each
`.sql` file explains the non-obvious *why*; `_macros.yml` covers the
mechanical *what argument does what*.

See [qlik/README.md](../../qlik/README.md) for how these fit into the wider
Qlik picture, and [../../README.md](../../README.md) for
`deploy_mcp_server`'s place in the Snowflake deploy sequence.

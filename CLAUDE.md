# Business metrics

Metric logic lives in the semantic layer YAML: `models/marts/_marts.yml` and
`models/metrics/_metrics.yml`. Nowhere else.

When someone asks for a business number (revenue, orders, customers, average
order value, anything that sounds like a KPI):

- **Use a governed metric.** With dbt MCP: `list_metrics`, then
  `get_dimensions`, then `query_metrics`. Without dbt Semantic Layer access,
  use the `jaffle-shop-metrics` tool on the Snowflake MCP server, which reads the
  same definitions from the `JAFFLE_SHOP` semantic view
- **Never compose a metric formula yourself**, not in SQL, not in Qlik, not in
  a pandas snippet. `sum(amount)` written by hand is a new, unreviewed metric
- **If the metric doesn't exist, say so.** Suggest adding it to the YAML
  instead of approximating it
- **Name the metric in every answer**: "Revenue (`revenue`), March 2026: 152.75"
- Metrics marked `config.meta.dbt_only` aren't in Snowflake or Qlik. Query
  them through dbt

## Changing a metric

1. Edit the YAML. `config.meta.snowflake` holds Cortex synonyms and
   instructions only, never a formula
2. `dbt build` locally, then check `qlik_metric_definitions`
3. `dbt parse --target snowflake && uv run scripts/semantic_bridge.py --check`.
   If it fails, fix the metric or mark it `config.meta.dbt_only: true`

# Business metrics

Metric logic lives in the semantic layer YAML: `models/marts/_marts.yml` and
`models/metrics/_metrics.yml`. Nowhere else. The full rules are in the
`governed-metrics` skill (`.claude/skills/governed-metrics/`); the short
version:

- **Use the `jaffle-shop-metrics` tool on the Snowflake MCP server by
  default.** It reads the same definitions from the `JAFFLE_SHOP` semantic
  view and carries synonyms and AI instructions dbt MCP doesn't. Fall back to
  dbt MCP (`list_metrics`, `get_dimensions`, `query_metrics`) only for a
  metric marked `config.meta.dbt_only` -- those aren't in Snowflake or Qlik
  at all
- **Never compose a metric formula yourself**, not in SQL, not in Qlik, not in
  a pandas snippet. `sum(amount)` written by hand is a new, unreviewed metric.
  A `PreToolUse` hook blocks the obvious cases when writing to a file; it
  can't catch one spoken in chat, so this rule still matters
- **If the metric doesn't exist, say so.** Suggest adding it to the YAML
  instead of approximating it
- **Name the metric in every answer**: "Revenue (`revenue`), March 2026: 152.75"

## Changing a metric

1. Edit the YAML. `config.meta.snowflake` holds Cortex synonyms and
   instructions only, never a formula. A `PostToolUse` hook re-runs the
   portability check on save
2. `dbt parse --target snowflake && uv run deploy/semantic_bridge.py --check`.
   If it fails, fix the metric or mark it `config.meta.dbt_only: true`.
   Optionally, `uv run deploy/snowflake_admin.py qlik-table --check` shows
   how the metric will mirror to Qlik, with no Snowflake connection
3. Open the PR. `dbt-sl-ci.yml` re-runs the same checks
4. Merge. `dbt-sl-cd.yml` builds, deploys the semantic view,
   serves it over MCP, reconciles, and pushes mirrored metrics to Qlik --
   nothing else by hand. See [README.md](README.md#deploy-to-snowflake) for
   the commands if you need to run any of that yourself

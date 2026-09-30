---
name: governed-metrics
description: Answer a business number (revenue, orders, customers, average order value, or anything that sounds like a KPI) using the governed semantic layer instead of a hand-written formula. Triggers on any question asking for a metric, a KPI, or "how much/how many" phrased as a business number.
---

# Governed metrics

Metric logic lives in the semantic layer YAML: `models/marts/_marts.yml` and
`models/metrics/_metrics.yml`. Nowhere else. A `sum()`, `count()` or `avg()`
written by hand anywhere else is a new, unreviewed metric, even if it looks
obviously right.

When this skill applies:

1. **Query the governed metric tool.** Snowflake MCP's metrics tool is the
   default (it carries synonyms, sample values and AI instructions the dbt
   Semantic Layer doesn't). Fall back to dbt MCP (`list_metrics`, then
   `get_dimensions`, then `query_metrics`) only for a metric tagged
   `config.meta.dbt_only` -- those don't reach Snowflake or Qlik at all
2. **Never compose the formula yourself**, not in SQL, not in Qlik, not in a
   pandas snippet, not "just this once" for a quick estimate
3. **If the metric doesn't exist, say so.** Suggest adding it to the YAML
   instead of approximating it with a formula that looks close
4. **Name the metric in every answer**: "Revenue (`revenue`), March 2026: 152.75"
5. If the request is ambiguous (gross or net revenue? which region?), ask
   before querying rather than guessing which governed metric was meant

A `PreToolUse` hook backs this up for anything written to a file (see
`.claude/hooks/block_hand_rolled_formulas.py`): it can't stop a wrong answer
spoken in chat, only a formula written to disk. This skill is what covers the
conversation itself.

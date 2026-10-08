# Semantic layer sketch: one definition, three consumers

Metric logic lives once, in dbt's semantic layer YAML, and reaches Snowflake
Cortex, Qlik and Claude from there, never retyped.

```text
                     models/marts/_marts.yml + models/metrics/_metrics.yml
                        (MetricFlow YAML: the only place metric logic lives)
                                             │
         ┌───────────────────────────────────┼───────────────────────────────────┐
         │ Path B (default: AI context)      │ Path A (fallback: dbt_only)       │ Qlik
         ▼                                   ▼                                   ▼
  deploy/semantic_bridge.py            dbt Semantic Layer            deploy/snowflake_admin.py qlik-table
  check → Ossie → + Cortex extras             │                          (generated from the graph)
         │                                    │                                   │
         ▼                                    │                                   ▼
  Snowflake semantic view                     │                          deploy/qlik/load_metric_definitions.qvs
  + snowflake_admin.py mcp-server             │                          (one variable per metric)
         ▼                                    ▼
  Snowflake MCP ───────────► Claude Code / Claude Desktop ◄─────────── dbt MCP
                           CLAUDE.md, .claude/desktop_project_instructions.md,
                           .claude/skills/governed-metrics/, .claude/hooks/
```

Path B is the default: Snowflake semantic views carry `ai_context` that the
dbt Semantic Layer has no equivalent for. Path A is the fallback, for metrics
Ossie can't carry at all (cumulative, conversion, private), marked
`config.meta.dbt_only`.

This branch's example: `orders` and `customers` as semantic models, four
simple metrics, one ratio (`average_order_value`), one cumulative
(`revenue_mtd`, `dbt_only`).

## What's where

| File | What it shows |
|------|---------------|
| [models/marts/_marts.yml](models/marts/_marts.yml) | Entities, dimensions, simple metrics, and the Cortex synonyms in `config.meta.snowflake` |
| [models/metrics/_metrics.yml](models/metrics/_metrics.yml) | The ratio and cumulative metrics, plus the time spine a cumulative metric needs |
| [deploy/semantic_bridge.py](deploy/semantic_bridge.py) | Path B's pipeline and CI check: builds the Ossie document, validates it, merges Cortex extras, writes the deploy SQL. Tested in [deploy/tests/](deploy/tests/) |
| [deploy/snowflake_admin.py](deploy/snowflake_admin.py) | `qlik-table` for the Qlik mirror, `mcp-server` for the Snowflake MCP server and its OAuth integration |
| [deploy/qlik/](deploy/qlik/) | The Qlik load script and the optional `sync.py` automation. See ["Qlik"](#qlik) |
| [.github/workflows/](.github/workflows/) | `dbt-sl-ci.yml` checks every PR; `dbt-sl-cd.yml` deploys on every merge to main |
| [CLAUDE.md](CLAUDE.md), [.claude/](.claude/) | The guardrails: instructions, a skill, and hooks. See ["Guardrails"](#guardrails-not-just-instructions) |

Environment-specific names (schema, view, role, warehouse, Qlik connection)
are dbt vars in [dbt_project.yml](dbt_project.yml).

## Changing the semantic layer

1. Edit `_marts.yml` for a simple metric, `_metrics.yml` for a ratio or
   cumulative one.
2. `dbt parse --target snowflake && python3 deploy/semantic_bridge.py --check`.
   Fix failures, or mark the metric `dbt_only: true`.
3. Open a PR, merge. CI re-runs the check; CD deploys, serves it over MCP,
   reconciles, and pushes to Qlik. Nothing else by hand.

## Deploy to Snowflake

```bash
export SNOWFLAKE_ACCOUNT=... SNOWFLAKE_USER=... SNOWFLAKE_PRIVATE_KEY_PATH=...
dbt build --target snowflake
dbt parse --target snowflake                              # so Ossie sources point at Snowflake tables
uv run deploy/semantic_bridge.py                          # writes target/deploy_semantic_views.sql
snow sql -f target/deploy_semantic_views.sql
uv run deploy/snowflake_admin.py mcp-server               # --oauth-integration for Claude Desktop
uv run deploy/snowflake_admin.py qlik-table --write-json target/qlik_metric_definitions.json
uv run deploy/qlik/sync.py --definitions target/qlik_metric_definitions.json
```

`mcp-server`'s grants need a role with `MANAGE GRANTS` or ownership; the CD
workflow runs that step with `continue-on-error: true` so a permissions gap
doesn't block the rest.

## Connect Claude

- **Claude Code:** copy `.env.example` to `.env`, fill in Path B or Path A.
  Hooks and the skill are already project-scoped.
- **Claude Desktop:** add a custom connector (Snowflake MCP for Path B, dbt
  MCP for Path A), then paste
  [.claude/desktop_project_instructions.md](.claude/desktop_project_instructions.md)
  into the project instructions. Desktop skips hooks, so the skill or the
  pasted instructions is the only guardrail there.
- **OAuth:** `uv run deploy/snowflake_admin.py mcp-server --oauth-integration`
  creates the integration. Read the client ID and secret once with
  `system$show_oauth_client_secrets`; never commit them.

## Guardrails, not just instructions

Three layers, hardest to skip last:

1. **The tool.** Easier to call the metrics tool than derive a number by
   hand.
2. **The skill.** The only layer that reaches a spoken answer in chat.
3. **The hooks.** `block_hand_rolled_formulas.py` blocks an obvious
   hand-rolled aggregation on write; `validate_semantic_yaml.py` re-runs the
   check on save. Heuristics, not parsers.

Hooks only fire on tool calls, so a wrong number said out loud is only caught
by the skill.

## Qlik

`qlik-table` writes one row per metric; `load_metric_definitions.qvs` turns
each into a `$(m_<metric>)` variable. `sync.py` can push master measures
automatically over the Engine API instead of wiring them by hand (master
items aren't reachable through REST/QRS).

Confirmed live, worth knowing:

- A measure evaluates fine even with no matching field loaded, `Sum(amount)`
  over a missing field returns `0`. Check the data model separately.
- Nested variables in a ratio master measure worked cleanly in testing. If
  yours doesn't, fall back to a flattened single-variable expression.

## Design rules this sketch enforces

- **Logic in one place.** `config.meta.snowflake` only allows `synonyms`,
  `instructions`, `examples`, never a formula.
- **Nothing lossy reaches Snowflake silently.** Cumulative, conversion,
  private metrics, offsets and Jinja filters fail the check unless marked
  `dbt_only`.
- **Metric `expr` is a plain column.** Aggregation lives in the YAML, row
  logic in the model.
- **Qlik mirrors definitions, not values**, and only what translates
  cleanly; everything else says why in `sync_note`.

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
         │ Path B (default: AI context)      │ Path A (fallback: dbt_only)       │ Qlik
         ▼                                   ▼                                   ▼
  scripts/semantic_bridge.py           dbt Semantic Layer            models/qlik/qlik_metric_definitions
  check → Ossie → + Cortex extras             │                          (generated from the graph)
         │                                    │                                   │
         ▼                                    │                                   ▼
  Snowflake semantic view                     │                          qlik/load_metric_definitions.qvs
  + deploy_mcp_server macro                   │                          (one variable per metric)
         ▼                                    ▼
  Snowflake MCP ───────────► Claude Code / Claude Desktop ◄─────────── dbt MCP
                           CLAUDE.md, claude/desktop_project_instructions.md,
                           .claude/skills/governed-metrics/, .claude/hooks/
```

Path B is the default because Snowflake semantic views carry `ai_context`
(synonyms, sample values, AI instructions) that the dbt Semantic Layer has no
equivalent for. Path A stays as the fallback specifically for metrics Ossie
can't carry at all -- cumulative, conversion and private -- marked
`config.meta.dbt_only` in the YAML. Drop Path A and those metrics become
unanswerable to any AI tool, not just unmirrored to Qlik.

**MetricFlow is the source of truth here, a decided call.** It's the only
format that can compute a cumulative, conversion or private metric at all,
and it ties logic to a tested, lineage-aware dbt model. A Snowflake-native
alternative (skip MetricFlow, author semantic views directly with the
Snowflake Labs `dbt_semantic_view` package) was scoped and rejected: fewer
moving parts, but the same metric-type ceiling with no fallback engine
behind it. See the research doc's section 5.0 for the record of that call.

## What's where

| File | What it shows |
|------|---------------|
| [models/marts/_marts.yml](models/marts/_marts.yml) | Semantic models, entities, dimensions and simple metrics. `config.meta.snowflake` holds Cortex synonyms and instructions, never a formula |
| [models/metrics/_metrics.yml](models/metrics/_metrics.yml) | A ratio (portable everywhere) and a cumulative metric marked `dbt_only` |
| [scripts/semantic_bridge.py](scripts/semantic_bridge.py) | The Path B (default) pipeline and CI check: fails on lossy metrics, gets an Ossie document, checks Snowflake can load it, merges Cortex extras, writes the deploy SQL |
| [scripts/ossie-0.1.1-schema.json](scripts/ossie-0.1.1-schema.json) | The official Ossie 0.1.1 JSON schema (Apache-2.0, from `apache/ossie` at tag `osi-0.1.1-rc1`), the version Snowflake accepts |
| [models/qlik/qlik_metric_definitions.sql](models/qlik/qlik_metric_definitions.sql) | The modern version of the old Qlik macro: the definitions table, built from the same YAML |
| [macros/qlik.sql](macros/qlik.sql) | SQL to Qlik translation. Mirrors simple and ratio metrics only, and flags the rest |
| [qlik/load_metric_definitions.qvs](qlik/load_metric_definitions.qvs) | Qlik load script that turns each mirrored row into a variable |
| [tests/reconcile_semantic_view.sql](tests/reconcile_semantic_view.sql) | Reconciliation: the semantic view against plain SQL. Fails on any drift |
| [macros/snowflake_mcp.sql](macros/snowflake_mcp.sql) | `deploy_mcp_server`: the Snowflake-managed MCP server over the semantic view, its grants, and optionally the OAuth integration Claude Desktop connects through |
| [.github/workflows/semantic-layer-checks.yml](.github/workflows/semantic-layer-checks.yml) | Runs the portability check and the Snowflake readiness check on every pull request, with no warehouse: on dbt v2 (the client's version) as the primary check, dbt v1.12 as a cross-check |
| [CLAUDE.md](CLAUDE.md), [claude/](claude/), [.claude/skills/governed-metrics/](.claude/skills/governed-metrics/) | The soft guardrail: use governed metrics, never improvise a formula. Packaged as a skill, not just prose, so it's more reliably triggered |
| [.claude/hooks/](.claude/hooks/), [.claude/settings.json](.claude/settings.json) | The hard guardrail: a `PreToolUse` hook blocks an obvious hand-rolled formula written to a file; a `PostToolUse` hook re-runs the portability check the moment the YAML changes. Neither catches a wrong answer spoken in chat, only one written to disk |
| [.mcp.json](.mcp.json), [.env.example](.env.example) | Project-scoped MCP servers for Claude Code, with a `.env` for Path B or Path A. No credentials committed |

Names that change per environment (schema, view, role, warehouse) are dbt vars
in [dbt_project.yml](dbt_project.yml).

## Validate before you have Snowflake access

This project has one target: `snowflake`. Two steps need no live connection at
all, because `dbt parse` doesn't connect, and the bridge only reads `target/`:

```bash
export SNOWFLAKE_ACCOUNT=ci SNOWFLAKE_USER=ci            # any value; parse never connects
dbt parse --target snowflake
python3 scripts/semantic_bridge.py --check                      # portability check, standard library only
uv run scripts/semantic_bridge.py --schema ANALYTICS.SEMANTIC   # full bridge: Ossie doc + deploy SQL
```

That's the client's real path (dbt v2, the bridge's default `--source converter`),
and it runs end to end today: the check passes, and the bridge writes a
schema-valid `target/ossie/jaffle_shop.yaml` plus the
`CALL SYSTEM$CREATE_SEMANTIC_VIEW_FROM_OSSIE_YAML(...)` statement in
`target/deploy_semantic_views.sql`. What it can't prove without real
credentials: that Snowflake accepts that call, and that `dbt build` (which
creates the tables `qlik_metric_definitions` reads) succeeds against a live
warehouse. Once built, the Qlik table comes out as:

| metric_name | qlik_expression | sync_status | sync_note |
|-------------|-----------------|-------------|-----------|
| average_order_value | `($(m_revenue)) / ($(m_order_count))` | mirrored | |
| completed_revenue | `Sum(completed_amount)` | mirrored | |
| new_customers | `Count(DISTINCT customer_id)` | mirrored | |
| order_count | `Count(DISTINCT order_id)` | mirrored | |
| revenue | `Sum(amount)` | mirrored | |
| revenue_mtd | | not_mirrored | Cumulative metric: query it through the dbt Semantic Layer |

## Getting Ossie out of dbt

The client runs dbt v2, which doesn't write Ossie documents yet -- dbt Labs
plans to add it, but hasn't committed to a date. Until then there are two
working routes, and the bridge takes either:

| Route | How | Status |
|-------|-----|--------|
| dbt v2 + Apache converter | `dbt parse`, then `uv run scripts/semantic_bridge.py` | The default, and the client's real path. The pinned converter writes Ossie 0.2.0.dev0; the bridge rewraps it as 0.1.1 and validates it against the official schema. Runs end to end today, confirmed above |
| dbt v1.12, only for this step | `uvx --from 'dbt-core>=1.12,<1.13' --with dbt-snowflake dbt parse --target snowflake`, then the bridge with `--source dbt-v1` | Not needed for this client. Kept as a CI cross-check: dbt writes `osi_document.json` natively, already 0.1.1, and it should agree with the converter route. uvx runs v1 in its own environment, like a container would |
| dbt v2, native | Wait | On dbt Labs' roadmap, no committed date |

`require-dbt-version` allows both v1.12 and v2, so the same project serves
either route without edits.

## Deploy to Snowflake

Everything below this line needs real credentials, which aren't set up yet.
The sequence, once they are:

```bash
export SNOWFLAKE_ACCOUNT=... SNOWFLAKE_USER=...
dbt build --target snowflake
dbt parse --target snowflake                              # so Ossie sources point at Snowflake tables
uv run scripts/semantic_bridge.py --schema ANALYTICS.SEMANTIC
snow sql -f target/deploy_semantic_views.sql              # creates ANALYTICS.SEMANTIC.JAFFLE_SHOP
dbt run-operation deploy_mcp_server --target snowflake    # --args '{dry_run: true}' to print only
dbt test --target snowflake --select tag:reconciliation --vars '{semantic_views_deployed: true}'
```

In CI, [.github/workflows/semantic-layer-checks.yml](.github/workflows/semantic-layer-checks.yml)
runs the portability check and the Snowflake readiness check on every pull request without
touching the warehouse: the `checks` job on dbt v2 (the client's version), a
`cross-check` job on dbt v1.12. The reconciliation test runs after a deploy.

## Connect Claude

- **Claude Code:** copy `.env.example` to `.env` and fill in the Path B or
  Path A block. Export `DBT_PROJECT_DIR`, `SNOWFLAKE_ACCOUNT_URL` and
  `SNOWFLAKE_PAT` in your shell (plus `SNOWFLAKE_DATABASE`, `SEMANTIC_SCHEMA`
  and `SNOWFLAKE_MCP_SERVER` if they differ from the defaults), then open
  Claude Code in this folder. `.claude/settings.json` and
  `.claude/skills/governed-metrics/` are already project-scoped and need no
  setup
- **Claude Desktop:** add a custom connector, the Snowflake MCP server on
  Path B or the remote dbt MCP server on Path A (OAuth needs Starter or
  above). Then paste [claude/desktop_project_instructions.md](claude/desktop_project_instructions.md)
  into the project's instructions. On Claude Team and Enterprise, only an Owner
  can add the connector. Desktop doesn't run project hooks, so the skill (or
  the pasted instructions) is the only guardrail there
- **Snowflake OAuth:** `dbt run-operation deploy_mcp_server --args '{oauth_integration: true}'`
  creates the security integration, with a role that has CREATE INTEGRATION.
  Read its client ID and secret once with `system$show_oauth_client_secrets`
  and paste them into the connector; never commit them

## Guardrails, not just instructions

Three layers, in order of how hard they are to skip:

1. **The tool.** Snowflake MCP's metrics tool is easier to call than deriving
   a number by hand, so it's the path of least resistance
2. **The skill.** `.claude/skills/governed-metrics/` packages the CLAUDE.md
   rules with an explicit trigger, so they're more likely to fire than a
   paragraph in a longer instructions file. This is the only layer that
   reaches a spoken answer in chat, since nothing else can
3. **The hooks.** `.claude/hooks/block_hand_rolled_formulas.py` (`PreToolUse`
   on `Edit`/`Write`) blocks an obvious `sum()`/`count()`/`avg()` written
   outside `models/marts/`, `models/metrics/` or `macros/`, and
   `.claude/hooks/validate_semantic_yaml.py` (`PostToolUse`) re-runs the
   portability check the moment the semantic layer YAML changes, instead of
   waiting for CI. Both are heuristics, tested against real payloads (see
   git history), not a parser -- they catch the obvious case and cost a retry
   on a false positive, they don't prove correctness

Hooks only fire on tool calls. They cannot stop Claude from saying a wrong
number in plain conversation; only the skill and the tool being the easier
choice cover that half.

## Automating Qlik master items

Today, a human wires each master measure to its variable once, by hand, in
the Qlik UI (`$(m_revenue)` instead of a hardcoded `Sum(amount)`); every
reload after that keeps it current automatically. That one-time step can be
automated instead, and now is: [scripts/qlik_sync.py](scripts/qlik_sync.py)
pushes master measures over Qlik's Engine API.

```bash
uv run scripts/qlik_sync.py --definitions target/qlik_metric_definitions.json --check     # validates the input, no network
uv run scripts/qlik_sync.py --definitions target/qlik_metric_definitions.json --dry-run   # prints every payload, no network
uv run scripts/qlik_sync.py --definitions target/qlik_metric_definitions.json             # the real push, needs Qlik credentials
```

Master items aren't exposed through the simpler REST/QRS API, only the
WebSocket-based Engine API (QIX), confirmed against current Qlik docs. This
script is a small, purpose-built JSON-RPC client for it, not a wrapper
around the official `enigma.js` library, so it has exactly one job: for each
mirrored metric, check whether its measure already exists (`GetObject`) and
either update it (`SetProperties`) or create it (`CreateMeasure`).

**What's tested:** the input validation and the exact JSON-RPC payload for
every metric in the sample fixture
([tests/fixtures/qlik_metric_definitions.sample.json](tests/fixtures/qlik_metric_definitions.sample.json)),
both run locally with no network. **What isn't:** the live round trip.
There's no Qlik Cloud tenant to test against yet, the same gap as the live
Snowflake deploy. Test it against one metric in a real app before trusting
it in CI.

One caution that applies whether a measure is set by hand or pushed by this
script: nesting variables inside a master measure (any ratio metric, like
`average_order_value`) is a documented limitation, not just untested by us.
Qlik's own community reports that parameterized dollar-sign expansion
doesn't work in master measures at all, and that nesting multiple variables
fails inconsistently. Test it in a real app before relying on it. The
fallback is to have the bridge write the ratio's flattened expression as one
variable instead of composing two.

## Design rules this sketch enforces

- **Logic in one place.** The bridge rejects any `config.meta.snowflake` key
  other than `synonyms`, `instructions` and `examples`, so a second copy of a
  formula can't hide in meta
- **Nothing lossy reaches Snowflake silently.** Cumulative, conversion and
  private metrics, offsets and Jinja filters fail the check unless marked
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

Tested locally, against `--target snowflake`, dummy credentials, no live
connection:

- **dbt 2.0.6, the client's version:** `dbt parse --target snowflake` runs
  clean (one expected warning: semantic manifest validation is skipped
  without a dbt platform connection). Confirms parse never opens a warehouse
  connection
- **The bridge's default route end to end** (`--source converter`, the one
  the client will actually run): validation passes 5 portable metrics, 1
  `dbt_only` skipped; the Apache Ossie converter produces a schema-valid
  0.1.1 document, every field and metric table-qualified; Cortex extras
  merged into `ai_context`; `target/deploy_semantic_views.sql` comes out as
  a real, well-formed `CALL SYSTEM$CREATE_SEMANTIC_VIEW_FROM_OSSIE_YAML(...)`
- **The validation's fail path:** a lossy metric without `dbt_only`
  correctly fails the build
- **dbt v1.12.5, the cross-check route:** parses the project unchanged and
  writes `osi_document.json` natively as Ossie 0.1.1; `--source dbt-v1`
  passes the same Snowflake-readiness checks as the converter route, on the
  same metrics
- **Both CI jobs' commands**, run locally with the same dummy credentials CI
  uses
- **`deploy_mcp_server`** in dry-run mode, rendering the statements from the vars
- **`qlik_sync.py`**, both `--check` and `--dry-run`, against the sample
  fixture: correctly excludes the `not_mirrored` metric and builds a
  schema-correct `CreateMeasure`/`SetProperties` payload for each of the
  other five

Checked against current vendor docs, not run: `SYSTEM$CREATE_SEMANTIC_VIEW_FROM_OSSIE_YAML`
and `SYSTEM$READ_OSSIE_YAML_FROM_SEMANTIC_VIEW` are confirmed live in
Snowflake's docs (the `_OSI_YAML` names are now deprecated aliases); metric
qualification, `SEMANTIC_VIEW()` naming and query privileges; Qlik's LET, SET
and nested dollar-sign expansion; Claude Code's `.mcp.json` variables; Claude
custom connectors.

Not run yet, pending a live Snowflake account:

- **Everything that needs a warehouse connection**: `dbt build` (so the
  `qlik_metric_definitions` table doesn't exist yet), the actual
  `CALL SYSTEM$CREATE_SEMANTIC_VIEW_FROM_OSSIE_YAML`, the MCP server, the
  grants and the OAuth integration
- **Qlik**: the load script in a real app, and a chart using
  `$(m_average_order_value)`. This one is a real risk, not just unverified:
  Qlik's own community documents nested master-measure variables as fragile
- **The Qlik master-item push (`qlik_sync.py`) actually reaching Qlik**:
  the payloads are built and validated; the live round trip needs a Qlik
  Cloud tenant and API key
- **Semantic validation.** `dbt parse` skips it without a dbt platform
  connection, and `dbt sl validate` needs one

Answered while building this, from an open pre-session question list:

- dbt v2 doesn't write `osi_document.json` (2.0.6, local parse). dbt v1.12
  does, as Ossie 0.1.1
- Snowflake marks `SYSTEM$CREATE_SEMANTIC_VIEW_FROM_OSI_YAML` as deprecated.
  Use `..._FROM_OSSIE_YAML`

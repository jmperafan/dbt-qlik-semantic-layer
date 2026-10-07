# Semantic layer sketch: one definition, three consumers

**This branch (`demo`) is the empty starting point.** It's a plain two-model
Jaffle Shop dbt project -- `orders` and `customers`, with ordinary column docs
and tests, no semantic models, no metrics. The governance engine around it
(Snowflake semantic view deploy, Qlik mirror, Claude guardrails, CI/CD) is
fully built and wired up, running clean against zero metrics. The point is to
build the semantic layer on top of it from scratch.

A complete, worked version -- semantic models, a ratio metric, a cumulative
metric, the Qlik mirror table with real rows -- lives on the `main` branch.
Diff this file or `models/marts/_marts.yml` against `main` to see the
destination:

```bash
git diff main -- models/
```

```
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

Nothing flows through the top box yet on this branch -- it's the shape
metrics take once you add them, not a description of what's here today.

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
behind it.

## What's where

| File | What it shows |
|------|---------------|
| [models/marts/_marts.yml](models/marts/_marts.yml) | On this branch: plain descriptions and tests for `orders`/`customers`, no semantic models yet. This is where entities, dimensions and simple metrics get added -- see `main` for a filled-in example |
| `models/metrics/` | Doesn't exist on this branch. Add `_metrics.yml` for a ratio or cumulative metric, and `_time_spine.yml` + `metricflow_time_spine.sql` (the daily calendar MetricFlow requires) the moment any metric needs an offset or a cumulative window |
| [deploy/semantic_bridge.py](deploy/semantic_bridge.py) | The Path B (default) pipeline and CI check: fails on lossy metrics, gets an Ossie document, checks Snowflake can load it, merges Cortex extras, writes the deploy SQL. No module-level third-party imports, doesn't touch the network itself, so nearly all of it is unit tested with no mocking -- see [deploy/tests/](deploy/tests/) |
| [deploy/ossie-0.1.1-schema.json](deploy/ossie-0.1.1-schema.json) | The official Ossie 0.1.1 JSON schema (Apache-2.0, from `apache/ossie` at tag `osi-0.1.1-rc1`), the version Snowflake accepts |
| [deploy/snowflake_admin.py](deploy/snowflake_admin.py) | `qlik-table`: translates every metric into a Qlik expression (a bare-column aggregation, or a ratio of two mirrored metrics) and writes `qlik_metric_definitions`, plus the JSON `sync.py` reads. `mcp-server`: the Snowflake-managed MCP server over the semantic view, its grants, and optionally the OAuth integration Claude Desktop connects through |
| [deploy/snowflake_connection.py](deploy/snowflake_connection.py) | The one shared "connect to Snowflake outside of dbt" helper, used by `snowflake_admin.py` and the reconciliation test |
| [deploy/qlik/load_metric_definitions.qvs](deploy/qlik/load_metric_definitions.qvs), [deploy/qlik/sync.py](deploy/qlik/sync.py) | Qlik load script that turns each mirrored row into a variable, and the optional automation that pushes master measures over the Engine API. See ["Qlik"](#qlik) below |
| [deploy/tests/](deploy/tests/) | Unit tests for everything in `deploy/`, against fixtures -- unaffected by this branch having zero real metrics. No network, no credentials, runs in CI. `test_reconcile_semantic_view.py` is the one exception: it needs a live, deployed semantic view, so it skips itself unless `RECONCILE_LIVE=true` |
| [.github/workflows/dbt-sl-ci.yml](.github/workflows/dbt-sl-ci.yml) | Runs the portability check and the Snowflake readiness check on every pull request, with no warehouse. See ["Current state"](#current-state-zero-metrics-by-design) below for why the readiness check currently fails here |
| [.github/workflows/dbt-sl-cd.yml](.github/workflows/dbt-sl-cd.yml) | Runs the full deploy sequence on every merge to main -- see ["Deploy to Snowflake"](#deploy-to-snowflake) |
| [CLAUDE.md](CLAUDE.md), [.claude/desktop_project_instructions.md](.claude/desktop_project_instructions.md), [.claude/skills/governed-metrics/](.claude/skills/governed-metrics/) | The soft guardrail: use governed metrics, never improvise a formula. Wired up and ready; there's nothing to govern yet on this branch |
| [.claude/hooks/](.claude/hooks/), [.claude/settings.json](.claude/settings.json) | The hard guardrail: a `PreToolUse` hook blocks an obvious hand-rolled formula written to a file; a `PostToolUse` hook re-runs the portability check the moment the YAML changes |
| [.mcp.json](.mcp.json), [.env.example](.env.example) | Project-scoped MCP servers for Claude Code, with a `.env` for Path B or Path A. No credentials committed |

Names that change per environment (schema, view, role, warehouse) are dbt vars
in [dbt_project.yml](dbt_project.yml).

## Current state: zero metrics, by design

```bash
export SNOWFLAKE_ACCOUNT=ci SNOWFLAKE_USER=ci   # any value; parse never connects
dbt parse --target snowflake
python3 deploy/semantic_bridge.py --check       # OK: 0 metrics portable to Snowflake
```

The check passes trivially -- there's nothing lossy because there's nothing
yet. Two things do **not** work until at least one semantic model exists:

- **The full bridge run** (`uv run deploy/semantic_bridge.py`, no `--check`)
  fails: `Snowflake can't load this document intact: semantic_model/0/datasets:
  [] should be non-empty`. Snowflake's Ossie schema requires at least one
  dataset, and there are none.
- **CI's `checks` job** runs that same full-bridge call as its "Snowflake
  readiness" step, so it currently fails on every PR into this branch, for
  the identical reason. Not a bug -- it clears the moment `_marts.yml` has a
  semantic model.

Everything else is unaffected: `dbt build` processes the 2 models / 7 tests /
2 seeds normally, `uv run deploy/snowflake_admin.py qlik-table --check`
reports `OK: 0/0 metrics mirror to Qlik`, and `deploy/tests/` passes in full
(95 passed, 1 skipped) since those tests run against fixtures, not this
repo's own models.

## Getting Ossie out of dbt

dbt v2 doesn't write Ossie documents yet -- dbt Labs plans to add it, but
hasn't committed to a date. Until then there are two working routes, and the
bridge takes either:

| Route | How | Status |
|-------|-----|--------|
| dbt v2 + Apache converter | `dbt parse`, then `uv run deploy/semantic_bridge.py` | The default route. The pinned converter writes Ossie 0.2.0.dev0; the bridge rewraps it as 0.1.1 and validates it against the official schema |
| dbt v1.12, only for this step | `uvx --from 'dbt-core>=1.12,<1.13' --with dbt-snowflake dbt parse --target snowflake`, then the bridge with `--source dbt-v1` | Optional. Useful as a CI cross-check: dbt writes `osi_document.json` natively, already 0.1.1, and it should agree with the converter route. uvx runs v1 in its own environment, like a container would |
| dbt v2, native | Wait | On dbt Labs' roadmap, no committed date |

`require-dbt-version` allows both v1.12 and v2, so the same project serves
either route without edits. Both routes need at least one semantic model to
produce a loadable document -- see ["Current state"](#current-state-zero-metrics-by-design).

## Building the semantic layer from here

1. **Add a semantic model.** In `models/marts/_marts.yml`, add
   `semantic_model: {enabled: true, name: ...}`, an `agg_time_dimension`, and
   `entity:` blocks on the relevant columns. `main`'s version of this file is
   a filled-in example (`orders` and `customers`, each with entities and a
   synonym/instructions block under `config.meta.snowflake`).
2. **Add a metric.** Either inline under the semantic model (a simple
   aggregation) or in a new `models/metrics/_metrics.yml` (a ratio, or a
   cumulative/conversion/private metric marked `config.meta.dbt_only: true`,
   since the converter drops those without warning). A cumulative or offset
   metric also needs the time spine -- add `models/metrics/_time_spine.yml`
   and `metricflow_time_spine.sql`.
3. **Validate.** `dbt parse --target snowflake`, then
   `python3 deploy/semantic_bridge.py --check`. Fix each failure, or mark the
   metric `config.meta.dbt_only: true` as a reviewed decision. Once at least
   one dataset exists, `uv run deploy/semantic_bridge.py` and
   `uv run deploy/snowflake_admin.py qlik-table --check` both start producing
   real output instead of the zero-metric case above.
4. **Open a PR.** `dbt-sl-ci.yml` re-runs the same checks -- this is the
   point where the "Current state" failure above should disappear.
5. **Merge.** `dbt-sl-cd.yml` builds, deploys the semantic view, serves it
   over MCP, reconciles, and pushes mirrored metrics to Qlik -- see ["Deploy
   to Snowflake"](#deploy-to-snowflake) for the same sequence run by hand.
6. **Claude.** `CLAUDE.md`, the Desktop connector and
   `.claude/skills/governed-metrics/` are already in place and need no
   changes -- see ["Connect Claude"](#connect-claude).

### What varies per project

If you're adapting this sketch (rather than just filling it in for this
demo), these are the only project-specific names -- everything else in
`deploy/`, the hooks and the skill reads them or the project graph directly:

| Where | Default here | Set to |
|-------|--------------|--------|
| dbt var `semantic_schema` / `semantic_view` | `semantic` / `jaffle_shop` | The schema and name for your semantic view |
| dbt var `metrics_reader_role` / `mcp_warehouse` | `metrics_reader` / `transforming` | The role Claude Desktop queries as, and the warehouse Cortex Analyst runs on |
| Qlik `vConnection`, `vMetricsTable` | `Snowflake`, `ANALYTICS.JAFFLE_SHOP.QLIK_METRIC_DEFINITIONS` | Your Qlik connection and where `qlik_metric_definitions` builds |
| Env `SNOWFLAKE_DATABASE`, `SEMANTIC_SCHEMA`, `SNOWFLAKE_MCP_SERVER` | `ANALYTICS`, `SEMANTIC`, `JAFFLE_SHOP_MCP` | Your database, schema, and `<SEMANTIC_VIEW>_MCP` |
| `CLAUDE.md` tool name | `jaffle-shop-metrics` | `<semantic_view>-metrics`, with dashes |

`profiles.yml` and the seeds are local-dev/CI scaffolding, not something the
dbt platform itself needs: there, connections come from the environment you
configure in its own UI, not a profiles file. Skip both if you're only ever
running on the platform.

## Deploy to Snowflake

[.github/workflows/dbt-sl-cd.yml](.github/workflows/dbt-sl-cd.yml) runs this
whole sequence on every merge to main -- edit the YAML, open a PR, merge, and
the rest happens without anyone running these by hand. The same sequence, for
running it yourself once at least one metric exists:

```bash
export SNOWFLAKE_ACCOUNT=... SNOWFLAKE_USER=... SNOWFLAKE_PRIVATE_KEY_PATH=...  # key-pair auth; add SNOWFLAKE_PRIVATE_KEY_PASSPHRASE=... if the key is encrypted
dbt build --target snowflake
dbt parse --target snowflake                              # so Ossie sources point at Snowflake tables
uv run deploy/semantic_bridge.py                          # --schema/--model-name default from dbt_project.yml
snow sql -f target/deploy_semantic_views.sql              # creates/updates ANALYTICS.SEMANTIC.JAFFLE_SHOP
uv run deploy/snowflake_admin.py mcp-server               # --dry-run to print only; --oauth-integration for Claude Desktop
RECONCILE_LIVE=true uv run --with pytest --with snowflake-connector-python --with pyyaml \
    pytest deploy/tests/test_reconcile_semantic_view.py -v
uv run deploy/snowflake_admin.py qlik-table --write-json target/qlik_metric_definitions.json
uv run deploy/qlik/sync.py --definitions target/qlik_metric_definitions.json
```

`snowflake_admin.py mcp-server`'s grants need a role with `MANAGE GRANTS` or
ownership -- set `SNOWFLAKE_ROLE` if the account's default build role can't
grant to `metrics_reader_role`. In the CD workflow this step runs with
`continue-on-error: true` so a permissions gap there doesn't block the rest
of the pipeline.

In CI, [.github/workflows/dbt-sl-ci.yml](.github/workflows/dbt-sl-ci.yml)
runs the portability check and the Snowflake readiness check on every pull
request without touching the warehouse: the `checks` job on dbt v2, an
optional `cross-check` job on dbt v1.12. The reconciliation test and the Qlik
push only run from the deploy workflow, after a real deploy.

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
  above). Then paste [.claude/desktop_project_instructions.md](.claude/desktop_project_instructions.md)
  into the project's instructions. On Claude Team and Enterprise, only an Owner
  can add the connector. Desktop doesn't run project hooks, so the skill (or
  the pasted instructions) is the only guardrail there
- **Snowflake OAuth:** `uv run deploy/snowflake_admin.py mcp-server --oauth-integration`
  creates the security integration, with a role that has CREATE INTEGRATION.
  Read its client ID and secret once with `system$show_oauth_client_secrets`
  and paste them into the connector; never commit them

## Guardrails, not just instructions

Three layers, in order of how hard they are to skip. All three are wired up
on this branch already; there's just nothing to govern until a metric exists.

1. **The tool.** Snowflake MCP's metrics tool is easier to call than deriving
   a number by hand, so it's the path of least resistance
2. **The skill.** `.claude/skills/governed-metrics/` packages the CLAUDE.md
   rules with an explicit trigger, so they're more likely to fire than a
   paragraph in a longer instructions file. This is the only layer that
   reaches a spoken answer in chat, since nothing else can
3. **The hooks.** `.claude/hooks/block_hand_rolled_formulas.py` (`PreToolUse`
   on `Edit`/`Write`) blocks an obvious `sum()`/`count()`/`avg()` written
   outside `models/marts/` or `models/metrics/`, and
   `.claude/hooks/validate_semantic_yaml.py` (`PostToolUse`) re-runs the
   portability check the moment the semantic layer YAML changes, instead of
   waiting for CI. Both are heuristics, tested against real payloads, not a
   parser -- they catch the obvious case and cost a retry on a false
   positive, they don't prove correctness

Hooks only fire on tool calls. They cannot stop Claude from saying a wrong
number in plain conversation; only the skill and the tool being the easier
choice cover that half.

## Qlik

Two places touch Qlik, both plain scripts outside dbt's layout, grouped
under `deploy/`:

| Where | What |
|-------|------|
| [deploy/snowflake_admin.py](deploy/snowflake_admin.py) (`qlik-table` subcommand) | Translates every metric into a Qlik expression (`qlik_expression`/`qlik_not_mirrored_reason`) and writes `qlik_metric_definitions`, one row per metric, plus the JSON `sync.py` reads (`--write-json`). On this branch that's zero rows -- `--check` reports `OK: 0/0 metrics mirror to Qlik` |
| [deploy/qlik/load_metric_definitions.qvs](deploy/qlik/load_metric_definitions.qvs) | The Qlik load script itself. Paste it into the Qlik app; it reads `qlik_metric_definitions` and turns each mirrored row into a `$(m_<metric>)` variable |
| [deploy/qlik/sync.py](deploy/qlik/sync.py) | Optional automation: pushes master measures over Qlik's Engine API instead of wiring each one by hand |

**Automating master items.** By default, a human wires each master measure to
its variable once, by hand, in the Qlik UI (`$(m_revenue)` instead of a
hardcoded `Sum(amount)`); every reload after that keeps it current
automatically. That one-time step can be automated instead:
`deploy/qlik/sync.py` pushes master measures over Qlik's Engine API.

```bash
uv run deploy/qlik/sync.py --definitions target/qlik_metric_definitions.json --check     # validates the input, no network
uv run deploy/qlik/sync.py --definitions target/qlik_metric_definitions.json --dry-run   # prints every payload, no network
uv run deploy/qlik/sync.py --definitions target/qlik_metric_definitions.json             # the real push, needs Qlik credentials
```

Master items aren't exposed through the simpler REST/QRS API, only the
WebSocket-based Engine API (QIX), confirmed against current Qlik docs. This
script is a small, purpose-built JSON-RPC client for it, not a wrapper
around the official `enigma.js` library, so it has exactly one job: for each
mirrored metric, check whether its measure already exists (`GetMeasure`) and
either update it (`SetProperties`) or create it (`CreateMeasure`).

**Confirmed live against a real Qlik Cloud tenant**, including the one real
network path the input validation and payload tests can't reach
(`sync_to_qlik`). That run caught a genuine bug, now fixed: the existence
check originally used `GetObject`, which never resolves a master measure by
qId -- confirmed with a controlled test, it returned a null handle even
immediately after creating that exact object in the same session.
`CreateMeasure` doesn't error on a duplicate qId either, it just mints a new
object with a random id, so every rerun under the old code was silently
piling up a duplicate measure instead of updating one in place. `GetMeasure`
is the qId-based lookup that actually works. If you ran this script before
this fix, check your app's master items for duplicates (same label, a
random-looking qId instead of `m_<metric_name>`) and delete them.

One caution that applies whether a measure is set by hand or pushed by this
script: nesting variables inside a master measure (any ratio metric) is a
documented Qlik community limitation elsewhere -- parameterized dollar-sign
expansion reportedly not working in master measures at all, nesting multiple
variables failing inconsistently. **Tested live and it isn't a problem
here**: creating two metrics as real variables and evaluating a ratio master
measure through the Engine API returned the correct computed ratio, nested
variables and all. If you see different behavior in your own app, the
fallback is to have `snowflake_admin.py` write the ratio's flattened
expression as one variable instead of composing two.

**A separate gotcha, confirmed live, worth checking before trusting any
number on a chart:** a master measure pushed by `sync.py` evaluates fine
even if the app's data model has no matching field loaded at all --
`Sum(amount)` over a missing `amount` field returns a clean `0`, not an
error. A measure existing and returning a plausible-looking value is not
proof the underlying data connection and load script have actually run;
check the app's data model (or just look at the source table in the UI)
separately.

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

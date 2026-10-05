# Qlik

Four places touch Qlik in this project. Two are locked in place by dbt's own
conventions -- macros and models have to live under `macros/` and `models/`
for dbt to find them, the same reason [MIGRATION.md](../../MIGRATION.md) tells
you to copy them into your own `macros/`/`models/qlik/` verbatim. This
directory holds the other two: the actual Qlik load script, and the optional
Python automation that pairs with it, grouped under `bridge/` with the
Snowflake half since both are plain scripts outside dbt's layout.

| Where | What |
|-------|------|
| [../../macros/metrics/qlik.sql](../../macros/metrics/qlik.sql) | SQL to Qlik translation: `qlik_expression`, `qlik_not_mirrored_reason`, `sql_string`. Must live under `macros/` (dbt convention) -- see [../../macros/metrics/README.md](../../macros/metrics/README.md) |
| [../../models/qlik/](../../models/qlik/) | The dbt model that builds `qlik_metric_definitions` from `graph.metrics`, one row per metric. Must live under `models/` (dbt convention) |
| [load_metric_definitions.qvs](load_metric_definitions.qvs) | The Qlik load script itself. Paste it into the Qlik app; it reads `qlik_metric_definitions` and turns each mirrored row into a `$(m_<metric>)` variable |
| [sync.py](sync.py) | Optional automation: pushes master measures over Qlik's Engine API instead of wiring each one by hand. Everything below is about this script |

## Automating master items

Today, a human wires each master measure to its variable once, by hand, in
the Qlik UI (`$(m_revenue)` instead of a hardcoded `Sum(amount)`); every
reload after that keeps it current automatically. That one-time step can be
automated instead, and now is: [sync.py](sync.py) pushes master measures over
Qlik's Engine API.

```bash
uv run bridge/qlik/sync.py --definitions target/qlik_metric_definitions.json --check     # validates the input, no network
uv run bridge/qlik/sync.py --definitions target/qlik_metric_definitions.json --dry-run   # prints every payload, no network
uv run bridge/qlik/sync.py --definitions target/qlik_metric_definitions.json             # the real push, needs Qlik credentials
```

Master items aren't exposed through the simpler REST/QRS API, only the
WebSocket-based Engine API (QIX), confirmed against current Qlik docs. This
script is a small, purpose-built JSON-RPC client for it, not a wrapper
around the official `enigma.js` library, so it has exactly one job: for each
mirrored metric, check whether its measure already exists (`GetObject`) and
either update it (`SetProperties`) or create it (`CreateMeasure`).

**What's tested:** the input validation, the exact JSON-RPC payload for every
metric in the sample fixture
([../tests/fixtures/qlik_metric_definitions.sample.json](../tests/fixtures/qlik_metric_definitions.sample.json)),
and the create/update branching, all in
[../tests/test_qlik_sync.py](../tests/test_qlik_sync.py) -- no
network needed. **What isn't:** the live round trip (`sync_to_qlik`). There's
no Qlik Cloud tenant to test against yet. Test it against one metric in a
real app before trusting it in CI.

One caution that applies whether a measure is set by hand or pushed by this
script: nesting variables inside a master measure (any ratio metric, like
`average_order_value`) is a documented limitation, not just untested by us.
Qlik's own community reports that parameterized dollar-sign expansion
doesn't work in master measures at all, and that nesting multiple variables
fails inconsistently. Test it in a real app before relying on it. The
fallback is to have the bridge write the ratio's flattened expression as one
variable instead of composing two.

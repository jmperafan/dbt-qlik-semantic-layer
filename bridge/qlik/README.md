# Qlik

Three places touch Qlik in this project, all plain scripts outside dbt's
layout, grouped under `bridge/`. `qlik_expression`/`qlik_not_mirrored_reason`
and the table-writing logic used to be a dbt macro and model (locked under
`macros/`/`models/` by dbt convention) -- moved into `snowflake_admin.py` so
they're unit tested, the same reason [MIGRATION.md](../../MIGRATION.md)'s
step order no longer mentions copying Qlik macros into `macros/`/`models/qlik/`.

| Where | What |
|-------|------|
| [../snowflake_admin.py](../snowflake_admin.py) (`qlik-table` subcommand) | Translates every metric into a Qlik expression (`qlik_expression`/`qlik_not_mirrored_reason`) and writes `qlik_metric_definitions`, one row per metric, plus the JSON `sync.py` reads (`--write-json`) |
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

**What's tested:** input validation, the exact JSON-RPC payload for every
metric in the sample fixture
([../tests/fixtures/qlik_metric_definitions.sample.json](../tests/fixtures/qlik_metric_definitions.sample.json)),
the create/update branching, and now the live round trip itself, all in
[../tests/test_qlik_sync.py](../tests/test_qlik_sync.py) plus the live run
above.

One caution that applies whether a measure is set by hand or pushed by this
script: nesting variables inside a master measure (any ratio metric, like
`average_order_value`) is a documented Qlik community limitation elsewhere --
parameterized dollar-sign expansion reportedly not working in master
measures at all, nesting multiple variables failing inconsistently. **Tested
live and it isn't a problem here**: creating `m_revenue`/`m_order_count` as
real variables and evaluating the `average_order_value` master measure
through the Engine API returned the correct computed ratio, nested variables
and all. If you see different behavior in your own app, the fallback is to
have the bridge write the ratio's flattened expression as one variable
instead of composing two.

**A separate gotcha, confirmed live, worth checking before trusting any
number on a chart:** a master measure pushed by `sync.py` evaluates fine
even if the app's data model has no matching field loaded at all --
`Sum(amount)` over a missing `amount` field returns a clean `0`, not an
error. A measure existing and returning a plausible-looking value is not
proof the underlying data connection and load script have actually run;
check the app's data model (or just look at the source table in the UI)
separately.

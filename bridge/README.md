# bridge

Everything that carries the governed metric definitions out to Snowflake and
Qlik, grouped here as plain Python scripts outside dbt's own layout, each
unit tested in [tests/](tests/).

| Where | What it is |
|-------|------------|
| [semantic_bridge.py](semantic_bridge.py) | MetricFlow YAML -> Apache Ossie -> Snowflake semantic view. Run with `uv run bridge/semantic_bridge.py --check` (portability only) or `--schema DATABASE.SCHEMA` (full run). See its module docstring for the full pipeline |
| [ossie-0.1.1-schema.json](ossie-0.1.1-schema.json) | The official Ossie 0.1.1 JSON schema (Apache-2.0), vendored so `semantic_bridge.py` can validate against it offline |
| [snowflake_admin.py](snowflake_admin.py) | `qlik-table` (the metric-to-Qlik-expression translation and the `qlik_metric_definitions` table) and `mcp-server` (the Snowflake-managed MCP server and its grants) |
| [snowflake_connection.py](snowflake_connection.py) | The one shared "connect to Snowflake outside of dbt" helper, used by `snowflake_admin.py` and `tests/test_reconcile_semantic_view.py` |
| [qlik/](qlik/) | The Qlik-facing half of the bridge: the load script and the optional master-item push automation. See [qlik/README.md](qlik/README.md) |
| [tests/](tests/) | Unit tests for `semantic_bridge.py` and `qlik/sync.py` -- no network, no credentials needed, runs in CI |

## Tests

`semantic_bridge.py` has no module-level third-party imports and never
touches the network itself (the actual Snowflake execution happens outside
it, via `snow sql` or a connector script), so nearly all of it is unit-tested
with no mocking:

```bash
uv run --with pytest --with pyyaml --with jsonschema pytest bridge/tests -v
```

See [tests/test_semantic_bridge.py](tests/test_semantic_bridge.py).
The one path deliberately left out is `from_converter` (needs the pinned git
dependency `apache-ossie-dbt`), which the `checks` job in
[../.github/workflows/semantic-layer-checks.yml](../.github/workflows/semantic-layer-checks.yml)
already exercises for real, against a live Ossie conversion.

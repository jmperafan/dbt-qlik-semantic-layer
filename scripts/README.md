# scripts

| File | What it is |
|------|------------|
| [semantic_bridge.py](semantic_bridge.py) | MetricFlow YAML -> Apache Ossie -> Snowflake semantic view. Run with `uv run scripts/semantic_bridge.py --check` (portability only) or `--schema DATABASE.SCHEMA` (full run). See its module docstring for the full pipeline |
| [ossie-0.1.1-schema.json](ossie-0.1.1-schema.json) | The official Ossie 0.1.1 JSON schema (Apache-2.0), vendored so `semantic_bridge.py` can validate against it offline |

Looking for `qlik_sync.py`? It moved to
[qlik/sync.py](../qlik/sync.py) -- see [qlik/README.md](../qlik/README.md).

## Tests

`semantic_bridge.py` has no module-level third-party imports and never
touches the network itself (the actual Snowflake execution happens outside
it, via `snow sql` or a connector script), so nearly all of it is unit-tested
with no mocking:

```bash
uv run --with pytest --with pyyaml --with jsonschema pytest tests/python -v
```

See [tests/python/test_semantic_bridge.py](../tests/python/test_semantic_bridge.py).
The one path deliberately left out is `from_converter` (needs the pinned git
dependency `apache-ossie-dbt`), which the `checks` job in
[.github/workflows/semantic-layer-checks.yml](../.github/workflows/semantic-layer-checks.yml)
already exercises for real, against a live Ossie conversion.

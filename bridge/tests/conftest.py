"""Shared fixtures for the bridge/ unit tests.

Neither bridge/semantic_bridge.py nor bridge/qlik/sync.py is a package (no
__init__.py, no pyproject.toml) -- they're single-file `uv run` scripts, on
purpose, so `uv run bridge/semantic_bridge.py ...` works with zero install
step. To test them as plain Python modules, this file puts bridge/ and
bridge/qlik/ on sys.path so tests can `import semantic_bridge` / `import sync`
directly, the standard recipe for testing a script with no packaging.

Run with: uv run --with pytest --with pyyaml --with jsonschema pytest bridge/tests -v
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
for directory in ("bridge", "bridge/qlik"):
    path = str(REPO_ROOT / directory)
    if path not in sys.path:
        sys.path.insert(0, path)

import pytest

# ---------------------------------------------------------------------------
# Ossie document builders. Small and named, not one static JSON fixture: the
# tests below need many different shapes (a valid minimal document, one with
# relationships and dimensions, orphaned extras, ambiguous cross-table
# metrics, ...), and mutating a single loaded blob per test reads worse than
# a handful of `ossie_*(...)` calls that show only what each test cares
# about. Every builder produces something that validates against the real
# vendored bridge/ossie-0.1.1-schema.json -- required fields (checked
# against the schema itself, not guessed): SemanticModel needs
# name+datasets (datasets non-empty), Dataset needs name+source, Field and
# Metric need name+expression, Relationship needs name/from/to/
# from_columns/to_columns.
# ---------------------------------------------------------------------------


def ossie_expression(expr: str, dialect: str = "ANSI_SQL") -> dict:
    return {"dialects": [{"dialect": dialect, "expression": expr}]}


def ossie_field(name: str, expr: str, *, dialect="ANSI_SQL", dimension=None, description=None, ai_context=None) -> dict:
    field = {"name": name, "expression": ossie_expression(expr, dialect)}
    if dimension is not None:
        field["dimension"] = dimension
    if description is not None:
        field["description"] = description
    if ai_context is not None:
        field["ai_context"] = ai_context
    return field


def ossie_dataset(name: str, source: str, *, primary_key=None, fields=None, description=None, ai_context=None) -> dict:
    dataset = {"name": name, "source": source}
    if primary_key is not None:
        dataset["primary_key"] = primary_key
    if fields is not None:
        dataset["fields"] = fields
    if description is not None:
        dataset["description"] = description
    if ai_context is not None:
        dataset["ai_context"] = ai_context
    return dataset


def ossie_metric(name: str, expr: str, *, dialect="ANSI_SQL", description=None, ai_context=None) -> dict:
    metric = {"name": name, "expression": ossie_expression(expr, dialect)}
    if description is not None:
        metric["description"] = description
    if ai_context is not None:
        metric["ai_context"] = ai_context
    return metric


def ossie_relationship(name: str, from_table: str, to_table: str, from_columns: list[str], to_columns: list[str]) -> dict:
    return {"name": name, "from": from_table, "to": to_table, "from_columns": from_columns, "to_columns": to_columns}


def ossie_document(*, datasets=None, relationships=None, metrics=None, model_name="test_model") -> dict:
    model = {"name": model_name, "datasets": datasets if datasets is not None else [ossie_dataset("t", "DB.SCHEMA.t")]}
    if relationships is not None:
        model["relationships"] = relationships
    if metrics is not None:
        model["metrics"] = metrics
    return {"version": "0.1.1", "semantic_model": [model]}


def mf_metric(name: str, type: str = "simple", *, type_params=None, filter=None, dbt_only=False) -> dict:
    """A MetricFlow-shaped metric dict, as found in target/semantic_manifest.json
    -- the input to unportable_reasons/check_portability, distinct from the
    Ossie-shaped metrics above (check_portability's OUTPUT)."""
    metric = {"name": name, "type": type, "type_params": type_params or {}}
    if filter is not None:
        metric["filter"] = filter
    if dbt_only:
        metric["config"] = {"meta": {"dbt_only": True}}
    return metric


@pytest.fixture
def sample_qlik_definitions_path() -> Path:
    return REPO_ROOT / "bridge" / "tests" / "fixtures" / "qlik_metric_definitions.sample.json"

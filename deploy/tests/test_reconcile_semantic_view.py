"""Ported from tests/reconcile_semantic_view.sql: compares every metric in
the Snowflake semantic view against plain SQL over the same models, by
region. The reference SQL below is written independently on purpose -- it
must not reuse the semantic layer's logic, or it can't catch a translation
bug.

Needs a live, already-deployed semantic view, so unlike the rest of
deploy/tests it isn't run by default: skipped unless SNOWFLAKE_ACCOUNT and
RECONCILE_LIVE=true are both set.

  RECONCILE_LIVE=true uv run --with pytest --with snowflake-connector-python \\
      --with pyyaml pytest deploy/tests/test_reconcile_semantic_view.py -v
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

import semantic_bridge
import snowflake_connection

pytestmark = pytest.mark.skipif(
    not (os.environ.get("SNOWFLAKE_ACCOUNT") and os.environ.get("RECONCILE_LIVE") == "true"),
    reason="needs a live, deployed Snowflake semantic view (set RECONCILE_LIVE=true to run)",
)

METRICS = ["revenue", "order_count", "completed_revenue", "average_order_value", "new_customers"]


def relation_name(manifest: dict, model_name: str) -> str:
    for node in manifest["nodes"].values():
        if node.get("resource_type") == "model" and node.get("name") == model_name:
            return node["relation_name"]
    raise KeyError(f"no model node named {model_name!r} in manifest.json")


def reconciliation_query(schema: str, view: str, orders: str, customers: str) -> str:
    metric_columns = ", ".join(f"reference.{m} as reference_{m}, semantic.{m} as semantic_{m}" for m in METRICS)
    return f"""
    with semantic as (
        select *
        from semantic_view(
            {schema}.{view}
            metrics {", ".join(METRICS)}
            dimensions region
        )
    ),
    order_metrics as (
        select
            customers.region,
            sum(orders.amount) as revenue,
            count(distinct orders.order_id) as order_count,
            sum(case when orders.status = 'completed' then orders.amount else 0 end) as completed_revenue
        from {orders} as orders
        left join {customers} as customers on orders.customer_id = customers.customer_id
        group by 1
    ),
    customer_metrics as (
        select region, count(distinct customer_id) as new_customers
        from {customers}
        group by 1
    ),
    reference as (
        select
            coalesce(order_metrics.region, customer_metrics.region) as region,
            order_metrics.revenue,
            order_metrics.order_count,
            order_metrics.completed_revenue,
            order_metrics.revenue / nullif(order_metrics.order_count, 0) as average_order_value,
            customer_metrics.new_customers
        from order_metrics
        full outer join customer_metrics
            on order_metrics.region is not distinct from customer_metrics.region
    )
    select coalesce(reference.region, semantic.region) as region, {metric_columns}
    from reference
    full outer join semantic
        on reference.region is not distinct from semantic.region
    """


def drifted_rows(rows: list[dict]) -> list[tuple]:
    """A null on one side only is drift; so is any difference beyond rounding."""
    drifted = []
    for row in rows:
        for metric in METRICS:
            reference_value, semantic_value = row[f"reference_{metric}"], row[f"semantic_{metric}"]
            if reference_value is None or semantic_value is None:
                if reference_value != semantic_value:
                    drifted.append((row["region"], metric, reference_value, semantic_value))
            elif abs(reference_value - semantic_value) >= 0.0001:
                drifted.append((row["region"], metric, reference_value, semantic_value))
    return drifted


def test_semantic_view_matches_plain_sql():
    manifest = json.loads(Path("target/manifest.json").read_text())
    schema, view = semantic_bridge.project_defaults(manifest)
    if not schema or not view:
        pytest.fail("couldn't default --schema/--view from dbt_project.yml + manifest.json")

    query = reconciliation_query(schema, view, relation_name(manifest, "orders"), relation_name(manifest, "customers"))

    conn = snowflake_connection.connect()
    try:
        cursor = conn.cursor()
        cursor.execute(query)
        columns = [c[0].lower() for c in cursor.description]
        rows = [dict(zip(columns, row)) for row in cursor.fetchall()]
    finally:
        conn.close()

    assert not drifted_rows(rows), f"metrics drifted between the semantic view and plain SQL: {drifted_rows(rows)}"

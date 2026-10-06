"""Unit tests for snowflake_admin.py -- the former macros/metrics/qlik.sql
and macros/metrics/snowflake_mcp.sql. No network, no credentials: everything
here is pure translation, same as semantic_bridge.py's own tests."""
from __future__ import annotations

from conftest import mf_metric

import snowflake_admin as admin


def simple(name, agg="sum", expr="amount", filter=None, label=None, description=None):
    metric = mf_metric(name, "simple", type_params={"expr": expr, "metric_aggregation_params": {"agg": agg}}, filter=filter)
    if label:
        metric["label"] = label
    if description:
        metric["description"] = description
    return metric


def ratio(name, numerator, denominator, num_filter=None, den_filter=None):
    return mf_metric(
        name,
        "ratio",
        type_params={
            "numerator": {"name": numerator, "filter": num_filter},
            "denominator": {"name": denominator, "filter": den_filter},
        },
    )


class TestQlikExpression:
    def test_simple_sum_on_bare_column(self):
        assert admin.qlik_expression(simple("revenue", "sum", "amount"), {}) == "Sum(amount)"

    def test_simple_count_distinct(self):
        metric = simple("order_count", "count_distinct", "order_id")
        assert admin.qlik_expression(metric, {}) == "Count(DISTINCT order_id)"

    def test_simple_filtered_metric_not_mirrored(self):
        metric = simple("completed_revenue", "sum", "amount", filter="{{ Dimension('order__status') }} = 'completed'")
        assert admin.qlik_expression(metric, {}) == ""

    def test_simple_unsupported_aggregation_not_mirrored(self):
        metric = simple("p90_latency", agg="percentile", expr="latency_ms")
        assert admin.qlik_expression(metric, {}) == ""

    def test_simple_expression_with_a_function_call_not_mirrored(self):
        # Not a bare column -- qlik_expression only trusts a plain `expr`.
        metric = simple("revenue", "sum", "coalesce(amount, 0)")
        assert admin.qlik_expression(metric, {}) == ""

    def test_ratio_of_two_mirrored_metrics(self):
        metrics = {"revenue": simple("revenue", "sum", "amount"), "order_count": simple("order_count", "count_distinct", "order_id")}
        metric = ratio("average_order_value", "revenue", "order_count")
        assert admin.qlik_expression(metric, metrics) == "($(m_revenue)) / ($(m_order_count))"

    def test_ratio_with_filtered_numerator_not_mirrored(self):
        metrics = {"revenue": simple("revenue"), "order_count": simple("order_count", "count_distinct", "order_id")}
        metric = ratio("x", "revenue", "order_count", num_filter="{{ Dimension('x') }} = 1")
        assert admin.qlik_expression(metric, metrics) == ""

    def test_ratio_whose_input_is_itself_not_mirrored(self):
        metrics = {"revenue": simple("revenue", filter="not none"), "order_count": simple("order_count", "count_distinct", "order_id")}
        metric = ratio("x", "revenue", "order_count")
        assert admin.qlik_expression(metric, metrics) == ""

    def test_cumulative_metric_not_mirrored(self):
        metric = mf_metric("revenue_mtd", "cumulative", type_params={})
        assert admin.qlik_expression(metric, {}) == ""


class TestQlikNotMirroredReason:
    def test_filtered_simple_metric(self):
        metric = simple("completed_revenue", filter="not none")
        assert "set analysis" in admin.qlik_not_mirrored_reason(metric)

    def test_unsupported_aggregation(self):
        metric = simple("p90_latency", agg="percentile")
        assert "percentile" in admin.qlik_not_mirrored_reason(metric)

    def test_simple_not_a_plain_column(self):
        metric = simple("revenue", "sum", "coalesce(amount, 0)")
        assert "not a plain column" in admin.qlik_not_mirrored_reason(metric)

    def test_ratio_with_filtered_input(self):
        metric = ratio("x", "a", "b", num_filter="not none")
        assert "Filtered numerator or denominator" in admin.qlik_not_mirrored_reason(metric)

    def test_ratio_with_unmirrored_input(self):
        metric = ratio("x", "a", "b")
        assert "not mirrored" in admin.qlik_not_mirrored_reason(metric)

    def test_cumulative_points_at_semantic_layer(self):
        metric = mf_metric("revenue_mtd", "cumulative", type_params={})
        assert admin.qlik_not_mirrored_reason(metric) == "Cumulative metric: query it through the dbt Semantic Layer"


class TestBuildQlikRows:
    def test_mirrors_simple_and_ratio_sorted_by_name(self):
        manifest = {
            "metrics": [
                ratio("average_order_value", "revenue", "order_count"),
                simple("revenue", "sum", "amount", label="Revenue"),
                simple("order_count", "count_distinct", "order_id"),
            ]
        }
        rows = admin.build_qlik_rows(manifest)
        assert [r["metric_name"] for r in rows] == ["average_order_value", "order_count", "revenue"]
        assert all(r["sync_status"] == "mirrored" for r in rows)
        revenue = next(r for r in rows if r["metric_name"] == "revenue")
        assert revenue["label"] == "Revenue"
        assert revenue["qlik_expression"] == "Sum(amount)"

    def test_unmirrored_metric_gets_a_note_and_no_expression(self):
        manifest = {"metrics": [mf_metric("revenue_mtd", "cumulative", type_params={})]}
        row = admin.build_qlik_rows(manifest)[0]
        assert row["sync_status"] == "not_mirrored"
        assert row["qlik_expression"] is None
        assert row["sync_note"] == "Cumulative metric: query it through the dbt Semantic Layer"

    def test_label_falls_back_to_metric_name(self):
        manifest = {"metrics": [simple("revenue", "sum", "amount")]}
        assert admin.build_qlik_rows(manifest)[0]["label"] == "revenue"


class TestMcpServerStatements:
    def test_builds_create_and_five_grants(self):
        statements = admin.mcp_server_statements(
            database="ANALYTICS", semantic_schema="semantic", semantic_view="jaffle_shop",
            role="metrics_reader", warehouse="transforming",
        )
        assert len(statements) == 6
        assert "create or replace mcp server ANALYTICS.semantic.jaffle_shop_mcp" in statements[0]
        assert "jaffle-shop-metrics" in statements[0]
        assert statements[1] == "grant usage on database ANALYTICS to role metrics_reader"
        assert statements[4] == "grant select on semantic view ANALYTICS.semantic.jaffle_shop to role metrics_reader"

    def test_oauth_integration_adds_a_seventh_statement(self):
        statements = admin.mcp_server_statements(
            database="ANALYTICS", semantic_schema="semantic", semantic_view="jaffle_shop",
            role="metrics_reader", warehouse="transforming", oauth_integration=True,
        )
        assert len(statements) == 7
        assert "claude_mcp_oauth" in statements[6]

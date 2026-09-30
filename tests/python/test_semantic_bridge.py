"""Unit tests for scripts/semantic_bridge.py.

Covers everything in that file except from_converter (needs the pinned git
dependency apache-ossie-dbt -- already exercised for real by the `checks` job
in .github/workflows/semantic-layer-checks.yml). Nothing here needs network
access or real Snowflake/dbt credentials.
"""
from __future__ import annotations

import json

import pytest
from conftest import mf_metric, ossie_dataset, ossie_document, ossie_field, ossie_metric, ossie_relationship

import semantic_bridge as sb


# ---------------------------------------------------------------------------
# unportable_reasons / input_metrics
# ---------------------------------------------------------------------------


def test_unportable_reasons_simple_metric_is_portable():
    assert sb.unportable_reasons(mf_metric("revenue")) == []


@pytest.mark.parametrize("metric_type", ["cumulative", "conversion"])
def test_unportable_reasons_flags_unsupported_types(metric_type):
    reasons = sb.unportable_reasons(mf_metric("m", type=metric_type))
    assert any("no Ossie equivalent" in r for r in reasons)


def test_unportable_reasons_flags_private_metric():
    reasons = sb.unportable_reasons(mf_metric("m", type_params={"is_private": True}))
    assert any("private" in r for r in reasons)


def test_unportable_reasons_flags_filter():
    reasons = sb.unportable_reasons(mf_metric("m", filter="{{ Dimension('x') }}"))
    assert any("Jinja filters" in r for r in reasons)


def test_unportable_reasons_flags_offset_on_a_ratio_input():
    metric = mf_metric("aov", type="ratio", type_params={
        "numerator": {"name": "revenue", "offset_window": "1 month"},
        "denominator": {"name": "order_count"},
    })
    reasons = sb.unportable_reasons(metric)
    assert any("offset on revenue is lost" in r for r in reasons)


def test_unportable_reasons_flags_filter_on_a_ratio_input():
    metric = mf_metric("aov", type="ratio", type_params={
        "numerator": {"name": "revenue", "filter": "{{ Dimension('x') }}"},
        "denominator": {"name": "order_count"},
    })
    reasons = sb.unportable_reasons(metric)
    assert any("filter on revenue doesn't translate" in r for r in reasons)


def test_unportable_reasons_combines_multiple_reasons():
    metric = mf_metric("m", type="cumulative", filter="x", type_params={"is_private": True})
    reasons = sb.unportable_reasons(metric)
    assert len(reasons) == 3


def test_input_metrics_empty_for_plain_simple_metric():
    assert sb.input_metrics(mf_metric("revenue")) == []


def test_input_metrics_ratio_refs_numerator_and_denominator():
    metric = mf_metric("aov", type="ratio", type_params={
        "numerator": {"name": "revenue"}, "denominator": {"name": "order_count"},
    })
    assert sorted(sb.input_metrics(metric)) == ["order_count", "revenue"]


def test_input_metrics_derived_metric_refs_metrics_list():
    metric = mf_metric("derived", type="derived", type_params={"metrics": [{"name": "a"}, {"name": "b"}]})
    assert sorted(sb.input_metrics(metric)) == ["a", "b"]


def test_input_metrics_cumulative_refs_input_metric():
    # cumulative_type_params.metric is itself a ref dict ({"name": ...}),
    # like numerator/denominator -- confirmed against a real dbt-generated
    # target/semantic_manifest.json, not guessed.
    metric = mf_metric("mtd", type="cumulative", type_params={"cumulative_type_params": {"metric": {"name": "revenue"}}})
    assert sb.input_metrics(metric) == ["revenue"]


# ---------------------------------------------------------------------------
# check_portability
# ---------------------------------------------------------------------------


def test_check_portability_all_portable_metrics_pass_through_unchanged():
    manifest = {"other_key": "kept", "metrics": [mf_metric("revenue"), mf_metric("order_count")]}
    portable, dbt_only = sb.check_portability(manifest)
    assert dbt_only == set()
    assert portable["other_key"] == "kept"
    assert {m["name"] for m in portable["metrics"]} == {"revenue", "order_count"}


def test_check_portability_excludes_dbt_only_metric_without_checking_it():
    manifest = {"metrics": [mf_metric("revenue"), mf_metric("mtd", type="cumulative", dbt_only=True)]}
    portable, dbt_only = sb.check_portability(manifest)
    assert dbt_only == {"mtd"}
    assert {m["name"] for m in portable["metrics"]} == {"revenue"}


def test_check_portability_raises_on_lossy_non_dbt_only_metric():
    manifest = {"metrics": [mf_metric("mtd", type="cumulative")]}
    with pytest.raises(sb.BridgeError, match="mtd.*no Ossie equivalent"):
        sb.check_portability(manifest)


def test_check_portability_raises_when_a_portable_metric_depends_on_a_dbt_only_one():
    manifest = {"metrics": [
        mf_metric("mtd", type="cumulative", dbt_only=True),
        mf_metric("aov", type="ratio", type_params={"numerator": {"name": "mtd"}, "denominator": {"name": "count"}}),
    ]}
    with pytest.raises(sb.BridgeError, match="depends on dbt_only metric mtd"):
        sb.check_portability(manifest)


# ---------------------------------------------------------------------------
# ossie_models
# ---------------------------------------------------------------------------


def test_ossie_models_returns_the_wrapped_list():
    document = {"semantic_model": [{"name": "a"}, {"name": "b"}]}
    assert sb.ossie_models(document) == [{"name": "a"}, {"name": "b"}]


def test_ossie_models_wraps_a_flat_document():
    document = {"name": "a", "datasets": []}
    assert sb.ossie_models(document) == [document]


def test_ossie_models_treats_an_empty_semantic_model_list_as_absent():
    # `document.get("semantic_model") or [document]` -- an explicit [] is
    # falsy, so this falls back to wrapping the whole document, same as if
    # the key were missing entirely. Documenting actual behavior, not
    # asserting it's ideal.
    document = {"semantic_model": [], "name": "a"}
    assert sb.ossie_models(document) == [document]


# ---------------------------------------------------------------------------
# from_dbt_v1
# ---------------------------------------------------------------------------


def test_from_dbt_v1_raises_when_osi_document_is_missing(tmp_path):
    with pytest.raises(sb.BridgeError, match="osi_document.json.*missing"):
        sb.from_dbt_v1(tmp_path, dbt_only=set())


def test_from_dbt_v1_drops_dbt_only_metrics(tmp_path):
    document = {"semantic_model": [{"name": "m", "metrics": [{"name": "a"}, {"name": "b"}]}]}
    (tmp_path / "osi_document.json").write_text(json.dumps(document))
    result = sb.from_dbt_v1(tmp_path, dbt_only={"b"})
    assert [m["name"] for m in result["semantic_model"][0]["metrics"]] == ["a"]


# ---------------------------------------------------------------------------
# snowflake_expression
# ---------------------------------------------------------------------------


def test_snowflake_expression_prefers_snowflake_dialect():
    element = ossie_field("x", "snowflake_expr", dialect="SNOWFLAKE")
    element["expression"]["dialects"].append({"dialect": "ANSI_SQL", "expression": "ansi_expr"})
    assert sb.snowflake_expression(element) == "snowflake_expr"


def test_snowflake_expression_falls_back_to_ansi_sql():
    element = ossie_field("x", "ansi_expr", dialect="ANSI_SQL")
    assert sb.snowflake_expression(element) == "ansi_expr"


def test_snowflake_expression_none_when_no_matching_dialect():
    element = ossie_field("x", "tableau_expr", dialect="TABLEAU")
    assert sb.snowflake_expression(element) is None


def test_snowflake_expression_none_when_no_expression_key():
    assert sb.snowflake_expression({"name": "x"}) is None


# ---------------------------------------------------------------------------
# check_snowflake_ready
# ---------------------------------------------------------------------------


def test_check_snowflake_ready_passes_a_valid_minimal_document():
    document = ossie_document(
        datasets=[ossie_dataset("orders", "DB.S.orders", primary_key=["order_id"], fields=[
            ossie_field("order_id", "order_id"),
        ])],
        metrics=[ossie_metric("revenue", "SUM(orders.amount)")],
    )
    sb.check_snowflake_ready(document)  # should not raise


def test_check_snowflake_ready_rejects_more_than_one_model():
    document = {"version": "0.1.1", "semantic_model": [
        {"name": "a", "datasets": [ossie_dataset("t", "DB.S.t")]},
        {"name": "b", "datasets": [ossie_dataset("t", "DB.S.t")]},
    ]}
    with pytest.raises(sb.BridgeError, match="2 semantic models"):
        sb.check_snowflake_ready(document)


def test_check_snowflake_ready_rejects_a_field_with_no_snowflake_or_ansi_dialect():
    document = ossie_document(datasets=[
        ossie_dataset("orders", "DB.S.orders", fields=[ossie_field("x", "x", dialect="TABLEAU")]),
    ])
    with pytest.raises(sb.BridgeError, match="orders.x.*no.*expression"):
        sb.check_snowflake_ready(document)


def test_check_snowflake_ready_rejects_a_metric_expression_without_a_table_qualified_column():
    document = ossie_document(
        datasets=[ossie_dataset("orders", "DB.S.orders")],
        metrics=[ossie_metric("revenue", "SUM(amount)")],  # no "orders." prefix
    )
    with pytest.raises(sb.BridgeError, match="revenue.*no table-qualified column"):
        sb.check_snowflake_ready(document)


# ---------------------------------------------------------------------------
# ai_context / collect_extras / merge
# ---------------------------------------------------------------------------


def test_ai_context_none_when_no_snowflake_meta():
    assert sb.ai_context({}, "x") is None
    assert sb.ai_context(None, "x") is None


def test_ai_context_returns_allowed_keys():
    meta = {"snowflake": {"synonyms": ["a"], "instructions": "b"}}
    assert sb.ai_context(meta, "x") == {"synonyms": ["a"], "instructions": "b"}


def test_ai_context_rejects_a_formula_key():
    meta = {"snowflake": {"synonyms": ["a"], "formula": "sum(x)"}}
    with pytest.raises(sb.BridgeError, match=r"x: config\.meta\.snowflake may only hold"):
        sb.ai_context(meta, "x")


def _manifest_and_semantic_manifest_with_meta():
    """A minimal (manifest.json, semantic_manifest.json) pair exercising
    both column-meta branches collect_extras reads from: node.columns[c]
    .config.meta (dbt v2 shape) and node.columns[c].meta (fallback shape)."""
    semantic_manifest = {
        "semantic_models": [{"name": "orders"}],
        "metrics": [{"name": "revenue", "config": {"meta": {"snowflake": {"synonyms": ["gross revenue"]}}}}],
    }
    manifest = {
        "semantic_models": {"semantic_model.p.orders": {"name": "orders", "depends_on": {"nodes": ["model.p.orders"]}}},
        "nodes": {
            "model.p.orders": {
                "name": "orders",
                "config": {"meta": {"snowflake": {"instructions": "returned orders included"}}},
                "columns": {
                    "region": {"name": "region", "config": {"meta": {"snowflake": {"synonyms": ["area"]}}}},
                    "status": {"name": "status", "meta": {"snowflake": {"instructions": "completed or returned"}}},
                    "amount": {"name": "amount"},
                },
            }
        },
    }
    return semantic_manifest, manifest


def test_collect_extras_reads_dataset_field_and_metric_meta():
    semantic_manifest, manifest = _manifest_and_semantic_manifest_with_meta()
    extras = sb.collect_extras(semantic_manifest, manifest)
    assert extras["datasets"]["orders"] == {"instructions": "returned orders included"}
    assert extras["fields"][("orders", "region")] == {"synonyms": ["area"]}
    assert extras["fields"][("orders", "status")] == {"instructions": "completed or returned"}
    assert ("orders", "amount") not in extras["fields"]
    assert extras["metrics"]["revenue"] == {"synonyms": ["gross revenue"]}


def test_merge_places_extras_and_reports_orphans():
    document = ossie_document(
        datasets=[ossie_dataset("orders", "DB.S.orders", fields=[ossie_field("region", "region")])],
        metrics=[ossie_metric("revenue", "SUM(orders.amount)")],
    )
    extras = {
        "datasets": {"orders": {"instructions": "note"}},
        "fields": {("orders", "region"): {"synonyms": ["area"]}, ("orders", "missing_field"): {"synonyms": ["x"]}},
        "metrics": {"revenue": {"synonyms": ["gross revenue"]}, "missing_metric": {"synonyms": ["y"]}},
    }
    orphans = sb.merge(document, extras)
    model = document["semantic_model"][0]
    assert model["datasets"][0]["ai_context"] == {"instructions": "note"}
    assert model["datasets"][0]["fields"][0]["ai_context"] == {"synonyms": ["area"]}
    assert model["metrics"][0]["ai_context"] == {"synonyms": ["gross revenue"]}
    expected = [("field", ("orders", "missing_field")), ("metric", "missing_metric")]
    assert orphans == sorted(str(k) for k in expected)


# ---------------------------------------------------------------------------
# quote_if_reserved / sql_string / synonyms_and_comment
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", ["order", "ORDER", "Order"])
def test_quote_if_reserved_quotes_reserved_words_case_insensitively(name):
    assert sb.quote_if_reserved(name) == f'"{name}"'


def test_quote_if_reserved_leaves_ordinary_names_alone():
    assert sb.quote_if_reserved("revenue") == "revenue"


def test_sql_string_escapes_embedded_quotes():
    assert sb.sql_string("O'Brien") == "'O''Brien'"


def test_sql_string_plain_text():
    assert sb.sql_string("hello") == "'hello'"


def test_synonyms_and_comment_empty_when_nothing_given():
    assert sb.synonyms_and_comment(None, None) == ""


def test_synonyms_and_comment_description_only():
    assert sb.synonyms_and_comment("A description.", None) == " COMMENT = 'A description.'"


def test_synonyms_and_comment_synonyms_only():
    result = sb.synonyms_and_comment(None, {"synonyms": ["area", "territory"]})
    assert result == " WITH SYNONYMS ('area', 'territory')"


def test_synonyms_and_comment_instructions_alone_become_the_comment():
    result = sb.synonyms_and_comment(None, {"instructions": "Values are North, South."})
    assert result == " COMMENT = 'Values are North, South.'"


def test_synonyms_and_comment_description_and_instructions_join_with_a_space():
    result = sb.synonyms_and_comment("Region.", {"instructions": "Values are North, South."})
    assert result == " COMMENT = 'Region. Values are North, South.'"


def test_synonyms_and_comment_both_clauses_together():
    result = sb.synonyms_and_comment("Region.", {"synonyms": ["area"], "instructions": "Values North/South."})
    assert result == " WITH SYNONYMS ('area') COMMENT = 'Region. Values North/South.'"


# ---------------------------------------------------------------------------
# native_base_metric_table
# ---------------------------------------------------------------------------


def test_native_base_metric_table_finds_the_single_matching_table():
    assert sb.native_base_metric_table("SUM(orders.amount)", {"orders", "customers"}) == "orders"


def test_native_base_metric_table_raises_when_no_table_matches():
    with pytest.raises(sb.BridgeError, match="can't tell which single table"):
        sb.native_base_metric_table("SUM(amount)", {"orders"})


def test_native_base_metric_table_raises_when_more_than_one_table_matches():
    expr = "SUM(orders.amount) - SUM(returns.amount)"
    with pytest.raises(sb.BridgeError, match="can't tell which single table"):
        sb.native_base_metric_table(expr, {"orders", "returns"})


def test_native_base_metric_table_regex_boundary_does_not_false_match_a_prefix():
    # A table literally named "order" must not match inside "orders.amount".
    with pytest.raises(sb.BridgeError):
        sb.native_base_metric_table("SUM(orders.amount)", {"order"})


# ---------------------------------------------------------------------------
# native_semantic_view_sql
# ---------------------------------------------------------------------------


def test_native_semantic_view_sql_builds_all_clauses():
    document = ossie_document(
        datasets=[
            ossie_dataset(
                "customers", "DB.S.customers", primary_key=["customer_id"], description="One row per customer.",
                fields=[
                    ossie_field("customer", "customer_id"),  # no "dimension" key -> PK/FK, skipped from DIMENSIONS
                    ossie_field("region", "region", dimension={"is_time": False}, ai_context={"synonyms": ["area"]}),
                ],
            ),
            ossie_dataset("orders", "DB.S.orders", primary_key=["order_id"]),
        ],
        relationships=[ossie_relationship("orders__customers", "orders", "customers", ["customer_id"], ["customer_id"])],
        metrics=[ossie_metric("revenue", "SUM(orders.amount)", description="Gross revenue.")],
    )
    sql = sb.native_semantic_view_sql(document, "ANALYTICS.SEMANTIC", "jaffle_shop")

    assert "CREATE OR ALTER SEMANTIC VIEW ANALYTICS.SEMANTIC.jaffle_shop" in sql
    assert "customers AS DB.S.customers PRIMARY KEY (customer_id) COMMENT = 'One row per customer.'" in sql
    assert "orders AS DB.S.orders PRIMARY KEY (order_id)" in sql
    assert "orders__customers AS orders (customer_id) REFERENCES customers (customer_id)" in sql
    assert "customers.region AS region WITH SYNONYMS ('area')" in sql
    assert "customers.customer" not in sql  # the PK/FK field never appears in DIMENSIONS
    assert "orders.revenue AS SUM(orders.amount) COMMENT = 'Gross revenue.'" in sql


def test_native_semantic_view_sql_omits_empty_clauses():
    document = ossie_document(datasets=[ossie_dataset("orders", "DB.S.orders", primary_key=["order_id"])])
    sql = sb.native_semantic_view_sql(document, "DB.S", "v")
    assert "TABLES (" in sql
    assert "RELATIONSHIPS (" not in sql
    assert "DIMENSIONS (" not in sql
    assert "METRICS (" not in sql


def test_native_semantic_view_sql_quotes_reserved_metric_names():
    document = ossie_document(
        datasets=[ossie_dataset("orders", "DB.S.orders")],
        metrics=[ossie_metric("order", "COUNT(orders.order_id)")],
    )
    sql = sb.native_semantic_view_sql(document, "DB.S", "v")
    assert '"order"' in sql


# ---------------------------------------------------------------------------
# project_defaults
# ---------------------------------------------------------------------------


def test_project_defaults_none_when_no_dbt_project_yml(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert sb.project_defaults({"nodes": {}}) == (None, None)


def test_project_defaults_reads_vars_and_resolved_database(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "dbt_project.yml").write_text(
        "vars:\n  semantic_schema: semantic\n  semantic_view: jaffle_shop\n"
    )
    manifest = {"nodes": {"model.p.orders": {"database": "ANALYTICS"}}}
    assert sb.project_defaults(manifest) == ("ANALYTICS.semantic", "jaffle_shop")


def test_project_defaults_schema_is_none_without_a_resolved_database(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "dbt_project.yml").write_text("vars:\n  semantic_schema: semantic\n  semantic_view: jaffle_shop\n")
    assert sb.project_defaults({"nodes": {}}) == (None, "jaffle_shop")


def test_project_defaults_schema_is_none_without_semantic_schema_var(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "dbt_project.yml").write_text("vars:\n  semantic_view: jaffle_shop\n")
    manifest = {"nodes": {"model.p.orders": {"database": "ANALYTICS"}}}
    assert sb.project_defaults(manifest) == (None, "jaffle_shop")


# ---------------------------------------------------------------------------
# main() -- integration slice via --source dbt-v1, deliberately avoiding
# from_converter (needs apache-ossie-dbt, already covered by CI's real run)
# ---------------------------------------------------------------------------


def _write_manifests(target_dir, metrics):
    (target_dir / "semantic_manifest.json").write_text(json.dumps({
        "semantic_models": [{"name": "orders", "depends_on": {"nodes": ["model.p.orders"]}}],
        "metrics": metrics,
    }))
    (target_dir / "manifest.json").write_text(json.dumps({
        "semantic_models": {"semantic_model.p.orders": {"name": "orders", "depends_on": {"nodes": ["model.p.orders"]}}},
        "nodes": {"model.p.orders": {"name": "orders", "database": "ANALYTICS", "config": {}, "columns": {}}},
    }))


def test_main_check_only_needs_no_third_party_dependencies(tmp_path, monkeypatch, capsys):
    _write_manifests(tmp_path, [{"name": "revenue", "type": "simple", "type_params": {}}])
    monkeypatch.setattr("sys.argv", ["semantic_bridge.py", "--check", "--target-dir", str(tmp_path)])
    sb.main()
    assert "OK: 1 metrics portable to Snowflake" in capsys.readouterr().out


def test_main_full_run_writes_ossie_yaml_and_deploy_sql(tmp_path, monkeypatch):
    _write_manifests(tmp_path, [{"name": "revenue", "type": "simple", "type_params": {}}])
    document = ossie_document(
        datasets=[ossie_dataset("orders", "ANALYTICS.SEMANTIC.orders", primary_key=["order_id"])],
        metrics=[ossie_metric("revenue", "SUM(orders.amount)")],
    )
    (tmp_path / "osi_document.json").write_text(json.dumps(document))

    monkeypatch.setattr("sys.argv", [
        "semantic_bridge.py", "--source", "dbt-v1", "--schema", "ANALYTICS.SEMANTIC",
        "--model-name", "jaffle_shop", "--target-dir", str(tmp_path),
    ])
    sb.main()

    assert (tmp_path / "ossie" / "jaffle_shop.yaml").exists()
    deploy_sql = (tmp_path / "deploy_semantic_views.sql").read_text()
    assert "CREATE SCHEMA IF NOT EXISTS ANALYTICS.SEMANTIC" in deploy_sql
    assert "CREATE OR ALTER SEMANTIC VIEW ANALYTICS.SEMANTIC.jaffle_shop" in deploy_sql
    assert "orders.revenue AS SUM(orders.amount)" in deploy_sql


def test_main_exits_nonzero_on_a_lossy_non_dbt_only_metric(tmp_path, monkeypatch):
    _write_manifests(tmp_path, [{"name": "mtd", "type": "cumulative", "type_params": {}}])
    monkeypatch.setattr("sys.argv", ["semantic_bridge.py", "--check", "--target-dir", str(tmp_path)])
    with pytest.raises(SystemExit):
        sb.main()

# /// script
# requires-python = ">=3.11"
# dependencies = [
#   "apache-ossie-dbt @ git+https://github.com/apache/ossie.git@938a439f0170abfe702952f6871f2221690becb5#subdirectory=converters/dbt",
#   "jsonschema",
#   "pyyaml",
# ]
# ///
"""
MetricFlow YAML -> Apache Ossie -> Snowflake semantic view.

  1. Check:   every metric either translates cleanly or is marked dbt_only
  2. Convert: get an Ossie document from one of two sources (below)
  3. Check:   the document is one Snowflake can load without dropping anything
  4. Merge:   Cortex extras from config.meta.snowflake -> Ossie ai_context
  5. Write:   target/ossie/<name>.yaml, the full, spec-correct Ossie
              document -- portable to any Ossie-reading tool, and what
              --check validates -- and target/deploy_semantic_views.sql, a
              native `CREATE OR ALTER SEMANTIC VIEW` statement built by
              walking that same document. Native syntax, not Snowflake's
              SYSTEM$CREATE_SEMANTIC_VIEW_FROM_OSSIE_YAML importer, because
              that procedure can't hold metrics at all: Ossie has no
              table-scoped metric concept, so it always emits them
              unqualified, and Snowflake treats every unqualified metric as
              a view-scoped 'derived metric' that can only reference other
              metrics by name, never hold a raw aggregate -- confirmed
              against a real deploy, down to a single standalone SUM(). See
              native_semantic_view_sql

Sources, because dbt v2 doesn't write Ossie yet (it's on the roadmap, no date):

  --source converter  (default) the Apache Ossie converter on semantic_manifest.json,
                      after a dbt v2 parse. It writes Ossie 0.2.0.dev0, which the
                      bridge rewraps as 0.1.1 for Snowflake
  --source dbt-v1     target/osi_document.json, written natively by a dbt v1.12
                      parse. Already Ossie 0.1.1

Run after parsing against Snowflake, so dataset sources point at real tables:

  dbt parse --target snowflake
  uv run scripts/semantic_bridge.py --schema ANALYTICS.SEMANTIC
  snow sql -f target/deploy_semantic_views.sql

Exits non-zero on anything lossy, so CI can use it as the check. `--check` runs
the check alone, with no dependencies beyond the standard library.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

# What config.meta.snowflake may hold. Anything else, a formula in particular,
# fails the build: metric logic lives in the MetricFlow block and nowhere else.
AI_CONTEXT_KEYS = {"synonyms", "instructions", "examples"}

# The Ossie importer rejects any other version at deploy time, and silently
# leaves out any field or metric without an expression in one of these dialects.
# The schema is the official one from apache/ossie at tag osi-0.1.1-rc1.
SNOWFLAKE_OSSIE_VERSION = "0.1.1"
SNOWFLAKE_DIALECTS = {"SNOWFLAKE", "ANSI_SQL"}
SCHEMA_PATH = Path(__file__).with_name("ossie-0.1.1-schema.json")


class BridgeError(Exception):
    pass


def unportable_reasons(metric: dict) -> list[str]:
    """Why a metric can't reach Snowflake intact. Some of these the converter
    drops silently (offsets, Jinja filters), so we check them ourselves."""
    params = metric["type_params"]
    reasons = []
    if metric["type"] in ("cumulative", "conversion"):
        reasons.append(f"{metric['type']} metrics have no Ossie equivalent")
    if params.get("is_private"):
        reasons.append("Ossie has no private metrics")
    if metric.get("filter"):
        reasons.append("Jinja filters don't translate; put the logic in a model column")
    for ref in [params.get("numerator"), params.get("denominator"), *(params.get("metrics") or [])]:
        if not ref:
            continue
        if ref.get("offset_window") or ref.get("offset_to_grain"):
            reasons.append(f"offset on {ref['name']} is lost in Ossie, silently")
        if ref.get("filter"):
            reasons.append(f"filter on {ref['name']} doesn't translate")
    return reasons


def input_metrics(metric: dict) -> list[str]:
    params = metric["type_params"]
    refs = [params.get("numerator"), params.get("denominator"), *(params.get("metrics") or [])]
    cumulative = (params.get("cumulative_type_params") or {}).get("metric")
    return [ref["name"] for ref in [*refs, cumulative] if ref]


def check_portability(semantic_manifest: dict) -> tuple[dict, set[str]]:
    """Drop metrics marked dbt_only; fail on any other lossy metric."""
    metrics = {m["name"]: m for m in semantic_manifest["metrics"]}
    dbt_only = {n for n, m in metrics.items() if ((m.get("config") or {}).get("meta") or {}).get("dbt_only")}
    errors = []
    for name, metric in metrics.items():
        if name in dbt_only:
            continue
        reasons = unportable_reasons(metric)
        reasons += [f"depends on dbt_only metric {dep}" for dep in input_metrics(metric) if dep in dbt_only]
        errors += [f"{name}: {reason}" for reason in reasons]
    if errors:
        raise BridgeError(
            "Metrics that would reach Snowflake broken. Fix them, or mark them "
            "config.meta.dbt_only: true so they're served by dbt only:\n  " + "\n  ".join(errors)
        )
    for name in sorted(dbt_only):
        print(f"[skip] {name}: dbt_only, served by the dbt Semantic Layer", file=sys.stderr)
    return {**semantic_manifest, "metrics": [m for n, m in metrics.items() if n not in dbt_only]}, dbt_only


def ossie_models(document: dict) -> list[dict]:
    return document.get("semantic_model") or [document]  # 0.1.x wraps models in a list


def from_converter(semantic_manifest: dict) -> dict:
    """The Apache converter writes flat 0.2.0.dev0 documents. Rewrap as 0.1.1;
    the schema check afterwards catches anything 0.1.1 can't hold."""
    import yaml
    from metricflow_semantics.model.dbt_manifest_parser import parse_manifest_from_dbt_generated_manifest
    from ossie_dbt import MSIToOssieConverter

    manifest = parse_manifest_from_dbt_generated_manifest(json.dumps(semantic_manifest))
    result = MSIToOssieConverter().convert(manifest, ossie_model_name="model")
    if result.issues:
        issues = "\n  ".join(f"{i.issue_type.value}: {i.element_name}" for i in result.issues)
        raise BridgeError(f"The Ossie converter dropped or degraded elements:\n  {issues}")
    flat = yaml.safe_load(result.output.to_ossie_yaml())
    return {"version": SNOWFLAKE_OSSIE_VERSION, "semantic_model": [{k: v for k, v in flat.items() if k != "version"}]}


def from_dbt_v1(target_dir: Path, dbt_only: set[str]) -> dict:
    """dbt v1.12 writes osi_document.json at parse time. It keeps lossy metrics
    with only an I078 warning, so drop the dbt_only ones here."""
    path = target_dir / "osi_document.json"
    if not path.exists():
        raise BridgeError(f"{path} is missing: parse with dbt v1.12+, and note dbt skips it when semantic validation fails")
    document = json.loads(path.read_text())
    for model in ossie_models(document):
        model["metrics"] = [m for m in model.get("metrics", []) if m["name"] not in dbt_only]
    return document


def snowflake_expression(element: dict) -> str | None:
    dialects = {d.get("dialect"): d.get("expression") for d in (element.get("expression") or {}).get("dialects", [])}
    return dialects.get("SNOWFLAKE") or dialects.get("ANSI_SQL")


def check_snowflake_ready(document: dict) -> None:
    """Fail in CI on what Snowflake would reject at deploy or drop silently."""
    import jsonschema

    errors = [
        f"schema: {'/'.join(map(str, e.absolute_path)) or 'document'}: {e.message}"
        for e in jsonschema.Draft202012Validator(json.loads(SCHEMA_PATH.read_text())).iter_errors(document)
    ]
    models = ossie_models(document)
    if len(models) != 1:
        errors.append(f"{len(models)} semantic models, the procedure takes one per call")
    for model in models:
        tables = {d["name"] for d in model.get("datasets", [])}
        qualified = re.compile(r"\b(" + "|".join(map(re.escape, tables)) + r")\.\w") if tables else None
        elements = [(f"{d['name']}.{f['name']}", f) for d in model.get("datasets", []) for f in d.get("fields", [])]
        for name, element in elements + [(m["name"], m) for m in model.get("metrics", [])]:
            if not snowflake_expression(element):
                errors.append(f"{name}: no {sorted(SNOWFLAKE_DIALECTS)} expression, Snowflake would drop it")
        # Snowflake metric expressions must qualify columns with the logical table.
        # The converter qualifies a plain column but passes other SQL through as is.
        for metric in model.get("metrics", []):
            expression = snowflake_expression(metric) or ""
            if qualified and not qualified.search(expression):
                errors.append(f"{metric['name']}: no table-qualified column in {expression!r}; use a plain model column in expr")
    if errors:
        raise BridgeError("Snowflake can't load this document intact:\n  " + "\n  ".join(errors))


def ai_context(meta: dict, where: str) -> dict | None:
    extras = (meta or {}).get("snowflake")
    if not extras:
        return None
    unknown = set(extras) - AI_CONTEXT_KEYS
    if unknown:
        raise BridgeError(f"{where}: config.meta.snowflake may only hold {sorted(AI_CONTEXT_KEYS)}, found {sorted(unknown)}")
    return extras


def collect_extras(semantic_manifest: dict, manifest: dict) -> dict:
    """Cortex extras keyed by (dataset, field) and by metric name. Model and
    column meta only exists in manifest.json; metric meta in both."""
    model_of = {sm["name"]: sm["depends_on"]["nodes"][0] for sm in manifest["semantic_models"].values()}
    datasets, fields, metrics = {}, {}, {}
    for sm in semantic_manifest["semantic_models"]:
        node = manifest["nodes"][model_of[sm["name"]]]
        if extras := ai_context(node["config"].get("meta"), node["name"]):
            datasets[sm["name"]] = extras
        for column in node["columns"].values():
            if extras := ai_context((column.get("config") or {}).get("meta") or column.get("meta"), f"{node['name']}.{column['name']}"):
                fields[(sm["name"], column["name"])] = extras
    for metric in semantic_manifest["metrics"]:
        if extras := ai_context((metric.get("config") or {}).get("meta"), metric["name"]):
            metrics[metric["name"]] = extras
    return {"datasets": datasets, "fields": fields, "metrics": metrics}


# Reserved words that collide with a semantic view's generated SQL when used
# unquoted as a field/dataset/metric name -- confirmed for ORDER against a
# real deploy ("order" is the orders dataset's primary-key field, from the
# dbt semantic model's entity name). Not exhaustive: add to this set if
# Snowflake rejects another one the same way ("Invalid identifier '...'.
# Consider adding double quotes").
SQL_RESERVED_WORDS = {
    "ORDER", "GROUP", "SELECT", "WHERE", "FROM", "TABLE", "VIEW", "USER",
    "ROLE", "DATE", "TIME", "TIMESTAMP", "COLUMN", "SCHEMA", "DATABASE",
    "INDEX", "KEY", "PRIMARY", "FOREIGN", "UNIQUE", "CHECK", "DEFAULT",
    "NULL", "TRUE", "FALSE", "AND", "OR", "NOT", "IN", "IS", "LIKE",
    "BETWEEN", "EXISTS", "UNION", "ALL", "DISTINCT", "HAVING", "LIMIT",
    "OFFSET", "JOIN", "INNER", "OUTER", "LEFT", "RIGHT", "ON", "AS",
    "CASE", "WHEN", "THEN", "ELSE", "END", "CAST", "INTO", "VALUES",
    "INSERT", "UPDATE", "DELETE", "CREATE", "DROP", "ALTER", "GRANT",
    "REVOKE", "WITH", "RECURSIVE",
}


def quote_if_reserved(name: str) -> str:
    return f'"{name}"' if name.upper() in SQL_RESERVED_WORDS else name


def native_base_metric_table(expression: str, tables: set[str]) -> str:
    """Which dataset/table a metric's expression belongs to, found by its
    table-qualified column reference (e.g. 'orders.amount' -> orders).
    check_snowflake_ready already guarantees every metric expression
    qualifies a column this way. A metric spanning more than one table (a
    cross-table ratio, say) has no single answer -- raise rather than guess
    which table should own it."""
    matches = [t for t in tables if re.search(rf"\b{re.escape(t)}\.\w", expression)]
    if len(matches) != 1:
        raise BridgeError(f"can't tell which single table {expression!r} belongs to (matched {matches})")
    return matches[0]


def sql_string(text: str) -> str:
    return "'" + text.replace("'", "''") + "'"


def synonyms_and_comment(description: str | None, ai_context: dict | None) -> str:
    """WITH SYNONYMS (...) and/or COMMENT = '...' for a table/dimension/
    metric declaration, from Ossie's description + ai_context (synonyms/
    instructions). Native syntax takes these as plain SQL, so there's none
    of the object-vs-string ambiguity the Ossie importer has."""
    parts = []
    if ai_context and ai_context.get("synonyms"):
        quoted = ", ".join(sql_string(s) for s in ai_context["synonyms"])
        parts.append(f"WITH SYNONYMS ({quoted})")
    comment_bits = [b for b in [description, (ai_context or {}).get("instructions")] if b]
    if comment_bits:
        parts.append(f"COMMENT = {sql_string(' '.join(comment_bits))}")
    return (" " + " ".join(parts)) if parts else ""


def native_semantic_view_sql(document: dict, schema: str, model_name: str) -> str:
    """CREATE OR ALTER SEMANTIC VIEW, Snowflake's native syntax -- the
    replacement for SYSTEM$CREATE_SEMANTIC_VIEW_FROM_OSSIE_YAML, which can't
    hold metrics at all (see the module docstring). Built by walking the
    same Ossie document that target/ossie/<name>.yaml comes from, not a
    second parallel representation of the semantic model.

    The fact that makes this simple: confirmed against a real deploy, a
    metric only needs a table-alias prefix (table.metric_name) to be
    accepted with its real expression intact -- even a ratio combining two
    aggregates from the same table. The 'a derived metric can only
    reference other metrics by name' restriction hit earlier only applies
    to *unqualified* names, which is all Ossie's importer can ever produce
    (it has no table-scoped metric concept at all). So there's no need to
    detect ratios and rewrite them as metric-name references -- every
    metric here just gets qualified with its table and keeps its real
    expression, simple or not."""
    model = ossie_models(document)[0]
    tables = {d["name"] for d in model.get("datasets", [])}

    table_lines = []
    for dataset in model.get("datasets", []):
        alias = quote_if_reserved(dataset["name"])
        pk = ", ".join(quote_if_reserved(c) for c in dataset.get("primary_key", []))
        extra = synonyms_and_comment(dataset.get("description"), dataset.get("ai_context"))
        table_lines.append(f"  {alias} AS {dataset['source']} PRIMARY KEY ({pk}){extra}")

    relationship_lines = []
    for rel in model.get("relationships", []):
        from_alias, to_alias = quote_if_reserved(rel["from"]), quote_if_reserved(rel["to"])
        from_cols = ", ".join(quote_if_reserved(c) for c in rel["from_columns"])
        to_cols = ", ".join(quote_if_reserved(c) for c in rel["to_columns"])
        relationship_lines.append(
            f"  {quote_if_reserved(rel['name'])} AS {from_alias} ({from_cols}) REFERENCES {to_alias} ({to_cols})"
        )

    dimension_lines = []
    for dataset in model.get("datasets", []):
        alias = quote_if_reserved(dataset["name"])
        for field in dataset.get("fields", []):
            if "dimension" not in field:
                continue  # plain PK/FK reference field, not something to group by
            expr = snowflake_expression(field)
            extra = synonyms_and_comment(field.get("description"), field.get("ai_context"))
            dimension_lines.append(f"  {alias}.{quote_if_reserved(field['name'])} AS {expr}{extra}")

    metric_lines = []
    for metric in model.get("metrics", []):
        expr = snowflake_expression(metric)
        table = quote_if_reserved(native_base_metric_table(expr, tables))
        extra = synonyms_and_comment(metric.get("description"), metric.get("ai_context"))
        metric_lines.append(f"  {table}.{quote_if_reserved(metric['name'])} AS {expr}{extra}")

    clauses = [f"CREATE OR ALTER SEMANTIC VIEW {schema}.{quote_if_reserved(model_name)}"]
    clauses.append("TABLES (\n" + ",\n".join(table_lines) + "\n)")
    if relationship_lines:
        clauses.append("RELATIONSHIPS (\n" + ",\n".join(relationship_lines) + "\n)")
    if dimension_lines:
        clauses.append("DIMENSIONS (\n" + ",\n".join(dimension_lines) + "\n)")
    if metric_lines:
        clauses.append("METRICS (\n" + ",\n".join(metric_lines) + "\n)")
    return "\n".join(clauses) + ";\n"


def merge(document: dict, extras: dict) -> list[str]:
    """Write extras into ai_context. Returns anything that found no home."""
    placed = set()
    for model in ossie_models(document):
        for dataset in model.get("datasets", []):
            if ctx := extras["datasets"].get(dataset["name"]):
                dataset["ai_context"] = ctx
                placed.add(("dataset", dataset["name"]))
            for field in dataset.get("fields", []):
                if ctx := extras["fields"].get((dataset["name"], field["name"])):
                    field["ai_context"] = ctx
                    placed.add(("field", (dataset["name"], field["name"])))
        for metric in model.get("metrics", []):
            if ctx := extras["metrics"].get(metric["name"]):
                metric["ai_context"] = ctx
                placed.add(("metric", metric["name"]))
    wanted = {("dataset", k) for k in extras["datasets"]} | {("field", k) for k in extras["fields"]} | {("metric", k) for k in extras["metrics"]}
    return sorted(str(k) for k in wanted - placed)


def project_defaults(manifest: dict) -> tuple[str | None, str | None]:
    """Default --schema/--model-name from dbt_project.yml's semantic_schema/
    semantic_view vars, combined with the database dbt actually resolved
    (any node's database field in manifest.json) -- so the bridge and
    deploy_mcp_server agree on where the semantic view lives without a human
    typing the same address twice in --schema and in dbt_project.yml. Either
    half missing (no vars block, or a manifest with no nodes) means no
    default; an explicit --schema/--model-name still works either way."""
    import yaml

    project_path = Path("dbt_project.yml")
    if not project_path.exists():
        return None, None
    project_vars = (yaml.safe_load(project_path.read_text()) or {}).get("vars") or {}
    semantic_schema, semantic_view = project_vars.get("semantic_schema"), project_vars.get("semantic_view")
    database = next((n["database"] for n in (manifest.get("nodes") or {}).values() if n.get("database")), None)
    schema = f"{database}.{semantic_schema}" if database and semantic_schema else None
    return schema, semantic_view


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--schema", help="Where the semantic view goes, as DATABASE.SCHEMA (default: dbt_project.yml's semantic_schema var + the resolved database; created if missing)")
    parser.add_argument("--model-name", help="Name of the semantic view (default: dbt_project.yml's semantic_view var)")
    parser.add_argument("--source", choices=["converter", "dbt-v1"], default="converter")
    parser.add_argument("--target-dir", type=Path, default=Path("target"))
    parser.add_argument("--check", action="store_true", help="Run the portability check only")
    args = parser.parse_args()

    try:
        semantic_manifest = json.loads((args.target_dir / "semantic_manifest.json").read_text())
        portable, dbt_only = check_portability(semantic_manifest)
        manifest = json.loads((args.target_dir / "manifest.json").read_text())
        extras = collect_extras(portable, manifest)
        if args.check:
            print(f"OK: {len(portable['metrics'])} metrics portable to Snowflake")
            return

        default_schema, default_model_name = project_defaults(manifest)
        schema = args.schema or default_schema
        model_name = args.model_name or default_model_name or "jaffle_shop"
        if not schema or not re.fullmatch(r"[A-Za-z_][\w$]*\.[A-Za-z_][\w$]*", schema):
            raise BridgeError(
                "--schema DATABASE.SCHEMA is required unless --check (couldn't default it from "
                "dbt_project.yml's semantic_schema var + the resolved database)"
            )

        import yaml

        document = from_converter(portable) if args.source == "converter" else from_dbt_v1(args.target_dir, dbt_only)
        for model in ossie_models(document):
            model["name"] = model_name
        check_snowflake_ready(document)
        if orphans := merge(document, extras):
            raise BridgeError("Cortex extras with no matching Ossie element:\n  " + "\n  ".join(orphans))

        body = yaml.safe_dump(document, sort_keys=False, allow_unicode=True)
        if "$$" in body:
            raise BridgeError("The Ossie document contains $$ and can't be dollar-quoted")
        out = args.target_dir / "ossie" / f"{model_name}.yaml"
        out.parent.mkdir(exist_ok=True)
        out.write_text(body)

        sql = f"CREATE SCHEMA IF NOT EXISTS {schema};\n\n" + native_semantic_view_sql(document, schema, model_name)
        deploy = args.target_dir / "deploy_semantic_views.sql"
        deploy.write_text(sql)
        print(f"{out}\n{deploy}")
    except BridgeError as error:
        sys.exit(f"[error] {error}")


if __name__ == "__main__":
    main()

{#-
  The table Qlik reads to keep its variables in line with dbt: one row per
  metric, generated from the semantic layer YAML at build time. Replaces the
  hand-written macro. Definitions only, never values.
-#}

{% if execute %}
{% for metric in graph.metrics.values() | sort(attribute='name') %}
{%- set qlik = qlik_expression(metric) | trim %}
select
    {{ sql_string(metric.name) }} as metric_name,
    {{ sql_string(metric.label) }} as label,
    {{ sql_string(metric.description) }} as description,
    {{ sql_string(metric.metric_type) }} as metric_type,
    {{ sql_string(qlik) }} as qlik_expression,
    {{ sql_string('mirrored' if qlik else 'not_mirrored') }} as sync_status,
    {{ sql_string(none if qlik else qlik_not_mirrored_reason(metric) | trim) }} as sync_note,
    {{ dbt.current_timestamp() }} as generated_at
{% if not loop.last %}union all{% endif %}
{% endfor %}
{% else %}
select 1 as metric_name
{% endif %}

{#-
  Translate a MetricFlow metric into a Qlik expression, or return '' when it
  can't be mirrored safely. Only two shapes are mirrored automatically:

    simple, unfiltered, on a bare column  ->  Sum(amount), Count(DISTINCT order_id)
    ratio of two mirrored metrics         ->  ($(m_revenue)) / ($(m_order_count))

  Everything else stays "defined in dbt, not mirrored" so nobody assumes coverage.
-#}

{% macro qlik_expression(metric) %}
    {%- set aggs = {
        'sum': 'Sum({})', 'count': 'Count({})', 'count_distinct': 'Count(DISTINCT {})',
        'average': 'Avg({})', 'min': 'Min({})', 'max': 'Max({})'
    } -%}
    {%- set params = metric.type_params -%}

    {%- if metric.metric_type == 'simple' and not metric.filter -%}
        {%- set agg = params.metric_aggregation_params.agg -%}
        {%- set expr = params.expr -%}
        {%- if expr and agg in aggs and ' ' not in expr and '(' not in expr -%}
            {{ aggs[agg].replace('{}', expr) }}
        {%- endif -%}

    {%- elif metric.metric_type == 'ratio' -%}
        {%- set num = graph.metrics['metric.' ~ project_name ~ '.' ~ params.numerator.name] -%}
        {%- set den = graph.metrics['metric.' ~ project_name ~ '.' ~ params.denominator.name] -%}
        {%- if not params.numerator.filter and not params.denominator.filter
              and qlik_expression(num) and qlik_expression(den) -%}
            ($(m_{{ num.name }})) / ($(m_{{ den.name }}))
        {%- endif -%}
    {%- endif -%}
{% endmacro %}


{% macro qlik_not_mirrored_reason(metric) %}
    {%- set aggs = ['sum', 'count', 'count_distinct', 'average', 'min', 'max'] -%}
    {%- if metric.metric_type == 'simple' and metric.filter -%}
        Filtered metric: needs set analysis, build it by hand in Qlik
    {%- elif metric.metric_type == 'simple' and metric.type_params.metric_aggregation_params.agg not in aggs -%}
        {{ metric.type_params.metric_aggregation_params.agg }} has no Qlik equivalent here: build it by hand in Qlik
    {%- elif metric.metric_type == 'simple' -%}
        Expression is not a plain column: build it by hand in Qlik
    {%- elif metric.metric_type == 'ratio'
          and (metric.type_params.numerator.filter or metric.type_params.denominator.filter) -%}
        Filtered numerator or denominator: build it by hand in Qlik
    {%- elif metric.metric_type == 'ratio' -%}
        Numerator or denominator is not mirrored
    {%- else -%}
        {{ metric.metric_type | capitalize }} metric: query it through the dbt Semantic Layer
    {%- endif -%}
{% endmacro %}


{% macro sql_string(value) -%}
    {%- if value -%}'{{ value | replace("'", "''") }}'{%- else -%}null{%- endif -%}
{%- endmacro %}

{#
  Required by MetricFlow for any cumulative or offset metric (see revenue_mtd
  in models/metrics/_metrics.yml). Extend end_date forward before it's reached.
#}
{% set start_date = "cast('2020-01-01' as date)" %}
{% set end_date = "cast('2035-01-01' as date)" %}

select cast(date_day as date) as date_day
from ({{ dbt.date_spine('day', start_date, end_date) }}) as spine

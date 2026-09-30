select cast(date_day as date) as date_day
from ({{ dbt.date_spine('day', "cast('2020-01-01' as date)", "cast('2035-01-01' as date)") }}) as spine

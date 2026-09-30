select
    order_id,
    customer_id,
    cast(ordered_at as date) as ordered_at,
    status,
    amount,
    -- Row-level logic lives here, aggregation in the metric YAML. A plain
    -- column reaches Snowflake table-qualified and Qlik as Sum(completed_amount).
    case when status = 'completed' then amount else 0 end as completed_amount
from {{ ref('raw_orders') }}

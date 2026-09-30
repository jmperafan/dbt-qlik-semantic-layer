select
    customer_id,
    region,
    cast(signed_up_at as date) as signed_up_at
from {{ ref('raw_customers') }}

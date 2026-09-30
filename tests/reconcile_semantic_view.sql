{#-
  Two engines, two answers? Compare every metric in the Snowflake semantic view
  against plain SQL over the same models, by region. Any row returned is a
  metric that drifted. The reference is written independently on purpose: it
  must not reuse the semantic layer's logic, or it can't catch a translation bug.
-#}
{#- static_analysis off: Fusion's SQL parser doesn't know SEMANTIC_VIEW(). -#}
{{ config(enabled=var('semantic_views_deployed'), tags=['reconciliation'], static_analysis='off') }}

{%- set metrics = ['revenue', 'order_count', 'completed_revenue', 'average_order_value', 'new_customers'] %}

with semantic as (
    select *
    -- Unqualified names work while they're unique in the view, so this doesn't
    -- depend on which logical table Snowflake scopes each metric to.
    from semantic_view(
        {{ target.database }}.{{ var('semantic_schema') }}.{{ var('semantic_view') }}
        metrics revenue, order_count, completed_revenue, average_order_value, new_customers
        dimensions region
    )
),

-- Left join from the fact, like MetricFlow and semantic views: an order with
-- no matching customer still counts, under a null region.
order_metrics as (
    select
        customers.region,
        sum(orders.amount) as revenue,
        count(distinct orders.order_id) as order_count,
        sum(case when orders.status = 'completed' then orders.amount else 0 end) as completed_revenue
    from {{ ref('orders') }} as orders
    left join {{ ref('customers') }} as customers on orders.customer_id = customers.customer_id
    group by 1
),

customer_metrics as (
    select region, count(distinct customer_id) as new_customers
    from {{ ref('customers') }}
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

select
    coalesce(reference.region, semantic.region) as region,
    {%- for metric in metrics %}
    reference.{{ metric }} as reference_{{ metric }},
    semantic.{{ metric }} as semantic_{{ metric }}{{ ',' if not loop.last }}
    {%- endfor %}
from reference
full outer join semantic
    on reference.region is not distinct from semantic.region
where false
{%- for metric in metrics %}
    -- A null on one side only is drift; so is any difference beyond rounding.
    or (reference.{{ metric }} is distinct from semantic.{{ metric }}
        and coalesce(abs(reference.{{ metric }} - semantic.{{ metric }}) >= 0.0001, true))
{%- endfor %}

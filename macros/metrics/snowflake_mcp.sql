{#-
  The Snowflake-managed MCP server over the semantic view, with its grants.
  Remote over HTTP, so Claude Desktop users connect it as a custom connector
  and install nothing locally. Names come from the vars in dbt_project.yml.

    dbt run-operation deploy_mcp_server --target snowflake
    dbt run-operation deploy_mcp_server --args '{dry_run: true}'    # print only

  Safe to re-run: the server is replaced so spec changes apply, and the grants
  a replace drops are applied again. The OAuth integration is account-level and
  needs CREATE INTEGRATION, so it's opt-in: --args '{oauth_integration: true}'.
-#}

{% macro deploy_mcp_server(dry_run=false, oauth_integration=false) %}
    {%- set schema = target.database ~ '.' ~ var('semantic_schema') -%}
    {%- set view = schema ~ '.' ~ var('semantic_view') -%}
    {%- set server = view ~ '_mcp' -%}
    {%- set role = var('metrics_reader_role') -%}

    {%- set statements = [
        "create or replace mcp server " ~ server ~ " from specification $$
    tools:
      - name: \"" ~ var('semantic_view') | replace('_', '-') ~ "-metrics\"
        type: \"CORTEX_ANALYST_MESSAGE\"
        identifier: \"" ~ view ~ "\"
        title: \"Governed metrics\"
        description: \"Governed metrics, generated from the dbt semantic layer. Use this for any business number.\"
  $$",
        "grant usage on database " ~ target.database ~ " to role " ~ role,
        "grant usage on schema " ~ schema ~ " to role " ~ role,
        "grant usage on mcp server " ~ server ~ " to role " ~ role,
        "grant select on semantic view " ~ view ~ " to role " ~ role,
        "grant usage on warehouse " ~ var('mcp_warehouse') ~ " to role " ~ role,
    ] -%}

    {%- if oauth_integration -%}
        {#- The redirect URI must match the one Claude shows during connector
            setup. Afterwards, read the client ID and secret once with
            system$show_oauth_client_secrets and never save them to the repo. -#}
        {%- do statements.append(
            "create security integration if not exists claude_mcp_oauth
  type = oauth oauth_client = custom enabled = true
  oauth_client_type = 'CONFIDENTIAL'
  oauth_redirect_uri = 'https://claude.ai/api/mcp/auth_callback'"
        ) -%}
    {%- endif -%}

    {%- for statement in statements -%}
        {%- if dry_run -%}
            {{ log(statement ~ ";\n", info=true) }}
        {%- else -%}
            {%- do run_query(statement) -%}
        {%- endif -%}
    {%- endfor -%}
{% endmacro %}

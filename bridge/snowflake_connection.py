# /// script
# requires-python = ">=3.11"
# dependencies = ["snowflake-connector-python"]
# ///
"""
One place for "how do I connect to Snowflake" outside of dbt, shared by
snowflake_admin.py and the reconciliation test -- both need a connection dbt
itself doesn't give them (dbt's own connection is internal to dbt-snowflake).

Reads the same env var names profiles.yml does, so the same secrets/env
already set for `dbt build --target snowflake` work here unchanged:
SNOWFLAKE_ACCOUNT, SNOWFLAKE_USER, SNOWFLAKE_PRIVATE_KEY_PATH (plus
SNOWFLAKE_PRIVATE_KEY_PASSPHRASE if the key is encrypted), SNOWFLAKE_DATABASE.
SNOWFLAKE_ROLE/SNOWFLAKE_WAREHOUSE are optional -- omitted, the user's
account defaults apply, same as an unset role/warehouse in profiles.yml
would fall through to dbt's own TRANSFORMER/TRANSFORMING defaults only
because profiles.yml hardcodes those; here there's no equivalent fallback,
so set them explicitly if the account default role/warehouse aren't right.
"""
from __future__ import annotations

import os


def connect(role: str | None = None, warehouse: str | None = None):
    import snowflake.connector

    return snowflake.connector.connect(
        account=os.environ["SNOWFLAKE_ACCOUNT"],
        user=os.environ["SNOWFLAKE_USER"],
        database=os.environ.get("SNOWFLAKE_DATABASE"),
        role=role or os.environ.get("SNOWFLAKE_ROLE"),
        warehouse=warehouse or os.environ.get("SNOWFLAKE_WAREHOUSE"),
        private_key_file=os.environ.get("SNOWFLAKE_PRIVATE_KEY_PATH"),
        private_key_file_pwd=os.environ.get("SNOWFLAKE_PRIVATE_KEY_PASSPHRASE") or None,
    )

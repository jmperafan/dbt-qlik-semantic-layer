#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = ["websockets>=12"]
# ///
"""
Push governed metric definitions to Qlik as master measures, over the
Engine API (QIX), instead of wiring each one by hand in the Qlik UI.

Input is a JSON export of the qlik_metric_definitions table -- the same
table the load script (qlik/load_metric_definitions.qvs) reads today. This
script doesn't query Snowflake itself; that stays a separate, already-built
step (dbt build), so this script has exactly one job.

  uv run qlik/sync.py --definitions target/qlik_metric_definitions.json --check
  uv run qlik/sync.py --definitions target/qlik_metric_definitions.json --dry-run
  uv run qlik/sync.py --definitions target/qlik_metric_definitions.json

--check validates the input file only, no network. --dry-run builds every
Engine API payload and prints it, no network. Neither needs Qlik credentials.
Only the final form opens a connection: it needs QLIK_TENANT_URL (a bare
host, like mytenant.us.qlikcloud.com), QLIK_API_KEY and QLIK_APP_ID.

What's confirmed against current Qlik docs (see qlik/README.md): the
WebSocket URL shape, the Bearer-token auth header, the OpenDoc handshake, and
the CreateMeasure/SetProperties property tree. What's NOT run yet, because
there's no live tenant to run it against: the actual round trip. Test this
against a real app before trusting it in CI -- start with one metric.

Unit tests: tests/python/test_qlik_sync.py covers everything except the real
network round trip (sync_to_qlik), same gap as above.
"""
from __future__ import annotations

import argparse
import asyncio
import itertools
import json
import os
import sys
from pathlib import Path

MEASURE_PREFIX = "m_"  # matches the $(m_<metric_name>) variable convention


class BridgeError(Exception):
    pass


def load_mirrored_metrics(path: Path) -> list[dict]:
    rows = json.loads(path.read_text())
    mirrored = [r for r in rows if r.get("sync_status") == "mirrored"]
    for row in mirrored:
        if not row.get("metric_name") or not row.get("qlik_expression"):
            raise BridgeError(f"a mirrored row is missing metric_name or qlik_expression: {row}")
    return mirrored


def measure_property_tree(metric: dict) -> dict:
    """The GenericMeasureProperties tree CreateMeasure and SetProperties both
    take. qGrouping 'N' means a plain expression, not an aggregation group --
    right for every shape this project mirrors (simple sums/counts, and
    ratios that reference other variables in their qDef string)."""
    name = metric["metric_name"]
    return {
        "qInfo": {"qId": f"{MEASURE_PREFIX}{name}", "qType": "measure"},
        "qMeasure": {
            "qLabel": metric.get("label") or name,
            "qDef": metric["qlik_expression"],
            "qGrouping": "N",
            "qDescription": metric.get("description") or "",
        },
        "qMetaDef": {
            "title": metric.get("label") or name,
            "description": metric.get("description") or "",
        },
    }


class EngineSession:
    """A minimal QIX JSON-RPC client: one in-flight request at a time, no
    pipelining, no reconnect. Enough to prove the request shapes are right,
    not a general-purpose Engine API client -- use enigma.js for that if
    this grows beyond pushing measures."""

    def __init__(self, ws):
        self.ws = ws
        self._ids = itertools.count(1)

    async def call(self, handle: int, method: str, params: list) -> dict:
        request_id = next(self._ids)
        await self.ws.send(json.dumps(
            {"jsonrpc": "2.0", "id": request_id, "handle": handle, "method": method, "params": params}
        ))
        while True:
            message = json.loads(await self.ws.recv())
            if message.get("id") == request_id:
                if "error" in message:
                    raise BridgeError(f"{method} failed: {message['error']}")
                return message["result"]


async def upsert_measure(session: EngineSession, doc_handle: int, metric: dict) -> str:
    """Update the measure if it exists, create it if it doesn't. GetObject
    returns a handle for an existing qId; Qlik's own docs don't document a
    clean 'does this exist' call beyond trying to fetch it, so this is the
    documented way to check."""
    tree = measure_property_tree(metric)
    qid = tree["qInfo"]["qId"]

    existing = await session.call(doc_handle, "GetObject", [qid])
    handle = existing.get("qReturn", {}).get("qHandle")

    if handle:
        await session.call(handle, "SetProperties", [tree])
        return "updated"
    created = await session.call(doc_handle, "CreateMeasure", [tree])
    if not created.get("qReturn", {}).get("qHandle"):
        raise BridgeError(f"CreateMeasure for {qid} returned no handle: {created}")
    return "created"


async def sync_to_qlik(tenant_url: str, api_key: str, app_id: str, metrics: list[dict]) -> None:
    import websockets

    url = f"wss://{tenant_url}/app/{app_id}"
    headers = {"Authorization": f"Bearer {api_key}"}
    async with websockets.connect(url, additional_headers=headers) as ws:
        session = EngineSession(ws)
        opened = await session.call(-1, "OpenDoc", [app_id])
        doc_handle = opened["qReturn"]["qHandle"]

        for metric in metrics:
            action = await upsert_measure(session, doc_handle, metric)
            print(f"{action}: {metric['metric_name']}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--definitions", required=True, type=Path, help="JSON export of qlik_metric_definitions")
    parser.add_argument("--check", action="store_true", help="Validate the input file only, no network")
    parser.add_argument("--dry-run", action="store_true", help="Print every payload, no network")
    parser.add_argument("--tenant-url", default=os.environ.get("QLIK_TENANT_URL"))
    parser.add_argument("--app-id", default=os.environ.get("QLIK_APP_ID"))
    args = parser.parse_args()

    try:
        metrics = load_mirrored_metrics(args.definitions)

        if args.check:
            print(f"OK: {len(metrics)} mirrored metrics ready to sync")
            return

        if args.dry_run:
            for metric in metrics:
                print(json.dumps(measure_property_tree(metric), indent=2))
            print(f"\n{len(metrics)} measures would be created or updated", file=sys.stderr)
            return

        api_key = os.environ.get("QLIK_API_KEY")
        if not (args.tenant_url and api_key and args.app_id):
            raise BridgeError("--tenant-url/--app-id (or QLIK_TENANT_URL/QLIK_APP_ID) and QLIK_API_KEY are required")

        asyncio.run(sync_to_qlik(args.tenant_url, api_key, args.app_id, metrics))
    except BridgeError as error:
        sys.exit(f"[error] {error}")


if __name__ == "__main__":
    main()

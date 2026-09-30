"""Unit tests for qlik/sync.py.

Covers everything except sync_to_qlik itself (the real websocket round trip
to a Qlik Cloud tenant) -- there's no live tenant to test that against, the
same honestly-documented gap as the module docstring and qlik/README.md.
"""
from __future__ import annotations

import asyncio
import json

import pytest

import sync as qlik_sync


# ---------------------------------------------------------------------------
# load_mirrored_metrics
# ---------------------------------------------------------------------------


def test_load_mirrored_metrics_returns_only_mirrored_rows(sample_qlik_definitions_path):
    rows = qlik_sync.load_mirrored_metrics(sample_qlik_definitions_path)
    assert {r["metric_name"] for r in rows} == {
        "revenue", "order_count", "completed_revenue", "average_order_value", "new_customers",
    }
    assert "revenue_mtd" not in {r["metric_name"] for r in rows}  # not_mirrored, correctly excluded


def test_load_mirrored_metrics_raises_when_a_mirrored_row_has_no_metric_name(tmp_path):
    path = tmp_path / "defs.json"
    path.write_text(json.dumps([{"metric_name": None, "qlik_expression": "Sum(x)", "sync_status": "mirrored"}]))
    with pytest.raises(qlik_sync.BridgeError, match="missing metric_name or qlik_expression"):
        qlik_sync.load_mirrored_metrics(path)


def test_load_mirrored_metrics_raises_when_a_mirrored_row_has_no_expression(tmp_path):
    path = tmp_path / "defs.json"
    path.write_text(json.dumps([{"metric_name": "x", "qlik_expression": None, "sync_status": "mirrored"}]))
    with pytest.raises(qlik_sync.BridgeError, match="missing metric_name or qlik_expression"):
        qlik_sync.load_mirrored_metrics(path)


def test_load_mirrored_metrics_does_not_validate_not_mirrored_rows(tmp_path):
    # revenue_mtd in the real sample fixture is not_mirrored with a null
    # qlik_expression -- that must NOT raise, since validation only applies
    # after the mirrored filter.
    path = tmp_path / "defs.json"
    path.write_text(json.dumps([
        {"metric_name": "revenue_mtd", "qlik_expression": None, "sync_status": "not_mirrored"},
    ]))
    assert qlik_sync.load_mirrored_metrics(path) == []


# ---------------------------------------------------------------------------
# measure_property_tree
# ---------------------------------------------------------------------------


def test_measure_property_tree_full_metric():
    metric = {"metric_name": "revenue", "label": "Revenue", "description": "Gross revenue.", "qlik_expression": "Sum(amount)"}
    tree = qlik_sync.measure_property_tree(metric)
    assert tree["qInfo"] == {"qId": "m_revenue", "qType": "measure"}
    assert tree["qMeasure"]["qDef"] == "Sum(amount)"
    assert tree["qMeasure"]["qGrouping"] == "N"
    assert tree["qMeasure"]["qLabel"] == "Revenue"
    assert tree["qMetaDef"]["title"] == "Revenue"


def test_measure_property_tree_falls_back_to_metric_name_and_empty_description():
    metric = {"metric_name": "revenue", "qlik_expression": "Sum(amount)"}
    tree = qlik_sync.measure_property_tree(metric)
    assert tree["qMeasure"]["qLabel"] == "revenue"
    assert tree["qMeasure"]["qDescription"] == ""
    assert tree["qMetaDef"]["description"] == ""


# ---------------------------------------------------------------------------
# EngineSession.call -- a tiny fake websocket, no real network
# ---------------------------------------------------------------------------


class FakeWebSocket:
    """Records what was sent and replies with a queued response, matching
    just enough of the websockets client interface (send/recv) for
    EngineSession.call to work against."""

    def __init__(self, responses: list[dict]):
        self.sent: list[dict] = []
        self._responses = list(responses)

    async def send(self, message: str) -> None:
        self.sent.append(json.loads(message))

    async def recv(self) -> str:
        return json.dumps(self._responses.pop(0))


def test_engine_session_call_returns_the_result_on_success():
    ws = FakeWebSocket([{"id": 1, "result": {"qReturn": {"qHandle": 5}}}])
    session = qlik_sync.EngineSession(ws)
    result = asyncio.run(session.call(-1, "OpenDoc", ["app-id"]))
    assert result == {"qReturn": {"qHandle": 5}}
    assert ws.sent[0]["method"] == "OpenDoc"
    assert ws.sent[0]["handle"] == -1


def test_engine_session_call_raises_on_an_error_response():
    ws = FakeWebSocket([{"id": 1, "error": {"message": "boom"}}])
    session = qlik_sync.EngineSession(ws)
    with pytest.raises(qlik_sync.BridgeError, match="boom"):
        asyncio.run(session.call(-1, "OpenDoc", ["app-id"]))


def test_engine_session_call_skips_messages_with_a_mismatched_id():
    # A message for a different in-flight request arrives first; the loop
    # must keep reading until the id it's actually waiting for shows up.
    ws = FakeWebSocket([{"id": 999, "result": "not this one"}, {"id": 1, "result": "correct"}])
    session = qlik_sync.EngineSession(ws)
    assert asyncio.run(session.call(-1, "OpenDoc", ["app-id"])) == "correct"


# ---------------------------------------------------------------------------
# upsert_measure -- a fake session recording calls, not a real connection.
# Left exactly as written: the GetObject-based existence check was flagged
# as an open question (GetObject vs the more measure-specific GetMeasure),
# not a confirmed bug, in the review that preceded this test suite.
# ---------------------------------------------------------------------------


class FakeSession:
    def __init__(self, responses: dict[str, dict]):
        self.calls: list[tuple[int, str, list]] = []
        self._responses = responses

    async def call(self, handle: int, method: str, params: list) -> dict:
        self.calls.append((handle, method, params))
        return self._responses[method]


def test_upsert_measure_updates_when_the_object_already_exists():
    session = FakeSession({
        "GetObject": {"qReturn": {"qHandle": 42}},
        "SetProperties": {},
    })
    metric = {"metric_name": "revenue", "qlik_expression": "Sum(amount)"}
    action = asyncio.run(qlik_sync.upsert_measure(session, doc_handle=1, metric=metric))
    assert action == "updated"
    assert session.calls[0] == (1, "GetObject", ["m_revenue"])
    assert session.calls[1][0] == 42 and session.calls[1][1] == "SetProperties"


def test_upsert_measure_creates_when_the_object_does_not_exist():
    session = FakeSession({
        "GetObject": {"qReturn": {}},
        "CreateMeasure": {"qReturn": {"qHandle": 7}},
    })
    metric = {"metric_name": "revenue", "qlik_expression": "Sum(amount)"}
    action = asyncio.run(qlik_sync.upsert_measure(session, doc_handle=1, metric=metric))
    assert action == "created"
    assert session.calls[1] == (1, "CreateMeasure", [qlik_sync.measure_property_tree(metric)])


def test_upsert_measure_raises_when_create_measure_returns_no_handle():
    session = FakeSession({
        "GetObject": {"qReturn": {}},
        "CreateMeasure": {"qReturn": {}},
    })
    metric = {"metric_name": "revenue", "qlik_expression": "Sum(amount)"}
    with pytest.raises(qlik_sync.BridgeError, match="returned no handle"):
        asyncio.run(qlik_sync.upsert_measure(session, doc_handle=1, metric=metric))


# ---------------------------------------------------------------------------
# main() CLI
# ---------------------------------------------------------------------------


def test_main_check_against_the_sample_fixture(sample_qlik_definitions_path, monkeypatch, capsys):
    monkeypatch.setattr("sys.argv", ["sync.py", "--definitions", str(sample_qlik_definitions_path), "--check"])
    qlik_sync.main()
    assert "OK: 5 mirrored metrics ready to sync" in capsys.readouterr().out


def test_main_dry_run_prints_one_payload_per_mirrored_metric_and_touches_no_network(sample_qlik_definitions_path, monkeypatch, capsys):
    monkeypatch.setattr("sys.argv", ["sync.py", "--definitions", str(sample_qlik_definitions_path), "--dry-run"])
    qlik_sync.main()
    captured = capsys.readouterr()
    assert captured.out.count('"qType": "measure"') == 5
    assert "5 measures would be created or updated" in captured.err


def test_main_without_check_or_dry_run_requires_credentials(sample_qlik_definitions_path, monkeypatch):
    monkeypatch.delenv("QLIK_TENANT_URL", raising=False)
    monkeypatch.delenv("QLIK_API_KEY", raising=False)
    monkeypatch.delenv("QLIK_APP_ID", raising=False)
    monkeypatch.setattr("sys.argv", ["sync.py", "--definitions", str(sample_qlik_definitions_path)])
    with pytest.raises(SystemExit, match="QLIK_TENANT_URL"):
        qlik_sync.main()

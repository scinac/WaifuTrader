"""Instruments, bars and the live WebSocket through the real app with a fake adapter."""
import asyncio
from datetime import datetime, timedelta, timezone
from decimal import Decimal as D

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from conftest import bootstrap_admin, login
from tt.adapters.base import BrokerAdapter, Candle, Capabilities, Quote
from tt.main import create_app

IID = "kraken:BTC/EUR"
NOW = datetime.now(timezone.utc).replace(second=0, microsecond=0)
HISTORY = [Candle(IID, "1m", NOW - timedelta(minutes=10 - i), D(100 + i), D(101 + i), D(99 + i),
                  D(100 + i), D(1)) for i in range(10)]


class FakeAdapter(BrokerAdapter):
    broker = "kraken"

    def __init__(self):
        self.feed: asyncio.Queue = asyncio.Queue()

    def capabilities(self):
        return Capabilities("kraken", frozenset(), "simulator", 1, "24/7")

    async def stream(self, symbols):
        while True:
            yield await self.feed.get()

    async def fetch_bars(self, symbol, interval, since):
        return [c for c in HISTORY if c.ts >= since]


@pytest.fixture
def md_client(settings):
    settings.marketdata_enabled = True
    adapter = FakeAdapter()
    with TestClient(create_app(settings, adapter_factory=lambda b: adapter)) as c:
        c.adapter = adapter
        yield c


def H(token):
    return {"Authorization": f"Bearer {token}"}


def test_requires_login(md_client):
    assert md_client.get("/api/v1/instruments/search?q=BTC").status_code == 401


def test_search_and_detail(md_client, clock):
    token = login(md_client, clock, bootstrap_admin(md_client, clock))
    res = md_client.get("/api/v1/instruments/search?q=btc", headers=H(token)).json()
    assert [r["id"] for r in res] == [IID]
    detail = md_client.get(f"/api/v1/instruments/{IID}", headers=H(token)).json()
    assert detail["symbol"] == "BTC/EUR" and detail["currency"] == "EUR"
    assert md_client.get("/api/v1/instruments/kraken:NOPE/EUR", headers=H(token)).status_code == 404


def test_bars_backfilled_and_aggregated(md_client, clock):
    token = login(md_client, clock, bootstrap_admin(md_client, clock))
    for _ in range(50):  # wait for startup backfill
        r = md_client.get(f"/api/v1/instruments/{IID}/bars?interval=1m&limit=50", headers=H(token))
        if len(r.json()["bars"]) == 10:
            break
        asyncio.run(asyncio.sleep(0.05))
    bars = r.json()["bars"]
    assert len(bars) == 10 and bars[0]["open"] == "100" and isinstance(bars[0]["open"], str)
    five = md_client.get(f"/api/v1/instruments/{IID}/bars?interval=5m", headers=H(token)).json()
    assert sum(D(b["volume"]) for b in five["bars"]) == D(10)


def test_bars_bad_interval_rejected(md_client, clock):
    token = login(md_client, clock, bootstrap_admin(md_client, clock))
    r = md_client.get(f"/api/v1/instruments/{IID}/bars?interval=2m", headers=H(token))
    assert r.status_code == 422 and r.json()["error"]["code"] == "VALIDATION_ERROR"


def test_websocket_rejects_without_login(md_client):
    with pytest.raises(WebSocketDisconnect) as e:
        with md_client.websocket_connect("/ws") as ws:
            ws.receive_json()
    assert e.value.code == 4401


def test_websocket_streams_quotes(md_client, clock):
    token = login(md_client, clock, bootstrap_admin(md_client, clock))
    with md_client.websocket_connect(f"/ws?token={token}") as ws:
        ws.send_json({"op": "subscribe", "channel": "quotes", "symbols": [IID]})
        assert ws.receive_json()["channel"] == "ack"
        q = Quote(IID, datetime.now(timezone.utc), D("61000.1"), D("61000.2"), D("61000.2"))
        md_client.portal.call(md_client.adapter.feed.put, q)
        msg = ws.receive_json()
        assert msg["channel"] == "quotes"
        assert msg["data"]["bid"] == "61000.1" and msg["data"]["instrument_id"] == IID
        ws.send_json({"op": "subscribe", "channel": "nonsense", "symbols": []})
        assert ws.receive_json()["data"]["code"] == "UNKNOWN_CHANNEL"


def test_admin_health_reports_marketdata(md_client, clock):
    token = login(md_client, clock, bootstrap_admin(md_client, clock))
    h = md_client.get("/api/v1/admin/health", headers=H(token)).json()
    assert h["marketdata"]["broker"] == "kraken" and IID in h["marketdata"]["symbols"]

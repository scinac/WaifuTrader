"""Market data service with a scripted fake adapter (no network)."""
import asyncio
from datetime import datetime, timedelta, timezone
from decimal import Decimal as D

import pytest
from sqlalchemy import select

from tt.adapters.base import (
    BrokerAdapter,
    Candle,
    Capabilities,
    ConnectionEvent,
    Quote,
    Trade,
)
from tt.db.models import Bar
from tt.db.session import Database
from tt.marketdata.hub import Hub
from tt.marketdata.service import MarketDataService

IID = "kraken:BTC/EUR"


class FakeAdapter(BrokerAdapter):
    broker = "kraken"

    def __init__(self, events, history=None):
        self.events = events
        self.history = history or []
        self.fetch_calls: list[datetime] = []

    def capabilities(self):
        return Capabilities("kraken", frozenset(), "simulator", 1, "24/7")

    async def stream(self, symbols):
        for e in self.events:
            yield e
        await asyncio.Event().wait()

    async def fetch_bars(self, symbol, interval, since):
        self.fetch_calls.append(since)
        return [c for c in self.history if c.ts >= since]


@pytest.fixture
async def db(tmp_path):
    d = Database(f"sqlite+aiosqlite:///{tmp_path}/md.db")
    await d.create_all()
    yield d
    await d.dispose()


def now_minute():
    n = datetime.now(timezone.utc)
    return n.replace(second=0, microsecond=0)


async def stored(db):
    async with db.sessionmaker() as s:
        return (await s.execute(select(Bar).order_by(Bar.ts))).scalars().all()


async def test_trades_build_and_store_candles(db):
    m = now_minute() - timedelta(minutes=3)
    events = [ConnectionEvent("kraken", "connected", m),
              Trade(IID, m + timedelta(seconds=5), D("100"), D("1"), "buy"),
              Trade(IID, m + timedelta(seconds=50), D("102"), D("1"), "buy"),
              Trade(IID, m + timedelta(seconds=65), D("101"), D("1"), "sell")]
    hub = Hub()
    sub = hub.subscribe()
    sub.add(f"candles:1m:{IID}")
    svc = MarketDataService(FakeAdapter(events), db.sessionmaker, hub, [IID])
    await svc.start()
    await asyncio.sleep(0.2)
    await svc.stop()
    bars = await stored(db)
    assert bars[0].ts == m and bars[0].high == D("102") and bars[0].volume == D("2")
    assert not sub.queue.empty()


async def test_backfill_on_start_and_after_reconnect(db):
    m = now_minute() - timedelta(minutes=10)
    history = [Candle(IID, "1m", m + timedelta(minutes=i), D(1), D(2), D(0), D(1), D(5))
               for i in range(5)]
    events = [ConnectionEvent("kraken", "connected", m),
              ConnectionEvent("kraken", "disconnected", m, "test"),
              ConnectionEvent("kraken", "connected", m)]
    adapter = FakeAdapter(events, history)
    hub = Hub()
    alerts = hub.subscribe()
    alerts.add("alerts")
    svc = MarketDataService(adapter, db.sessionmaker, hub, [IID])
    await svc.start()
    await asyncio.sleep(0.2)
    await svc.stop()
    assert len(adapter.fetch_calls) == 2  # startup + reconnect
    assert adapter.fetch_calls[1] == history[-1].ts  # resumes from the last stored bar
    assert len(await stored(db)) == 5
    topic, alert = alerts.queue.get_nowait()
    assert alert["type"] == "broker_disconnected"
    assert svc.status()["reconnects"] == 1


async def test_stale_detection_and_recovery(db):
    hub = Hub()
    alerts = hub.subscribe()
    alerts.add("alerts")
    svc = MarketDataService(FakeAdapter([]), db.sessionmaker, hub, [IID], stale_after_s=0.05)
    await svc.check_stale()
    assert svc.stale[IID] is True
    await svc.handle(Quote(IID, datetime.now(timezone.utc), D(1), D(2), D(1)))
    assert svc.stale[IID] is False
    types = [alerts.queue.get_nowait()[1]["type"] for _ in range(2)]
    assert types == ["feed_stale", "feed_recovered"]


async def test_backfill_does_not_overwrite_live_candle(db):
    m = now_minute()
    history = [Candle(IID, "1m", m, D(1), D(1), D(1), D(1), D(1))]
    svc = MarketDataService(FakeAdapter([], history), db.sessionmaker, Hub(), [IID])
    await svc.handle(Trade(IID, m + timedelta(seconds=1), D(50), D(3), "buy"))
    await svc.backfill()
    assert await stored(db) == []
    assert svc.builders[IID].current.volume == D(3)


async def test_upsert_is_idempotent(db):
    m = now_minute() - timedelta(minutes=30)
    history = [Candle(IID, "1m", m, D(1), D(2), D(0), D(1), D(5))]
    svc = MarketDataService(FakeAdapter([], history), db.sessionmaker, Hub(), [IID],
                            backfill_minutes=60)
    await svc.backfill()
    await svc.backfill()
    assert len(await stored(db)) == 1

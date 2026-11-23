"""Market data service (FSD: Market data).

- consumes the adapter stream (quotes, trades, connection events)
- builds 1m candles from trades and upserts them into `bars`
- backfills missing bars from REST on startup and after every reconnect
- marks symbols stale when no update arrives in time, and publishes alerts
- publishes quotes and the live candle to the Hub for WebSocket clients
"""
from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from tt.adapters.base import (
    BrokerAdapter,
    Candle,
    ConnectionEvent,
    Quote,
    Trade,
    split_instrument_id,
)
from tt.db.models import Bar
from tt.marketdata.bars import BarBuilder
from tt.marketdata.hub import Hub
from tt.marketdata.serialize import candle_json, quote_json
from tt.marketdata.store import upsert_bars

log = logging.getLogger(__name__)


class MarketDataService:
    def __init__(self, adapter: BrokerAdapter, sessionmaker: async_sessionmaker, hub: Hub,
                 instrument_ids: list[str], stale_after_s: float = 10,
                 candle_publish_interval_ms: int = 250, backfill_minutes: int = 720):
        self.adapter = adapter
        self.sessionmaker = sessionmaker
        self.hub = hub
        self.instrument_ids = instrument_ids
        self.symbols = [split_instrument_id(i)[1] for i in instrument_ids]
        self.stale_after = stale_after_s
        self.publish_interval = candle_publish_interval_ms / 1000
        self.backfill_minutes = backfill_minutes

        self.builders = {i: BarBuilder(i) for i in instrument_ids}
        self.last_quote: dict[str, Quote] = {}
        self.last_update: dict[str, float] = {}  # monotonic seconds
        self.stale: dict[str, bool] = {i: False for i in instrument_ids}
        self._last_candle_pub: dict[str, float] = {}
        self.connected = False
        self.last_disconnect_reason = ""
        self.reconnects = 0
        self.bars_written = 0
        self.backfilled_bars = 0
        self._tasks: list[asyncio.Task] = []

    # --- lifecycle ----------------------------------------------------------
    async def start(self) -> None:
        self._tasks = [asyncio.create_task(self._run_stream(), name="md-stream"),
                       asyncio.create_task(self._run_monitor(), name="md-monitor")]

    async def stop(self) -> None:
        for t in self._tasks:
            t.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks = []
        await self._flush_all(datetime.now(timezone.utc), force=True)

    # --- stream -------------------------------------------------------------
    async def _run_stream(self) -> None:
        await self.backfill()
        async for event in self.adapter.stream(self.symbols):
            try:
                await self.handle(event)
            except Exception:  # never let one bad event kill the feed
                log.exception("market data event failed: %r", event)

    async def handle(self, event) -> None:
        if isinstance(event, Quote):
            self._touch(event.instrument_id)
            self.last_quote[event.instrument_id] = event
            self.hub.publish(f"quotes:{event.instrument_id}", quote_json(event))
        elif isinstance(event, Trade):
            b = self.builders.get(event.instrument_id)
            if b is None:
                return
            self._touch(event.instrument_id)
            closed = b.on_trade(event)
            if closed is not None:
                await self._store([closed])
                self._publish_candle(closed, force=True)
            if b.current is not None:
                self._publish_candle(b.current)
        elif isinstance(event, ConnectionEvent):
            if event.status == "connected":
                was_down = not self.connected and self.reconnects > 0
                self.connected = True
                if was_down:
                    await self.backfill()
            else:
                if self.connected:
                    self.hub.publish("alerts", {"type": "broker_disconnected",
                                                "broker": event.broker, "reason": event.reason,
                                                "ts": event.ts.isoformat()})
                self.connected = False
                self.reconnects += 1
                self.last_disconnect_reason = event.reason

    def _touch(self, iid: str) -> None:
        self.last_update[iid] = time.monotonic()
        if self.stale.get(iid):
            self.stale[iid] = False
            self.hub.publish("alerts", {"type": "feed_recovered", "instrument_id": iid})

    def _publish_candle(self, c: Candle, force: bool = False) -> None:
        now = time.monotonic()
        if not force and now - self._last_candle_pub.get(c.instrument_id, 0) < self.publish_interval:
            return
        self._last_candle_pub[c.instrument_id] = now
        self.hub.publish(f"candles:1m:{c.instrument_id}", candle_json(c))

    async def _store(self, candles: list[Candle]) -> None:
        if not candles:
            return
        async with self.sessionmaker() as s:
            await upsert_bars(s, candles)
            await s.commit()
        self.bars_written += len(candles)

    # --- backfill -----------------------------------------------------------
    async def backfill(self) -> None:
        """Fetch completed 1m bars from REST since the last stored bar (max backfill window)."""
        now = datetime.now(timezone.utc)
        floor = now - timedelta(minutes=self.backfill_minutes)
        for iid, symbol in zip(self.instrument_ids, self.symbols):
            async with self.sessionmaker() as s:
                last = (await s.execute(
                    select(Bar.ts).where(Bar.instrument_id == iid, Bar.interval == "1m")
                    .order_by(Bar.ts.desc()).limit(1)
                )).scalar_one_or_none()
            since = max(last, floor) if last else floor
            try:
                candles = await self.adapter.fetch_bars(symbol, "1m", since)
            except Exception as e:
                log.warning("backfill failed for %s: %s", iid, e)
                self.hub.publish("alerts", {"type": "backfill_failed", "instrument_id": iid,
                                            "reason": str(e)})
                continue
            # never overwrite the candle currently being built from live trades
            cur = self.builders[iid].current
            candles = [c for c in candles if cur is None or c.ts < cur.ts]
            await self._store(candles)
            self.backfilled_bars += len(candles)

    # --- monitor ------------------------------------------------------------
    async def _run_monitor(self) -> None:
        while True:
            await asyncio.sleep(1)
            await self.check_stale()
            await self._flush_all(datetime.now(timezone.utc))

    async def check_stale(self) -> None:
        now = time.monotonic()
        for iid in self.instrument_ids:
            last = self.last_update.get(iid)
            is_stale = last is None or now - last > self.stale_after
            if is_stale and not self.stale[iid]:
                self.stale[iid] = True
                self.hub.publish("alerts", {"type": "feed_stale", "instrument_id": iid})

    async def _flush_all(self, now: datetime, force: bool = False) -> None:
        closed = []
        for b in self.builders.values():
            c = b.current if force else b.flush_if_due(now)
            if c is not None:
                closed.append(c)
        await self._store(closed)

    # --- status -------------------------------------------------------------
    def status(self) -> dict:
        now = time.monotonic()
        return {
            "enabled": True,
            "broker": self.adapter.broker,
            "connected": self.connected,
            "reconnects": self.reconnects,
            "last_disconnect_reason": self.last_disconnect_reason,
            "bars_written": self.bars_written,
            "backfilled_bars": self.backfilled_bars,
            "symbols": {
                iid: {
                    "stale": self.stale[iid],
                    "seconds_since_update": (round(now - self.last_update[iid], 1)
                                             if iid in self.last_update else None),
                    "late_trades": self.builders[iid].late_trades,
                } for iid in self.instrument_ids
            },
        }

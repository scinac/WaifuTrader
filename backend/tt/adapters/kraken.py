"""Kraken adapter: public market data via WebSocket v2, history via REST OHLC.

Trading (private endpoints, via ccxt) is added in phase 2. Numbers are parsed
straight to Decimal (json parse_float=Decimal) so prices never pass through float.
"""
from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator, Callable
from datetime import datetime, timezone
from decimal import Decimal

import httpx
import websockets

from tt.adapters.base import (
    BrokerAdapter,
    Candle,
    Capabilities,
    ConnectionEvent,
    MarketEvent,
    Quote,
    Trade,
    instrument_id,
)

log = logging.getLogger(__name__)

WS_URL = "wss://ws.kraken.com/v2"
REST_OHLC_URL = "https://api.kraken.com/0/public/OHLC"
BACKOFF_SECONDS = [1, 2, 5, 10, 30]  # FSD error handling: broker connection lost
INTERVAL_MINUTES = {"1m": 1, "5m": 5, "15m": 15, "1h": 60, "4h": 240, "1d": 1440, "1w": 10080}
BROKER = "kraken"


def _ts(value: str) -> datetime:
    # Kraken: "2023-09-25T07:49:37.708706Z"
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def _dec(value) -> Decimal | None:
    if value is None:
        return None
    return value if isinstance(value, Decimal) else Decimal(str(value))


def rest_pair(symbol: str) -> str:
    """WS v2 symbol 'BTC/EUR' -> REST pair 'XBTEUR'."""
    base, _, quote = symbol.partition("/")
    base = "XBT" if base == "BTC" else base
    return f"{base}{quote}"


def parse_ws_message(raw: str | bytes, now: Callable[[], datetime] | None = None) -> list[MarketEvent]:
    """Translate one Kraken WS v2 message into zero or more market events."""
    msg = json.loads(raw, parse_float=Decimal)
    channel = msg.get("channel")
    if channel == "ticker":
        stamp = (now or (lambda: datetime.now(timezone.utc)))()
        out: list[MarketEvent] = []
        for d in msg.get("data", []):
            out.append(Quote(
                instrument_id=instrument_id(BROKER, d["symbol"]), ts=stamp,
                bid=_dec(d.get("bid")), ask=_dec(d.get("ask")), last=_dec(d.get("last")),
                volume_24h=_dec(d.get("volume")), change_pct_24h=_dec(d.get("change_pct")),
                high_24h=_dec(d.get("high")), low_24h=_dec(d.get("low")),
            ))
        return out
    if channel == "trade":
        return [
            Trade(instrument_id=instrument_id(BROKER, d["symbol"]), ts=_ts(d["timestamp"]),
                  price=_dec(d["price"]), qty=_dec(d["qty"]), side=d["side"],
                  trade_id=str(d.get("trade_id")) if d.get("trade_id") is not None else None)
            for d in msg.get("data", [])
        ]
    if msg.get("method") == "subscribe" and msg.get("success") is False:
        log.error("Kraken subscription failed: %s", msg.get("error"))
    return []  # heartbeat, status, subscribe acks


def parse_ohlc(payload: dict, symbol: str, interval: str, since: datetime) -> list[Candle]:
    """Parse REST OHLC. Kraken's last row is the current, uncommitted bar: drop it."""
    if payload.get("error"):
        raise RuntimeError(f"Kraken OHLC error: {payload['error']}")
    result = payload["result"]
    rows = next(v for k, v in result.items() if k != "last")
    iid = instrument_id(BROKER, symbol)
    candles = []
    for row in rows[:-1]:
        t, o, h, low, c, _vwap, vol, _count = row
        ts = datetime.fromtimestamp(int(t), tz=timezone.utc)
        if ts < since:
            continue
        candles.append(Candle(iid, interval, ts, Decimal(str(o)), Decimal(str(h)),
                              Decimal(str(low)), Decimal(str(c)), Decimal(str(vol))))
    return candles


class KrakenAdapter(BrokerAdapter):
    broker = BROKER

    def __init__(self, http: httpx.AsyncClient | None = None, ws_url: str = WS_URL):
        self._http = http or httpx.AsyncClient(timeout=15)
        self._ws_url = ws_url

    def capabilities(self) -> Capabilities:
        return Capabilities(
            broker=BROKER,
            native_order_types=frozenset({"market", "limit", "stop", "stop_limit", "take_profit",
                                          "trailing_stop"}),
            paper_mode="simulator",  # Kraken has no public spot testnet
            max_requests_per_second=1.0,
            trading_hours="24/7",
            supports_margin=True,
        )

    async def stream(self, symbols: list[str]) -> AsyncIterator[MarketEvent]:
        attempt = 0
        while True:
            try:
                async with websockets.connect(self._ws_url, ping_interval=20, ping_timeout=20,
                                              max_size=2**22) as ws:
                    # Ticker on best bid/offer changes: the default (trades only) goes quiet
                    # for longer than the stale limit in a healthy market.
                    for params in ({"channel": "ticker", "event_trigger": "bbo"}, {"channel": "trade"}):
                        await ws.send(json.dumps({"method": "subscribe",
                                                  "params": {**params, "symbol": symbols}}))
                    attempt = 0
                    yield ConnectionEvent(BROKER, "connected", datetime.now(timezone.utc))
                    async for raw in ws:
                        for event in parse_ws_message(raw):
                            yield event
                    reason = "closed by server"
            except asyncio.CancelledError:
                raise
            except Exception as e:  # network errors, protocol errors
                reason = f"{type(e).__name__}: {e}"
                log.warning("Kraken stream error: %s", reason)
            yield ConnectionEvent(BROKER, "disconnected", datetime.now(timezone.utc), reason)
            await asyncio.sleep(BACKOFF_SECONDS[min(attempt, len(BACKOFF_SECONDS) - 1)])
            attempt += 1

    async def fetch_bars(self, symbol: str, interval: str, since: datetime) -> list[Candle]:
        minutes = INTERVAL_MINUTES[interval]
        resp = await self._http.get(REST_OHLC_URL, params={
            "pair": rest_pair(symbol), "interval": minutes, "since": int(since.timestamp()) - 1,
        })
        resp.raise_for_status()
        return parse_ohlc(resp.json(), symbol, interval, since)

    async def close(self) -> None:
        await self._http.aclose()

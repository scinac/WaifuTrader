"""JSON shapes for the API. Money as decimal strings, never floats (FSD API conventions)."""
from decimal import Decimal

from tt.adapters.base import Candle, Quote


def d(v: Decimal | None) -> str | None:
    return None if v is None else format(v.normalize(), "f")


def quote_json(q: Quote) -> dict:
    return {"instrument_id": q.instrument_id, "ts": q.ts.isoformat(), "bid": d(q.bid),
            "ask": d(q.ask), "last": d(q.last), "volume_24h": d(q.volume_24h),
            "change_pct_24h": d(q.change_pct_24h), "high_24h": d(q.high_24h),
            "low_24h": d(q.low_24h), "delayed": q.delayed}


def candle_json(c: Candle) -> dict:
    return {"instrument_id": c.instrument_id, "interval": c.interval, "ts": c.ts.isoformat(),
            "open": d(c.open), "high": d(c.high), "low": d(c.low), "close": d(c.close),
            "volume": d(c.volume)}

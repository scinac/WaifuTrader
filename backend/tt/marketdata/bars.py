"""Candle building from trades, aggregation of 1m bars into higher intervals, gap finding."""
from __future__ import annotations

from collections.abc import Iterable
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from tt.adapters.base import Candle, Trade

INTERVALS: dict[str, timedelta] = {
    "1m": timedelta(minutes=1), "5m": timedelta(minutes=5), "15m": timedelta(minutes=15),
    "1h": timedelta(hours=1), "4h": timedelta(hours=4), "1d": timedelta(days=1),
    "1w": timedelta(weeks=1),
}
_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)
_MONDAY = datetime(1970, 1, 5, tzinfo=timezone.utc)  # weeks start Monday 00:00 UTC


def bucket_start(ts: datetime, interval: str) -> datetime:
    step = INTERVALS[interval]
    anchor = _MONDAY if interval == "1w" else _EPOCH
    n = (ts - anchor) // step
    return anchor + n * step


class BarBuilder:
    """Builds 1m candles for one instrument from a trade stream.

    on_trade returns the candle that just CLOSED (if the trade started a new minute).
    Late trades for an already-closed minute are ignored; REST backfill corrects them.
    """

    def __init__(self, instrument_id: str, interval: str = "1m",
                 flush_grace: timedelta = timedelta(seconds=2)):
        self.instrument_id = instrument_id
        self.interval = interval
        self.flush_grace = flush_grace
        self.current: Candle | None = None
        self.last_closed_ts: datetime | None = None
        self.late_trades = 0

    def on_trade(self, t: Trade) -> Candle | None:
        start = bucket_start(t.ts, self.interval)
        cur = self.current
        if (cur is not None and start < cur.ts) or (
                self.last_closed_ts is not None and start <= self.last_closed_ts):
            # belongs to a candle that is already closed: never rewrite it with a partial one
            self.late_trades += 1
            return None
        if cur is None or start > cur.ts:
            self.current = Candle(self.instrument_id, self.interval, start, t.price, t.price,
                                  t.price, t.price, t.qty)
            if cur is not None:
                self.last_closed_ts = cur.ts
            return cur
        self.current = replace(cur, high=max(cur.high, t.price), low=min(cur.low, t.price),
                               close=t.price, volume=cur.volume + t.qty)
        return None

    def flush_if_due(self, now: datetime) -> Candle | None:
        """Close the current candle when its period (plus a grace for late trades) has ended."""
        cur = self.current
        if cur is not None and now >= cur.ts + INTERVALS[self.interval] + self.flush_grace:
            self.current = None
            self.last_closed_ts = cur.ts
            return cur
        return None


def aggregate(candles: Iterable[Candle], interval: str) -> list[Candle]:
    """Aggregate sorted 1m candles into a higher interval."""
    out: list[Candle] = []
    for c in candles:
        start = bucket_start(c.ts, interval)
        if out and out[-1].ts == start:
            last = out[-1]
            out[-1] = replace(last, high=max(last.high, c.high), low=min(last.low, c.low),
                              close=c.close, volume=last.volume + c.volume)
        else:
            out.append(Candle(c.instrument_id, interval, start, c.open, c.high, c.low, c.close,
                              c.volume))
    return out


def find_gaps(timestamps: list[datetime], interval: str = "1m",
              start: datetime | None = None, end: datetime | None = None
              ) -> list[tuple[datetime, datetime]]:
    """Return [first_missing, last_missing] ranges between sorted bar timestamps."""
    step = INTERVALS[interval]
    gaps = []
    ts = sorted(timestamps)
    if start is not None and ts and ts[0] > start:
        gaps.append((start, ts[0] - step))
    for a, b in zip(ts, ts[1:]):
        if b - a > step:
            gaps.append((a + step, b - step))
    if end is not None and ts and ts[-1] + step < end:
        gaps.append((ts[-1] + step, bucket_start(end, interval) - step))
    return [g for g in gaps if g[0] <= g[1]]


ZERO = Decimal(0)

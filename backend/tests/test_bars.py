from datetime import datetime, timedelta, timezone
from decimal import Decimal as D

from tt.adapters.base import Candle, Trade
from tt.marketdata.bars import BarBuilder, aggregate, bucket_start, find_gaps

T0 = datetime(2026, 11, 23, 20, 0, tzinfo=timezone.utc)
IID = "kraken:BTC/EUR"


def trade(sec: float, price: str, qty: str = "1") -> Trade:
    return Trade(IID, T0 + timedelta(seconds=sec), D(price), D(qty), "buy")


def test_builder_ohlcv_and_rollover():
    b = BarBuilder(IID)
    assert b.on_trade(trade(1, "100")) is None
    b.on_trade(trade(10, "105", "2"))
    b.on_trade(trade(30, "98"))
    b.on_trade(trade(59, "101"))
    closed = b.on_trade(trade(61, "102"))
    assert closed == Candle(IID, "1m", T0, D("100"), D("105"), D("98"), D("101"), D("5"))
    assert b.current.ts == T0 + timedelta(minutes=1)


def test_late_trade_never_rewrites_closed_candle():
    b = BarBuilder(IID)
    b.on_trade(trade(1, "100"))
    b.on_trade(trade(61, "101"))  # closes minute 0
    assert b.on_trade(trade(50, "999")) is None
    assert b.late_trades == 1
    # after a timed flush, a late trade for the flushed minute is also ignored
    flushed = b.flush_if_due(T0 + timedelta(minutes=2, seconds=3))
    assert flushed.ts == T0 + timedelta(minutes=1)
    assert b.on_trade(trade(90, "999")) is None
    assert b.current is None and b.late_trades == 2


def test_flush_waits_for_grace_period():
    b = BarBuilder(IID)
    b.on_trade(trade(1, "100"))
    assert b.flush_if_due(T0 + timedelta(seconds=61)) is None
    assert b.flush_if_due(T0 + timedelta(seconds=62)) is not None


def test_bucket_start_weeks_start_monday():
    assert bucket_start(datetime(2026, 11, 26, 13, tzinfo=timezone.utc), "1w") == \
        datetime(2026, 11, 23, tzinfo=timezone.utc)  # Thursday -> Monday
    assert bucket_start(T0 + timedelta(minutes=7), "5m") == T0 + timedelta(minutes=5)


def test_aggregate_5m():
    ones = [Candle(IID, "1m", T0 + timedelta(minutes=i), D(100 + i), D(110 + i), D(90 + i),
                   D(101 + i), D(1)) for i in range(7)]
    fives = aggregate(ones, "5m")
    assert len(fives) == 2
    first = fives[0]
    assert (first.open, first.high, first.low, first.close, first.volume) == \
        (D(100), D(114), D(90), D(105), D(5))
    assert fives[1].ts == T0 + timedelta(minutes=5) and fives[1].volume == D(2)


def test_find_gaps():
    ts = [T0, T0 + timedelta(minutes=1), T0 + timedelta(minutes=4), T0 + timedelta(minutes=5)]
    assert find_gaps(ts) == [(T0 + timedelta(minutes=2), T0 + timedelta(minutes=3))]
    assert find_gaps(ts, end=T0 + timedelta(minutes=8)) == [
        (T0 + timedelta(minutes=2), T0 + timedelta(minutes=3)),
        (T0 + timedelta(minutes=6), T0 + timedelta(minutes=7)),
    ]

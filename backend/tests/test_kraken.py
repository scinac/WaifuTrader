import json
from datetime import datetime, timezone
from decimal import Decimal

from tt.adapters.base import Quote, Trade
from tt.adapters.kraken import parse_ohlc, parse_ws_message, rest_pair

# Message shapes from Kraken WebSocket v2 docs (ticker, trade, heartbeat).
TICKER = json.dumps({"channel": "ticker", "type": "update", "data": [{
    "symbol": "BTC/EUR", "bid": 61234.5, "bid_qty": 0.5, "ask": 61234.6, "ask_qty": 1.2,
    "last": 61234.6, "volume": 1520.12345678, "vwap": 60900.1, "low": 60000.0, "high": 61500.0,
    "change": 900.0, "change_pct": 1.49}]})
TRADE = json.dumps({"channel": "trade", "type": "update", "data": [
    {"symbol": "BTC/EUR", "side": "buy", "price": 61234.6, "qty": 0.00012345, "ord_type": "market",
     "trade_id": 123, "timestamp": "2026-11-23T20:15:31.123456Z"},
    {"symbol": "BTC/EUR", "side": "sell", "price": 61234.5, "qty": 0.1, "ord_type": "limit",
     "trade_id": 124, "timestamp": "2026-11-23T20:15:32.000000Z"}]})


def test_ticker_parsed_to_decimal_without_float_loss():
    fixed = datetime(2026, 11, 23, tzinfo=timezone.utc)
    [q] = parse_ws_message(TICKER, now=lambda: fixed)
    assert isinstance(q, Quote)
    assert q.instrument_id == "kraken:BTC/EUR"
    assert q.bid == Decimal("61234.5") and q.ask == Decimal("61234.6")
    assert q.volume_24h == Decimal("1520.12345678")
    assert q.ts == fixed


def test_trades_parsed():
    t1, t2 = parse_ws_message(TRADE)
    assert isinstance(t1, Trade) and t1.qty == Decimal("0.00012345") and t1.side == "buy"
    assert t1.ts == datetime(2026, 11, 23, 20, 15, 31, 123456, tzinfo=timezone.utc)
    assert t2.trade_id == "124"


def test_heartbeat_and_acks_ignored():
    assert parse_ws_message('{"channel":"heartbeat"}') == []
    assert parse_ws_message('{"method":"subscribe","success":true,"result":{}}') == []


def test_rest_pair_mapping():
    assert rest_pair("BTC/EUR") == "XBTEUR"
    assert rest_pair("ETH/EUR") == "ETHEUR"


def test_ohlc_drops_uncommitted_last_bar_and_respects_since():
    base = 1_795_000_020  # aligned to a minute
    rows = [[base + 60 * i, "100.0", "101.0", "99.0", "100.5", "100.2", "1.5", 3] for i in range(4)]
    payload = {"error": [], "result": {"XXBTZEUR": rows, "last": base + 180}}
    since = datetime.fromtimestamp(base + 60, tz=timezone.utc)
    candles = parse_ohlc(payload, "BTC/EUR", "1m", since)
    assert [c.ts for c in candles] == [datetime.fromtimestamp(base + 60 * i, tz=timezone.utc)
                                       for i in (1, 2)]
    assert candles[0].close == Decimal("100.5") and candles[0].volume == Decimal("1.5")

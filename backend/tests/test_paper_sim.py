from datetime import datetime, timezone
from decimal import Decimal as D

from tt.adapters.base import OrderRequest, Quote
from tt.adapters.paper_sim import PaperSimulator

IID = "kraken:BTC/EUR"


def quote(bid, ask):
    return Quote(IID, datetime.now(timezone.utc), D(bid), D(ask), D(ask))


def order(coid, side, qty, type="market", limit=None):
    return OrderRequest(coid, IID, side, type, D(qty), limit_price=D(limit) if limit else None)


def test_market_buy_fills_at_ask_with_fee():
    sim = PaperSimulator(D("1000"))
    sim.on_quote(quote("50000", "50010"))
    u = sim.place_order(order("a", "buy", "0.01"))
    assert u.status == "filled" and u.fill_price == D("50010")
    assert u.fee == D("500.10") * D("0.004")
    assert sim.cash == D("1000") - D("500.10") - u.fee
    pos = sim.positions[IID]
    assert pos.qty == D("0.01") and pos.avg_price == (D("500.10") + u.fee) / D("0.01")


def test_market_sell_fills_at_bid():
    sim = PaperSimulator(D("1000"))
    sim.on_quote(quote("50000", "50010"))
    sim.place_order(order("a", "buy", "0.01"))
    u = sim.place_order(order("b", "sell", "0.01"))
    assert u.status == "filled" and u.fill_price == D("50000")
    assert IID not in sim.positions
    assert sim.cash < D("1000")  # spread + two fees


def test_rejections():
    sim = PaperSimulator(D("100"))
    assert sim.place_order(order("x", "buy", "1")).reason == "NO_QUOTE"
    sim.on_quote(quote("50000", "50010"))
    assert sim.place_order(order("a", "buy", "1")).reason == "INSUFFICIENT_FUNDS"
    assert sim.place_order(order("b", "sell", "1")).reason == "INSUFFICIENT_POSITION"
    assert sim.place_order(order("c", "buy", "0")).reason == "INVALID_QTY"


def test_duplicate_client_order_id_rejected():
    sim = PaperSimulator(D("1000"))
    sim.on_quote(quote("50000", "50010"))
    assert sim.place_order(order("a", "buy", "0.001")).status == "filled"
    assert sim.place_order(order("a", "buy", "0.001")).reason == "DUPLICATE_CLIENT_ORDER_ID"


def test_limit_buy_fills_when_ask_crosses():
    sim = PaperSimulator(D("1000"))
    sim.on_quote(quote("50000", "50010"))
    u = sim.place_order(order("a", "buy", "0.01", "limit", "49000"))
    assert u.status == "accepted"
    assert sim.on_quote(quote("49500", "49510")) == []
    [fill] = sim.on_quote(quote("48990", "48999"))
    assert fill.status == "filled" and fill.fill_price == D("49000")
    assert sim.open_orders == {}


def test_limit_order_reserves_cash_and_can_be_cancelled():
    sim = PaperSimulator(D("1000"))
    sim.on_quote(quote("50000", "50010"))
    sim.place_order(order("a", "buy", "0.019", "limit", "50000"))  # reserves ~954 EUR
    assert sim.place_order(order("b", "buy", "0.002")).reason == "INSUFFICIENT_FUNDS"
    assert sim.cancel_order("a").status == "cancelled"
    assert sim.place_order(order("b", "buy", "0.002")).status == "filled"


def test_account_equity_marks_positions_at_bid():
    sim = PaperSimulator(D("1000"))
    sim.on_quote(quote("50000", "50010"))
    sim.place_order(order("a", "buy", "0.01"))
    acct = sim.account()
    assert acct.equity == sim.cash + D("0.01") * D("50000")


def advanced(coid, side, qty, type, **kw):
    return OrderRequest(coid, IID, side, type, D(qty), **{k: D(v) for k, v in kw.items()})


def funded_sim():
    sim = PaperSimulator(D("10000"))
    sim.on_quote(quote("50000", "50010"))
    sim.place_order(order("entry", "buy", "0.1"))
    return sim


def test_stop_sell_fills_at_bid_when_triggered():
    sim = funded_sim()
    assert sim.place_order(advanced("s", "sell", "0.1", "stop", stop_price="49000")).status == "accepted"
    assert sim.on_quote(quote("49100", "49110")) == []
    [fill] = sim.on_quote(quote("48900", "48910"))
    assert fill.status == "filled" and fill.fill_price == D("48900")


def test_stop_limit_rests_as_limit_after_trigger():
    sim = funded_sim()
    sim.place_order(advanced("s", "sell", "0.1", "stop_limit", stop_price="49000", limit_price="49050"))
    assert sim.on_quote(quote("48900", "48910")) == []
    assert sim.open_orders["s"].triggered and "s" in sim.dirty
    [fill] = sim.on_quote(quote("49060", "49070"))
    assert fill.fill_price == D("49050")


def test_take_profit_fills_at_market():
    sim = funded_sim()
    sim.place_order(advanced("t", "sell", "0.1", "take_profit", take_profit="51000"))
    [fill] = sim.on_quote(quote("51010", "51020"))
    assert fill.fill_price == D("51010")


def test_trailing_stop_only_moves_up():
    sim = funded_sim()
    sim.place_order(advanced("t", "sell", "0.1", "trailing_stop", trail_amount="500"))
    assert sim.on_quote(quote("51000", "51010")) == []
    assert sim.open_orders["t"].anchor == D("51000")
    assert sim.on_quote(quote("50600", "50610")) == []
    assert sim.open_orders["t"].anchor == D("51000")
    [fill] = sim.on_quote(quote("50500", "50510"))
    assert fill.fill_price == D("50500")


def test_trigger_already_crossed_is_rejected():
    sim = funded_sim()
    bad = sim.place_order(advanced("s", "sell", "0.1", "stop", stop_price="50500"))
    assert bad.reason == "STOP_ALREADY_TRIGGERED"
    bad = sim.place_order(advanced("t", "sell", "0.1", "take_profit", take_profit="49000"))
    assert bad.reason == "TARGET_ALREADY_REACHED"


def test_gapped_buy_stop_without_funds_is_rejected():
    sim = PaperSimulator(D("1010"))
    sim.on_quote(quote("50000", "50010"))
    assert sim.place_order(advanced("s", "buy", "0.02", "stop", stop_price="50100")).status == "accepted"
    [update] = sim.on_quote(quote("60000", "60010"))
    assert update.status == "rejected" and update.reason == "INSUFFICIENT_FUNDS"
    assert sim.cash == D("1010") and sim.positions == {}

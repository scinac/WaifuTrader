from datetime import datetime, timezone
from decimal import Decimal as D
from uuid import uuid4

import pytest
from conftest import bootstrap_admin, code_for, login, register_user

from tt.adapters.base import Quote

IID = "kraken:BTC/EUR"
API = "/api/v1"


def push(client, bid, ask, iid=IID):
    quote = Quote(iid, datetime.now(timezone.utc), D(bid), D(ask), D(ask))
    client.portal.call(client.app.state.trading.on_quote, quote)


def auth(token):
    return {"Authorization": f"Bearer {token}"}


def send(client, token, side="buy", qty="0.01", type="market", key=None, **prices):
    headers = {**auth(token), "Idempotency-Key": key or str(uuid4())}
    return client.post(f"{API}/orders", headers=headers,
                       json={"instrument_id": IID, "side": side, "type": type, "qty": qty, **prices})


@pytest.fixture
def trader(client, clock):
    creds = bootstrap_admin(client, clock)
    token = login(client, clock, creds)
    push(client, "50000", "50010")
    return {"creds": creds, "token": token}


def summary(client, token):
    return client.get(f"{API}/accounts", headers=auth(token)).json()[0]


def test_market_buy_updates_cash_position_and_fills(client, trader):
    r = send(client, trader["token"])
    assert r.status_code == 201 and r.json()["status"] == "filled"
    account = summary(client, trader["token"])
    assert D(account["cash"]) == D("10000") - D("500.10") * D("1.004")
    [position] = client.get(f"{API}/positions", headers=auth(trader["token"])).json()
    assert D(position["qty"]) == D("0.01")
    assert len(client.get(f"{API}/fills", headers=auth(trader["token"])).json()) == 1


def test_sell_records_realized_pnl_with_fees(client, trader):
    send(client, trader["token"])
    push(client, "51000", "51010")
    assert send(client, trader["token"], side="sell").status_code == 201
    expected = D("510") - D("510") * D("0.004") - D("500.10") * D("1.004")
    assert abs(D(summary(client, trader["token"])["realized_pnl"]) - expected) < D("0.0001")


def test_same_idempotency_key_creates_one_order(client, trader):
    key = "same-key-123"
    first = send(client, trader["token"], key=key).json()
    second = send(client, trader["token"], key=key).json()
    assert first["id"] == second["id"]
    assert len(client.get(f"{API}/orders", headers=auth(trader["token"])).json()) == 1


def test_max_position_is_rejected_with_limit_name(client, trader):
    r = send(client, trader["token"], qty="0.05")
    assert r.status_code == 422 and r.json()["error"]["code"] == "RISK_MAX_POSITION"
    assert summary(client, trader["token"])["cash"] == "10000"


def test_eleventh_order_in_a_minute_is_rejected(client, trader):
    for _ in range(10):
        assert send(client, trader["token"], qty="0.0001").status_code == 201
    r = send(client, trader["token"], qty="0.0001")
    assert r.json()["error"]["code"] == "RISK_ORDER_RATE"


def test_daily_loss_blocks_entries_but_allows_exits(client, trader):
    send(client, trader["token"], qty="0.03")
    push(client, "48000", "48010")
    blocked = send(client, trader["token"], qty="0.001")
    assert blocked.json()["error"]["code"] == "RISK_DAILY_LOSS"
    assert send(client, trader["token"], side="sell", qty="0.03").status_code == 201


def test_kill_switch_cancels_blocks_and_needs_2fa_to_release(client, clock, trader):
    token = trader["token"]
    send(client, token, qty="0.01")
    send(client, token, side="sell", qty="0.01", type="limit", limit_price="60000")
    result = client.post(f"{API}/killswitch", headers=auth(token), json={}).json()
    assert result["cancelled"] == 1 and result["halted"] is True
    assert send(client, token, qty="0.001").json()["error"]["code"] == "KILL_SWITCH_ACTIVE"
    assert send(client, token, side="sell", qty="0.001").status_code == 201
    assert client.delete(f"{API}/killswitch", headers=auth(token)).status_code == 401
    code = code_for(trader["creds"]["secret"], clock)
    assert client.delete(f"{API}/killswitch", headers={**auth(token), "X-TOTP": code}).status_code == 204
    assert send(client, token, qty="0.001").status_code == 201


def test_flatten_needs_2fa_and_closes_positions(client, clock, trader):
    token = trader["token"]
    send(client, token)
    assert client.post(f"{API}/killswitch", headers=auth(token), json={"close_positions": True}).status_code == 401
    assert client.get(f"{API}/positions", headers=auth(token)).json() != []
    headers = {**auth(token), "X-TOTP": code_for(trader["creds"]["secret"], clock)}
    result = client.post(f"{API}/killswitch", headers=headers, json={"close_positions": True}).json()
    assert result["closed"] == 1 and result["positions_left"] == 0
    assert client.get(f"{API}/positions", headers=auth(token)).json() == []


def test_close_position_releases_resting_sells(client, trader):
    token = trader["token"]
    send(client, token)
    send(client, token, side="sell", type="stop", stop_price="40000")
    r = client.post(f"{API}/positions/{IID}/close", headers=auth(token))
    assert r.status_code == 200 and r.json()["status"] == "filled"
    orders = client.get(f"{API}/orders", headers=auth(token)).json()
    assert {o["status"] for o in orders} == {"filled", "cancelled"}


def test_resting_stop_survives_restart_and_fires(client, trader):
    token = trader["token"]
    send(client, token)
    order = send(client, token, side="sell", type="stop", stop_price="49000").json()
    engine = client.app.state.trading
    engine.sims.clear()
    client.portal.call(engine.start)
    push(client, "48900", "48910")
    orders = client.get(f"{API}/orders", headers=auth(token)).json()
    assert next(o for o in orders if o["id"] == order["id"])["status"] == "filled"
    assert client.get(f"{API}/positions", headers=auth(token)).json() == []


def test_users_cannot_touch_each_others_orders(client, clock, trader):
    other = login(client, clock, register_user(client, clock, trader["token"], "friend"))
    order = send(client, trader["token"], type="limit", limit_price="40000").json()
    assert client.delete(f"{API}/orders/{order['id']}", headers=auth(other)).status_code == 404
    assert client.get(f"{API}/orders", headers=auth(other)).json() == []
    assert client.get(f"{API}/positions", headers=auth(other)).json() == []
    account = summary(client, trader["token"])
    assert client.get(f"{API}/accounts/{account['id']}/summary", headers=auth(other)).status_code == 404


def test_raising_a_limit_needs_2fa_and_waits(client, clock, trader):
    token = trader["token"]
    assert client.put(f"{API}/risk-limits", headers=auth(token), json={"daily_loss_eur": "100"}).status_code == 401
    headers = {**auth(token), "X-TOTP": code_for(trader["creds"]["secret"], clock)}
    limits = client.put(f"{API}/risk-limits", headers=headers, json={"daily_loss_eur": "100"}).json()
    assert D(limits["daily_loss_eur"]) == 50 and limits["pending"] == {"daily_loss_eur": "100"}
    lowered = client.put(f"{API}/risk-limits", headers=auth(token), json={"max_position_pct": "10"}).json()
    assert D(lowered["max_position_pct"]) == 10


def test_preview_changes_nothing(client, trader):
    body = {"instrument_id": IID, "side": "buy", "type": "limit", "qty": "0.01", "limit_price": "40000"}
    preview = client.post(f"{API}/orders/preview", headers=auth(trader["token"]), json=body).json()
    assert preview["ok"] is True and preview["warnings"]
    assert client.get(f"{API}/orders", headers=auth(trader["token"])).json() == []
    assert summary(client, trader["token"])["cash"] == "10000"


def test_watchlists_and_drawings_are_scoped(client, clock, trader):
    token = trader["token"]
    other = login(client, clock, register_user(client, clock, token, "friend"))
    [default] = client.get(f"{API}/watchlists", headers=auth(token)).json()
    assert IID in default["symbols"]
    r = client.patch(f"{API}/watchlists/{default['id']}", headers=auth(token), json={"symbols": [IID]})
    assert r.json()["symbols"] == [IID]
    assert client.patch(f"{API}/watchlists/{default['id']}", headers=auth(other), json={"name": "x"}).status_code == 404
    client.put(f"{API}/drawings/{IID}", headers=auth(token), json={"items": [{"name": "segment"}]})
    assert client.get(f"{API}/drawings/{IID}", headers=auth(token)).json()["items"] == [{"name": "segment"}]
    assert client.get(f"{API}/drawings/{IID}", headers=auth(other)).json()["items"] == []

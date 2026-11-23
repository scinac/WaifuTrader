"""Internal paper-trading simulator on live quotes (used for Kraken, which has no testnet).

Fill model (FSD open question, default chosen here):
- market orders fill immediately at the opposite side of the spread (buy at ask, sell at bid)
- limit orders fill at their limit price once the opposite side crosses it
- every fill pays a taker fee (default 0.40%; check Kraken's current fee schedule)
Spot only, long only, one quote currency per simulator. Phase 2 adds stops/brackets.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal

from tt.adapters.base import AccountSummary, OrderRequest, OrderUpdate, Position, Quote


@dataclass
class _Open:
    req: OrderRequest
    reserved: Decimal  # cash (buy) or qty (sell) reserved


class PaperSimulator:
    def __init__(self, cash: Decimal, currency: str = "EUR", fee_rate: Decimal = Decimal("0.004")):
        self.currency = currency
        self.fee_rate = fee_rate
        self.cash = cash
        self.positions: dict[str, Position] = {}
        self.quotes: dict[str, Quote] = {}
        self.open_orders: dict[str, _Open] = {}
        self._seen: set[str] = set()

    def _now(self) -> datetime:
        return datetime.now(timezone.utc)

    def _reject(self, req: OrderRequest, reason: str) -> OrderUpdate:
        return OrderUpdate(req.client_order_id, "rejected", self._now(), reason=reason)

    def _held(self, iid: str) -> Decimal:
        p = self.positions.get(iid)
        return p.qty if p else Decimal(0)

    def _reserved_qty(self, iid: str) -> Decimal:
        return sum((o.reserved for o in self.open_orders.values()
                    if o.req.instrument_id == iid and o.req.side == "sell"), Decimal(0))

    def _reserved_cash(self) -> Decimal:
        return sum((o.reserved for o in self.open_orders.values() if o.req.side == "buy"), Decimal(0))

    def place_order(self, req: OrderRequest) -> OrderUpdate:
        if req.client_order_id in self._seen:
            return self._reject(req, "DUPLICATE_CLIENT_ORDER_ID")
        if req.qty <= 0:
            return self._reject(req, "INVALID_QTY")
        if req.type not in ("market", "limit"):
            return self._reject(req, "ORDER_TYPE_NOT_SUPPORTED_YET")
        if req.type == "limit" and (req.limit_price is None or req.limit_price <= 0):
            return self._reject(req, "LIMIT_PRICE_REQUIRED")
        q = self.quotes.get(req.instrument_id)
        if req.type == "market" and (q is None or (q.ask if req.side == "buy" else q.bid) is None):
            return self._reject(req, "NO_QUOTE")

        if req.side == "buy":
            price = req.limit_price if req.type == "limit" else q.ask
            needed = req.qty * price * (1 + self.fee_rate)
            if needed > self.cash - self._reserved_cash():
                return self._reject(req, "INSUFFICIENT_FUNDS")
            reserved = needed
        else:
            available = self._held(req.instrument_id) - self._reserved_qty(req.instrument_id)
            if req.qty > available:
                return self._reject(req, "INSUFFICIENT_POSITION")
            reserved = req.qty

        self._seen.add(req.client_order_id)
        if req.type == "market":
            return self._fill(req, q.ask if req.side == "buy" else q.bid)
        self.open_orders[req.client_order_id] = _Open(req, reserved)
        update = self._try_fill_limit(req.client_order_id)
        return update or OrderUpdate(req.client_order_id, "accepted", self._now(),
                                     broker_order_id=f"sim-{req.client_order_id}")

    def cancel_order(self, client_order_id: str) -> OrderUpdate:
        if self.open_orders.pop(client_order_id, None) is None:
            return OrderUpdate(client_order_id, "rejected", self._now(), reason="UNKNOWN_ORDER")
        return OrderUpdate(client_order_id, "cancelled", self._now())

    def on_quote(self, quote: Quote) -> list[OrderUpdate]:
        self.quotes[quote.instrument_id] = quote
        updates = []
        for coid in list(self.open_orders):
            if self.open_orders[coid].req.instrument_id == quote.instrument_id:
                if (u := self._try_fill_limit(coid)) is not None:
                    updates.append(u)
        return updates

    def _try_fill_limit(self, coid: str) -> OrderUpdate | None:
        o = self.open_orders[coid]
        q = self.quotes.get(o.req.instrument_id)
        if q is None:
            return None
        lp = o.req.limit_price
        if o.req.side == "buy" and q.ask is not None and q.ask <= lp:
            del self.open_orders[coid]
            return self._fill(o.req, lp)
        if o.req.side == "sell" and q.bid is not None and q.bid >= lp:
            del self.open_orders[coid]
            return self._fill(o.req, lp)
        return None

    def _fill(self, req: OrderRequest, price: Decimal) -> OrderUpdate:
        notional = req.qty * price
        fee = notional * self.fee_rate
        held = self._held(req.instrument_id)
        if req.side == "buy":
            self.cash -= notional + fee
            old = self.positions.get(req.instrument_id)
            new_qty = held + req.qty
            # average cost includes the purchase fee (matches the moving-average tax rule)
            avg = ((old.avg_price * old.qty if old else Decimal(0)) + notional + fee) / new_qty
            self.positions[req.instrument_id] = Position(req.instrument_id, new_qty, avg)
        else:
            self.cash += notional - fee
            new_qty = held - req.qty
            if new_qty == 0:
                self.positions.pop(req.instrument_id, None)
            else:
                old = self.positions[req.instrument_id]
                self.positions[req.instrument_id] = Position(req.instrument_id, new_qty, old.avg_price)
        return OrderUpdate(req.client_order_id, "filled", self._now(),
                           broker_order_id=f"sim-{req.client_order_id}", filled_qty=req.qty,
                           fill_price=price, fee=fee, fee_currency=self.currency)

    def account(self) -> AccountSummary:
        equity = self.cash
        for p in self.positions.values():
            q = self.quotes.get(p.instrument_id)
            mark = (q.bid if q and q.bid is not None else p.avg_price)
            equity += p.qty * mark
        return AccountSummary(self.currency, self.cash, equity, self.cash - self._reserved_cash())

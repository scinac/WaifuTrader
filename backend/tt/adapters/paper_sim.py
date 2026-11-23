"""Internal paper-trading simulator on live quotes (used for Kraken, which has no testnet).

Fill model (FSD open question, default chosen here):
- market orders fill immediately at the opposite side of the spread (buy at ask, sell at bid)
- limit orders fill at their limit price once the opposite side crosses it
- stop, take-profit and trailing orders become market orders when triggered
- stop-limit orders become limit orders when triggered
- every fill pays a taker fee (default 0.40%; check Kraken's current fee schedule)
Spot only, long only, one quote currency per simulator.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal

from tt.adapters.base import AccountSummary, OrderRequest, OrderUpdate, Position, Quote

SUPPORTED_TYPES = ("market", "limit", "stop", "stop_limit", "take_profit", "trailing_stop")


@dataclass
class _Open:
    req: OrderRequest
    reserved: Decimal  # cash (buy) or qty (sell) reserved
    triggered: bool = False  # stop-limit: the stop has fired
    anchor: Decimal | None = None  # trailing: best price seen since placement


def _stop_crossed(side: str, touch: Decimal, stop: Decimal) -> bool:
    return touch >= stop if side == "buy" else touch <= stop


def _target_reached(side: str, touch: Decimal, target: Decimal) -> bool:
    return touch <= target if side == "buy" else touch >= target


class PaperSimulator:
    def __init__(self, cash: Decimal, currency: str = "EUR", fee_rate: Decimal = Decimal("0.004")):
        self.currency = currency
        self.fee_rate = fee_rate
        self.cash = cash
        self.positions: dict[str, Position] = {}
        self.quotes: dict[str, Quote] = {}
        self.open_orders: dict[str, _Open] = {}
        self.dirty: set[str] = set()  # open orders whose trigger state changed
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

    @staticmethod
    def _touch(side: str, q: Quote | None) -> Decimal | None:
        if q is None:
            return None
        return q.ask if side == "buy" else q.bid

    @staticmethod
    def _trail(req: OrderRequest, anchor: Decimal) -> Decimal:
        return req.trail_amount if req.trail_amount is not None else anchor * req.trail_pct / 100

    def _invalid(self, req: OrderRequest, touch: Decimal | None) -> str | None:
        positive = lambda v: v is not None and v > 0  # noqa: E731
        if req.type not in SUPPORTED_TYPES:
            return "ORDER_TYPE_NOT_SUPPORTED_YET"
        if req.type in ("limit", "stop_limit") and not positive(req.limit_price):
            return "LIMIT_PRICE_REQUIRED"
        if req.type in ("stop", "stop_limit") and not positive(req.stop_price):
            return "STOP_PRICE_REQUIRED"
        if req.type == "take_profit" and not positive(req.take_profit):
            return "TARGET_PRICE_REQUIRED"
        if req.type == "trailing_stop":
            if (req.trail_amount is None) == (req.trail_pct is None):
                return "TRAIL_REQUIRED"
            if not positive(req.trail_amount or req.trail_pct):
                return "TRAIL_REQUIRED"
            if req.trail_pct is not None and req.trail_pct >= 100:
                return "TRAIL_REQUIRED"
        if req.type != "limit" and touch is None:
            return "NO_QUOTE"
        if req.type in ("stop", "stop_limit") and _stop_crossed(req.side, touch, req.stop_price):
            return "STOP_ALREADY_TRIGGERED"
        if req.type == "take_profit" and _target_reached(req.side, touch, req.take_profit):
            return "TARGET_ALREADY_REACHED"
        return None

    def reference_price(self, req: OrderRequest) -> Decimal | None:
        """The price used to estimate cost and reserve funds."""
        touch = self._touch(req.side, self.quotes.get(req.instrument_id))
        if req.type in ("limit", "stop_limit"):
            return req.limit_price
        if req.type == "stop":
            return req.stop_price
        if req.type == "take_profit":
            return req.take_profit
        if req.type == "trailing_stop" and touch is not None:
            distance = self._trail(req, touch)
            return touch + distance if req.side == "buy" else touch - distance
        return touch

    def place_order(self, req: OrderRequest) -> OrderUpdate:
        if req.client_order_id in self._seen:
            return self._reject(req, "DUPLICATE_CLIENT_ORDER_ID")
        if req.qty <= 0:
            return self._reject(req, "INVALID_QTY")
        touch = self._touch(req.side, self.quotes.get(req.instrument_id))
        if (reason := self._invalid(req, touch)) is not None:
            return self._reject(req, reason)

        if req.side == "buy":
            needed = req.qty * self.reference_price(req) * (1 + self.fee_rate)
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
            return self._fill(req, touch)
        anchor = touch if req.type == "trailing_stop" else None
        self.open_orders[req.client_order_id] = _Open(req, reserved, anchor=anchor)
        update = self._try_fill(req.client_order_id)
        return update or OrderUpdate(req.client_order_id, "accepted", self._now(),
                                     broker_order_id=f"sim-{req.client_order_id}")

    def restore_open(self, req: OrderRequest, triggered: bool, anchor: Decimal | None) -> None:
        """Re-create a resting order after a restart, without trying to fill it."""
        price = self.reference_price(req)
        if req.side == "buy":
            if price is None:  # trailing buy with no quote yet: use its stored best price
                price = (anchor or Decimal(0)) + self._trail(req, anchor or Decimal(0))
            reserved = req.qty * price * (1 + self.fee_rate)
        else:
            reserved = req.qty
        self._seen.add(req.client_order_id)
        self.open_orders[req.client_order_id] = _Open(req, reserved, triggered, anchor)

    def cancel_order(self, client_order_id: str) -> OrderUpdate:
        if self.open_orders.pop(client_order_id, None) is None:
            return OrderUpdate(client_order_id, "rejected", self._now(), reason="UNKNOWN_ORDER")
        self.dirty.discard(client_order_id)
        return OrderUpdate(client_order_id, "cancelled", self._now())

    def on_quote(self, quote: Quote) -> list[OrderUpdate]:
        self.quotes[quote.instrument_id] = quote
        updates = []
        for coid in list(self.open_orders):
            if self.open_orders[coid].req.instrument_id == quote.instrument_id:
                if (u := self._try_fill(coid)) is not None:
                    updates.append(u)
        return updates

    def _try_fill(self, coid: str) -> OrderUpdate | None:
        o = self.open_orders[coid]
        req = o.req
        touch = self._touch(req.side, self.quotes.get(req.instrument_id))
        if touch is None:
            return None

        if req.type == "stop_limit" and not o.triggered and _stop_crossed(req.side, touch, req.stop_price):
            o.triggered = True
            self.dirty.add(coid)

        price: Decimal | None = None
        if req.type == "limit" or (req.type == "stop_limit" and o.triggered):
            if _target_reached(req.side, touch, req.limit_price):
                price = req.limit_price
        elif req.type == "stop" and _stop_crossed(req.side, touch, req.stop_price):
            price = touch
        elif req.type == "take_profit" and _target_reached(req.side, touch, req.take_profit):
            price = touch
        elif req.type == "trailing_stop":
            best = o.anchor if o.anchor is not None else touch
            best = min(best, touch) if req.side == "buy" else max(best, touch)
            if best != o.anchor:
                o.anchor = best
                self.dirty.add(coid)
            distance = self._trail(req, best)
            level = best + distance if req.side == "buy" else best - distance
            if _stop_crossed(req.side, touch, level):
                price = touch

        if price is None:
            return None
        del self.open_orders[coid]
        self.dirty.discard(coid)
        if req.side == "buy":
            # A gap can move a triggered order past the price its funds were reserved at.
            needed = req.qty * price * (1 + self.fee_rate)
            if needed > self.cash - self._reserved_cash():
                return self._reject(req, "INSUFFICIENT_FUNDS")
        return self._fill(req, price)

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

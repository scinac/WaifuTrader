"""Order manager for paper accounts (FSD: Order management and risk controls).

Every order passes through the risk checks before reaching the simulator, and every state
change is committed to the database. If a commit fails, the in-memory simulator is dropped
and rebuilt from the database, so the database stays the source of truth.
"""
from __future__ import annotations

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tt.adapters.base import OrderRequest, OrderUpdate, Position, Quote
from tt.adapters.paper_sim import PaperSimulator
from tt.core.errors import ApiError
from tt.db.models import (
    AuditLog,
    BrokerAccount,
    Fill,
    Instrument,
    Order,
    PaperBalance,
    PositionRow,
    RiskLimit,
    utcnow,
)
from tt.marketdata.hub import Hub
from tt.marketdata.serialize import d

log = logging.getLogger(__name__)

VIENNA = ZoneInfo("Europe/Vienna")
OPEN_STATUSES = ("accepted", "partially_filled")
FAT_FINGER_PCT = Decimal(10)

REASONS = {
    "RISK_ORDER_RATE": "Too many orders this minute. Wait a moment and try again.",
    "RISK_DAILY_LOSS": "Daily loss limit reached. New entries are blocked until midnight Vienna time.",
    "RISK_MAX_POSITION": "This order would exceed your maximum position size.",
    "KILL_SWITCH_ACTIVE": "The kill switch is on. Turn it off before opening new positions.",
    "STALE_QUOTE": "The price feed is stale, so a market order can't be filled safely.",
    "NO_QUOTE": "No live price is available for this instrument yet.",
    "INSUFFICIENT_FUNDS": "Not enough available cash for this order.",
    "INSUFFICIENT_POSITION": "You don't hold enough available quantity to sell.",
    "INVALID_QTY": "Quantity must be greater than zero.",
    "LIMIT_PRICE_REQUIRED": "Enter a limit price.",
    "STOP_PRICE_REQUIRED": "Enter a stop price.",
    "TARGET_PRICE_REQUIRED": "Enter a target price.",
    "TRAIL_REQUIRED": "Enter either a trail amount or a trail percentage below 100.",
    "STOP_ALREADY_TRIGGERED": "The market is already past this stop price.",
    "TARGET_ALREADY_REACHED": "The market is already past this target price.",
    "ORDER_TYPE_NOT_SUPPORTED_YET": "This order type isn't available yet.",
}


@dataclass(frozen=True)
class OrderFields:
    instrument_id: str
    side: str
    type: str
    qty: Decimal
    limit_price: Decimal | None = None
    stop_price: Decimal | None = None
    take_profit: Decimal | None = None
    trail_amount: Decimal | None = None
    trail_pct: Decimal | None = None
    tif: str = "gtc"


def order_json(o: Order) -> dict:
    return {
        "id": o.id, "client_order_id": o.client_order_id, "account_id": o.account_id,
        "instrument_id": o.instrument_id, "source": o.source, "side": o.side, "type": o.type,
        "qty": d(o.qty), "limit_price": d(o.limit_price), "stop_price": d(o.stop_price),
        "take_profit": d(o.take_profit), "trail_amount": d(o.trail_amount),
        "trail_pct": d(o.trail_pct), "tif": o.tif, "status": o.status, "reason": o.reason,
        "filled_qty": d(o.filled_qty), "avg_fill_price": d(o.avg_fill_price),
        "triggered": o.triggered, "trail_anchor": d(o.trail_anchor),
        "created_at": o.created_at.isoformat(), "updated_at": o.updated_at.isoformat(),
    }


def next_vienna_midnight(now: datetime) -> datetime:
    local = now.astimezone(VIENNA)
    tomorrow = (local + timedelta(days=1)).date()
    return datetime(tomorrow.year, tomorrow.month, tomorrow.day, tzinfo=VIENNA).astimezone(timezone.utc)


def vienna_day(now: datetime) -> str:
    return now.astimezone(VIENNA).date().isoformat()


class TradingEngine:
    def __init__(self, sessionmaker: async_sessionmaker, hub: Hub, starting_cash: Decimal,
                 fee_rate: Decimal, stale_after_s: float):
        self.sessionmaker = sessionmaker
        self.hub = hub
        self.starting_cash = starting_cash
        self.fee_rate = fee_rate
        self.stale_after = stale_after_s
        self.lock = asyncio.Lock()
        self.sims: dict[str, PaperSimulator] = {}
        self.owners: dict[str, str] = {}  # account id -> user id
        self.quotes: dict[str, Quote] = {}
        self.quote_at: dict[str, float] = {}
        self._day: str | None = None

    # --- lifecycle ----------------------------------------------------------
    async def start(self) -> None:
        """Load every paper account, so resting orders trigger with nobody logged in."""
        async with self.lock, self.sessionmaker() as s:
            accounts = (await s.execute(select(BrokerAccount).where(BrokerAccount.mode == "paper"))).scalars()
            for account in accounts.all():
                await self._sim(s, account)

    async def _sim(self, s: AsyncSession, account: BrokerAccount) -> PaperSimulator:
        if (sim := self.sims.get(account.id)) is not None:
            return sim
        balance = await s.get(PaperBalance, account.id)
        sim = PaperSimulator(balance.cash, balance.currency, self.fee_rate)
        sim.quotes = self.quotes
        rows = (await s.execute(select(PositionRow).where(PositionRow.account_id == account.id,
                                                          PositionRow.qty > 0))).scalars()
        for row in rows:
            sim.positions[row.instrument_id] = Position(row.instrument_id, row.qty, row.avg_price_eur)
        orders = (await s.execute(select(Order).where(Order.account_id == account.id,
                                                      Order.status.in_(OPEN_STATUSES)))).scalars()
        for o in orders:
            sim.restore_open(self._request(o.client_order_id, o), o.triggered, o.trail_anchor)
        self.sims[account.id] = sim
        self.owners[account.id] = account.user_id
        return sim

    @staticmethod
    def _request(coid: str, f: OrderFields | Order) -> OrderRequest:
        return OrderRequest(coid, f.instrument_id, f.side, f.type, f.qty, limit_price=f.limit_price,
                            stop_price=f.stop_price, trail_amount=f.trail_amount,
                            trail_pct=f.trail_pct, take_profit=f.take_profit, tif=f.tif)

    async def _account(self, s: AsyncSession, user_id: str) -> BrokerAccount:
        account = (await s.execute(select(BrokerAccount).where(
            BrokerAccount.user_id == user_id, BrokerAccount.broker == "kraken",
            BrokerAccount.mode == "paper"))).scalar_one_or_none()
        if account is None:
            account = BrokerAccount(user_id=user_id, broker="kraken", mode="paper", status="connected")
            s.add(account)
            await s.flush()
            s.add(PaperBalance(account_id=account.id, currency="EUR", starting_cash=self.starting_cash,
                               cash=self.starting_cash))
            await s.flush()
        return account

    async def _limits(self, s: AsyncSession, user_id: str) -> RiskLimit:
        limits = await s.get(RiskLimit, user_id)
        if limits is None:
            limits = RiskLimit(user_id=user_id, daily_loss_eur=Decimal(50), max_position_pct=Decimal(20),
                               max_orders_per_min=10, pending={}, halted=False)
            s.add(limits)
            await s.flush()
        if limits.pending and limits.pending_raise_at and limits.pending_raise_at <= utcnow():
            for key, value in limits.pending.items():
                setattr(limits, key, int(value) if key == "max_orders_per_min" else Decimal(value))
            limits.pending = {}
            limits.pending_raise_at = None
        return limits

    async def _balance(self, s: AsyncSession, account: BrokerAccount, sim: PaperSimulator) -> PaperBalance:
        balance = await s.get(PaperBalance, account.id)
        today = vienna_day(utcnow())
        if balance.day != today:
            balance.day = today
            balance.day_start_equity = sim.account().equity
        return balance

    # --- risk ---------------------------------------------------------------
    async def _risk_code(self, s: AsyncSession, account: BrokerAccount, limits: RiskLimit,
                         sim: PaperSimulator, req: OrderRequest, bypass: bool) -> str | None:
        if not bypass:
            cutoff = utcnow() - timedelta(minutes=1)
            recent = (await s.execute(select(func.count()).select_from(Order).where(
                Order.account_id == account.id, Order.created_at >= cutoff,
                Order.client_order_id != req.client_order_id))).scalar_one()
            if recent >= limits.max_orders_per_min:
                return "RISK_ORDER_RATE"
            seen = self.quote_at.get(req.instrument_id)
            if req.type == "market" and seen is not None and time.monotonic() - seen > self.stale_after:
                return "STALE_QUOTE"
        if req.side == "sell":
            return None  # exits are always allowed
        if limits.halted:
            return "KILL_SWITCH_ACTIVE"
        balance = await self._balance(s, account, sim)
        summary = sim.account()
        if balance.day_start_equity - summary.equity >= limits.daily_loss_eur:
            return "RISK_DAILY_LOSS"
        price = sim.reference_price(req)
        if price is not None:
            held = sim.positions[req.instrument_id].qty if req.instrument_id in sim.positions else Decimal(0)
            resting = sum((o.req.qty for o in sim.open_orders.values()
                           if o.req.side == "buy" and o.req.instrument_id == req.instrument_id), Decimal(0))
            if (held + resting + req.qty) * price > summary.equity * limits.max_position_pct / 100:
                return "RISK_MAX_POSITION"
        return None

    # --- persistence --------------------------------------------------------
    async def _apply(self, s: AsyncSession, account_id: str, sim: PaperSimulator, order: Order,
                     update: OrderUpdate) -> None:
        now = utcnow()
        order.status = update.status
        order.reason = update.reason
        order.updated_at = now
        if update.broker_order_id:
            order.broker_order_id = update.broker_order_id
        resting = sim.open_orders.get(order.client_order_id)
        if resting:
            order.triggered = resting.triggered
            order.trail_anchor = resting.anchor
        if update.status != "filled":
            return

        price, qty = update.fill_price, update.filled_qty
        row = await s.get(PositionRow, (account_id, order.instrument_id))
        if row is None:
            row = PositionRow(account_id=account_id, instrument_id=order.instrument_id, qty=Decimal(0),
                              avg_price_eur=Decimal(0), realized_pnl_eur=Decimal(0))
            s.add(row)
        if order.side == "sell":
            row.realized_pnl_eur += qty * (price - row.avg_price_eur) - update.fee
        held = sim.positions.get(order.instrument_id)
        row.qty = held.qty if held else Decimal(0)
        row.avg_price_eur = held.avg_price if held else Decimal(0)
        row.updated_at = now
        s.add(Fill(order_id=order.id, qty=qty, price=price, fee=update.fee,
                   fee_currency=update.fee_currency or sim.currency, filled_at=update.ts))
        order.filled_qty = qty
        order.avg_fill_price = price
        balance = await s.get(PaperBalance, account_id)
        balance.cash = sim.cash

    def _publish(self, user_id: str, orders: list[Order]) -> None:
        for order in orders:
            self.hub.publish(f"orders:{user_id}", order_json(order))

    async def _place(self, s: AsyncSession, account: BrokerAccount, sim: PaperSimulator,
                     fields: OrderFields, coid: str, bypass: bool = False) -> Order:
        limits = await self._limits(s, account.user_id)
        req = self._request(coid, fields)
        code = await self._risk_code(s, account, limits, sim, req, bypass)
        order = Order(client_order_id=coid, account_id=account.id, instrument_id=fields.instrument_id,
                      source="manual", side=fields.side, type=fields.type, qty=fields.qty,
                      limit_price=fields.limit_price, stop_price=fields.stop_price,
                      take_profit=fields.take_profit, trail_amount=fields.trail_amount,
                      trail_pct=fields.trail_pct, tif=fields.tif, status="created", reason="",
                      filled_qty=Decimal(0), triggered=False, created_at=utcnow(), updated_at=utcnow())
        s.add(order)
        await s.flush()
        update = (OrderUpdate(coid, "rejected", utcnow(), reason=code) if code else sim.place_order(req))
        await self._apply(s, account.id, sim, order, update)
        s.add(AuditLog(user_id=account.user_id, action="order.placed",
                       details={"order_id": order.id, "status": order.status, "reason": order.reason}))
        return order

    # --- quotes -------------------------------------------------------------
    async def on_quote(self, quote: Quote) -> None:
        self.quotes[quote.instrument_id] = quote  # accounts created later need the latest price
        self.quote_at[quote.instrument_id] = time.monotonic()
        async with self.lock:
            today = vienna_day(utcnow())
            roll = today != self._day
            self._day = today
            for account_id, sim in list(self.sims.items()):
                updates = sim.on_quote(quote)
                if not (updates or sim.dirty or roll):
                    continue
                try:
                    await self._persist_quote_effects(account_id, sim, updates, roll)
                except Exception:
                    log.exception("could not persist fills for account %s; reloading it", account_id)
                    self.sims.pop(account_id, None)

    async def _persist_quote_effects(self, account_id: str, sim: PaperSimulator,
                                     updates: list[OrderUpdate], roll: bool) -> None:
        changed: list[Order] = []
        async with self.sessionmaker() as s:
            for update in updates:
                order = (await s.execute(select(Order).where(
                    Order.account_id == account_id,
                    Order.client_order_id == update.client_order_id))).scalar_one()
                await self._apply(s, account_id, sim, order, update)
                changed.append(order)
            for coid in list(sim.dirty):
                resting = sim.open_orders.get(coid)
                if resting is None:
                    continue
                order = (await s.execute(select(Order).where(
                    Order.account_id == account_id, Order.client_order_id == coid))).scalar_one()
                order.triggered = resting.triggered
                order.trail_anchor = resting.anchor
                order.updated_at = utcnow()
            if roll:
                await self._balance(s, await s.get(BrokerAccount, account_id), sim)
            await s.commit()
        sim.dirty.clear()
        self._publish(self.owners[account_id], changed)

    # --- commands -----------------------------------------------------------
    async def place(self, user_id: str, fields: OrderFields, idempotency_key: str) -> tuple[Order, bool]:
        """Returns (order, created). Resending the same key returns the original order."""
        async with self.lock, self.sessionmaker() as s:
            account = await self._account(s, user_id)
            existing = (await s.execute(select(Order).where(
                Order.account_id == account.id,
                Order.client_order_id == idempotency_key))).scalar_one_or_none()
            if existing is not None:
                return existing, False
            await self._require_instrument(s, fields.instrument_id)
            sim = await self._sim(s, account)
            try:
                order = await self._place(s, account, sim, fields, idempotency_key)
                await s.commit()
            except Exception:
                self.sims.pop(account.id, None)
                raise
            self._publish(user_id, [order])
            return order, True

    @staticmethod
    async def _require_instrument(s: AsyncSession, iid: str) -> Instrument:
        instrument = await s.get(Instrument, iid)
        if instrument is None:
            raise ApiError(404, "INSTRUMENT_NOT_FOUND", f"Unknown instrument {iid}")
        if instrument.currency != "EUR":
            raise ApiError(422, "CURRENCY_NOT_SUPPORTED", "Paper accounts trade EUR instruments only")
        return instrument

    async def preview(self, user_id: str, fields: OrderFields) -> dict:
        async with self.lock, self.sessionmaker() as s:
            account = await self._account(s, user_id)
            await self._require_instrument(s, fields.instrument_id)
            sim = await self._sim(s, account)
            limits = await self._limits(s, user_id)
            req = self._request(f"preview-{uuid.uuid4()}", fields)
            quote = self.quotes.get(fields.instrument_id)
            touch = sim._touch(fields.side, quote)
            code = sim._invalid(req, touch) if fields.qty > 0 else "INVALID_QTY"
            code = code or await self._risk_code(s, account, limits, sim, req, False)
            price = sim.reference_price(req)
            notional = fee = total = None
            held = sim.positions[req.instrument_id].qty if req.instrument_id in sim.positions else Decimal(0)
            if price is not None:
                notional = fields.qty * price
                fee = notional * self.fee_rate
                total = notional + fee if fields.side == "buy" else notional - fee
                if code is None and fields.side == "buy" and total > sim.cash - sim._reserved_cash():
                    code = "INSUFFICIENT_FUNDS"
            if code is None and fields.side == "sell" and fields.qty > held - sim._reserved_qty(req.instrument_id):
                code = "INSUFFICIENT_POSITION"

            warnings = []
            market = quote.last if quote and quote.last is not None else touch
            for value in (fields.limit_price, fields.stop_price, fields.take_profit):
                if value is not None and market and abs(value - market) / market * 100 > FAT_FINGER_PCT:
                    warnings.append(f"Price is more than {FAT_FINGER_PCT}% away from the market.")
                    break
            await s.commit()
            return {
                "ok": code is None, "code": code, "message": REASONS.get(code) if code else None,
                "estimated_price": d(price), "notional": d(notional), "fee": d(fee), "total": d(total),
                "resulting_qty": d(held + fields.qty if fields.side == "buy" else held - fields.qty),
                "available_cash": d(sim.cash - sim._reserved_cash()), "warnings": warnings,
            }

    async def _owned_order(self, s: AsyncSession, user_id: str, order_id: str) -> tuple[BrokerAccount, Order]:
        account = await self._account(s, user_id)
        order = await s.get(Order, order_id)
        if order is None or order.account_id != account.id:
            raise ApiError(404, "ORDER_NOT_FOUND", "Order not found")
        return account, order

    async def cancel(self, user_id: str, order_id: str) -> Order:
        async with self.lock, self.sessionmaker() as s:
            account, order = await self._owned_order(s, user_id, order_id)
            if order.status not in OPEN_STATUSES:
                raise ApiError(409, "ORDER_NOT_OPEN", "Only open orders can be cancelled")
            sim = await self._sim(s, account)
            try:
                await self._cancel_one(s, account, sim, order)
                await s.commit()
            except Exception:
                self.sims.pop(account.id, None)
                raise
            self._publish(user_id, [order])
            return order

    async def _cancel_one(self, s: AsyncSession, account: BrokerAccount, sim: PaperSimulator, order: Order) -> None:
        await self._apply(s, account.id, sim, order, sim.cancel_order(order.client_order_id))
        s.add(AuditLog(user_id=account.user_id, action="order.cancelled", details={"order_id": order.id}))

    async def _open_orders(self, s: AsyncSession, account_id: str, instrument_id: str | None = None) -> list[Order]:
        query = select(Order).where(Order.account_id == account_id, Order.status.in_(OPEN_STATUSES))
        if instrument_id:
            query = query.where(Order.instrument_id == instrument_id)
        return list((await s.execute(query)).scalars())

    async def close_position(self, user_id: str, instrument_id: str) -> Order:
        async with self.lock, self.sessionmaker() as s:
            account = await self._account(s, user_id)
            sim = await self._sim(s, account)
            position = sim.positions.get(instrument_id)
            if position is None:
                raise ApiError(404, "POSITION_NOT_FOUND", "No open position for this instrument")
            if sim._touch("sell", self.quotes.get(instrument_id)) is None:
                raise ApiError(409, "NO_QUOTE", REASONS["NO_QUOTE"])
            try:
                changed = []
                for order in await self._open_orders(s, account.id, instrument_id):
                    if order.side == "sell":
                        await self._cancel_one(s, account, sim, order)
                        changed.append(order)
                fields = OrderFields(instrument_id, "sell", "market", position.qty)
                closing = await self._place(s, account, sim, fields, f"close-{uuid.uuid4()}", bypass=True)
                changed.append(closing)
                await s.commit()
            except Exception:
                self.sims.pop(account.id, None)
                raise
            self._publish(user_id, changed)
            return closing

    async def kill(self, user_id: str, close_positions: bool) -> dict:
        async with self.lock, self.sessionmaker() as s:
            account = await self._account(s, user_id)
            sim = await self._sim(s, account)
            limits = await self._limits(s, user_id)
            try:
                limits.halted = True
                changed = await self._open_orders(s, account.id)
                for order in changed:
                    await self._cancel_one(s, account, sim, order)
                cancelled = len(changed)
                closed = 0
                if close_positions:
                    for position in list(sim.positions.values()):
                        if sim._touch("sell", self.quotes.get(position.instrument_id)) is None:
                            continue
                        fields = OrderFields(position.instrument_id, "sell", "market", position.qty)
                        order = await self._place(s, account, sim, fields, f"flatten-{uuid.uuid4()}", bypass=True)
                        changed.append(order)
                        closed += order.status == "filled"
                s.add(AuditLog(user_id=user_id, action="killswitch.on",
                               details={"cancelled": cancelled, "closed": closed, "flatten": close_positions}))
                await s.commit()
            except Exception:
                self.sims.pop(account.id, None)
                raise
            self._publish(user_id, changed)
            remaining = len(sim.positions) if close_positions else None
            return {"halted": True, "cancelled": cancelled, "closed": closed, "positions_left": remaining}

    async def release(self, user_id: str) -> None:
        async with self.lock, self.sessionmaker() as s:
            limits = await self._limits(s, user_id)
            limits.halted = False
            s.add(AuditLog(user_id=user_id, action="killswitch.off", details={}))
            await s.commit()

    # --- queries ------------------------------------------------------------
    async def summary(self, user_id: str) -> dict:
        async with self.lock, self.sessionmaker() as s:
            account = await self._account(s, user_id)
            sim = await self._sim(s, account)
            balance = await self._balance(s, account, sim)
            limits = await self._limits(s, user_id)
            rows = (await s.execute(select(PositionRow).where(PositionRow.account_id == account.id))).scalars().all()
            summary = sim.account()
            realized = sum((r.realized_pnl_eur for r in rows), Decimal(0))
            daily = summary.equity - balance.day_start_equity
            result = {
                "id": account.id, "broker": account.broker, "mode": account.mode,
                "currency": summary.currency, "cash": d(summary.cash), "equity": d(summary.equity),
                "buying_power": d(summary.buying_power), "starting_cash": d(balance.starting_cash),
                "total_pnl": d(summary.equity - balance.starting_cash), "realized_pnl": d(realized),
                "daily_pnl": d(daily), "daily_loss_limit": d(limits.daily_loss_eur), "halted": limits.halted,
                "open_orders": len(sim.open_orders),
            }
            await s.commit()
            return result

    async def positions(self, user_id: str) -> list[dict]:
        async with self.lock, self.sessionmaker() as s:
            account = await self._account(s, user_id)
            sim = await self._sim(s, account)
            rows = (await s.execute(select(PositionRow).where(PositionRow.account_id == account.id))).scalars().all()
            await s.commit()
            out = []
            for row in rows:
                held = sim.positions.get(row.instrument_id)
                if held is None:
                    continue
                quote = self.quotes.get(row.instrument_id)
                mark = quote.bid if quote and quote.bid is not None else None
                value = held.qty * mark if mark is not None else None
                out.append({
                    "instrument_id": row.instrument_id, "qty": d(held.qty), "avg_price": d(held.avg_price),
                    "mark": d(mark), "market_value": d(value),
                    "unrealized_pnl": d(held.qty * (mark - held.avg_price)) if mark is not None else None,
                    "realized_pnl": d(row.realized_pnl_eur),
                })
            return out

    async def orders(self, user_id: str, status: str | None, limit: int) -> list[Order]:
        async with self.lock, self.sessionmaker() as s:
            account = await self._account(s, user_id)
            await s.commit()
            query = select(Order).where(Order.account_id == account.id)
            if status == "open":
                query = query.where(Order.status.in_(OPEN_STATUSES))
            elif status:
                query = query.where(Order.status == status)
            return list((await s.execute(query.order_by(Order.created_at.desc()).limit(limit))).scalars())

    async def fills(self, user_id: str, limit: int) -> list[dict]:
        async with self.lock, self.sessionmaker() as s:
            account = await self._account(s, user_id)
            await s.commit()
            rows = (await s.execute(select(Fill, Order).join(Order, Fill.order_id == Order.id)
                                    .where(Order.account_id == account.id)
                                    .order_by(Fill.filled_at.desc()).limit(limit))).all()
            return [{"id": f.id, "order_id": f.order_id, "instrument_id": o.instrument_id, "side": o.side,
                     "qty": d(f.qty), "price": d(f.price), "fee": d(f.fee), "fee_currency": f.fee_currency,
                     "filled_at": f.filled_at.isoformat()} for f, o in rows]

    async def risk_limits(self, user_id: str) -> RiskLimit:
        async with self.lock, self.sessionmaker() as s:
            limits = await self._limits(s, user_id)
            await s.commit()
            return limits

    async def update_limits(self, user_id: str, changes: dict, has_totp: bool) -> RiskLimit:
        """Lowering applies now. Raising needs 2FA and applies at the next Vienna midnight."""
        async with self.lock, self.sessionmaker() as s:
            limits = await self._limits(s, user_id)
            raises = {k: v for k, v in changes.items() if v > getattr(limits, k)}
            if raises and not has_totp:
                raise ApiError(401, "TOTP_REQUIRED", "A fresh 2FA code is required to raise a limit")
            pending = dict(limits.pending or {})
            for key, value in changes.items():
                pending.pop(key, None)
                if key in raises:
                    pending[key] = str(value)
                else:
                    setattr(limits, key, value)
            limits.pending = pending
            limits.pending_raise_at = next_vienna_midnight(utcnow()) if pending else None
            s.add(AuditLog(user_id=user_id, action="risk_limits.updated",
                           details={k: str(v) for k, v in changes.items()}))
            await s.commit()
            return limits

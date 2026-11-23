"""Paper trading, risk limits, watchlists and chart drawings (FSD: API contract)."""
from __future__ import annotations

from decimal import Decimal
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Header, Query, Request, Response
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tt.api.deps import current_user, get_db, get_vault
from tt.core.errors import ApiError
from tt.core.security import Vault, verify_totp
from tt.db.models import Drawing, Instrument, User, Watchlist, utcnow
from tt.marketdata.serialize import d
from tt.trading.engine import REASONS, OrderFields, TradingEngine, order_json

router = APIRouter(tags=["trading"])

Amount = Annotated[Decimal, Field(gt=0, max_digits=28, decimal_places=10, allow_inf_nan=False)]
PRICE_FIELDS = {
    "market": (), "limit": ("limit_price",), "stop": ("stop_price",),
    "stop_limit": ("stop_price", "limit_price"), "take_profit": ("take_profit",),
    "trailing_stop": ("trail_amount", "trail_pct"),
}


def engine(request: Request) -> TradingEngine:
    return request.app.state.trading


class OrderIn(BaseModel):
    instrument_id: str = Field(max_length=64)
    side: Literal["buy", "sell"]
    type: Literal["market", "limit", "stop", "stop_limit", "take_profit", "trailing_stop"]
    qty: Amount
    limit_price: Amount | None = None
    stop_price: Amount | None = None
    take_profit: Amount | None = None
    trail_amount: Amount | None = None
    trail_pct: Amount | None = None
    tif: Literal["day", "gtc", "ioc"] = "gtc"

    @model_validator(mode="after")
    def drop_unrelated_prices(self):
        allowed = PRICE_FIELDS[self.type]
        for name in ("limit_price", "stop_price", "take_profit", "trail_amount", "trail_pct"):
            if name not in allowed:
                setattr(self, name, None)
        return self

    def fields(self) -> OrderFields:
        return OrderFields(self.instrument_id, self.side, self.type, self.qty, self.limit_price,
                           self.stop_price, self.take_profit, self.trail_amount, self.trail_pct, self.tif)


@router.get("/accounts")
async def accounts(request: Request, user: User = Depends(current_user)):
    return [await engine(request).summary(user.id)]


@router.get("/accounts/{account_id}/summary")
async def account_summary(account_id: str, request: Request, user: User = Depends(current_user)):
    summary = await engine(request).summary(user.id)
    if summary["id"] != account_id:
        raise ApiError(404, "ACCOUNT_NOT_FOUND", "Account not found")
    return summary


@router.post("/orders/preview")
async def preview(body: OrderIn, request: Request, user: User = Depends(current_user)):
    return await engine(request).preview(user.id, body.fields())


@router.post("/orders", status_code=201)
async def place(body: OrderIn, request: Request,
                idempotency_key: str = Header(min_length=8, max_length=64),
                user: User = Depends(current_user)):
    order, _ = await engine(request).place(user.id, body.fields(), idempotency_key)
    if order.status == "rejected":
        raise ApiError(422, order.reason, REASONS.get(order.reason, "Order rejected"),
                       {"order_id": order.id})
    return order_json(order)


@router.get("/orders")
async def orders(request: Request, status: str | None = Query(None, max_length=20),
                 limit: int = Query(100, ge=1, le=500), user: User = Depends(current_user)):
    return [order_json(o) for o in await engine(request).orders(user.id, status, limit)]


@router.delete("/orders/{order_id}")
async def cancel(order_id: str, request: Request, user: User = Depends(current_user)):
    return order_json(await engine(request).cancel(user.id, order_id))


@router.get("/fills")
async def fills(request: Request, limit: int = Query(100, ge=1, le=500), user: User = Depends(current_user)):
    return await engine(request).fills(user.id, limit)


@router.get("/positions")
async def positions(request: Request, user: User = Depends(current_user)):
    return await engine(request).positions(user.id)


@router.post("/positions/{instrument_id:path}/close")
async def close_position(instrument_id: str, request: Request, user: User = Depends(current_user)):
    order = await engine(request).close_position(user.id, instrument_id)
    if order.status == "rejected":
        raise ApiError(422, order.reason, REASONS.get(order.reason, "Close rejected"))
    return order_json(order)


async def fresh_totp(request: Request, user: User, db: AsyncSession, vault: Vault) -> bool:
    code = request.headers.get("x-totp", "")
    db_user = await db.get(User, user.id)
    step = verify_totp(vault.decrypt(db_user.totp_secret_enc), code, db_user.totp_last_step)
    if step is None:
        return False
    db_user.totp_last_step = step
    await db.commit()
    return True


class KillIn(BaseModel):
    close_positions: bool = False


@router.post("/killswitch")
async def kill(body: KillIn, request: Request, user: User = Depends(current_user),
               db: AsyncSession = Depends(get_db), vault: Vault = Depends(get_vault)):
    if body.close_positions and not await fresh_totp(request, user, db, vault):
        raise ApiError(401, "TOTP_REQUIRED", "A fresh 2FA code is required to close all positions")
    return await engine(request).kill(user.id, body.close_positions)


@router.delete("/killswitch", status_code=204)
async def release(request: Request, user: User = Depends(current_user),
                  db: AsyncSession = Depends(get_db), vault: Vault = Depends(get_vault)):
    if not await fresh_totp(request, user, db, vault):
        raise ApiError(401, "TOTP_REQUIRED", "A fresh 2FA code is required to turn the kill switch off")
    await engine(request).release(user.id)
    return Response(status_code=204)


def limits_json(limits) -> dict:
    return {"daily_loss_eur": d(limits.daily_loss_eur), "max_position_pct": d(limits.max_position_pct),
            "max_orders_per_min": limits.max_orders_per_min, "pending": limits.pending or {},
            "pending_raise_at": limits.pending_raise_at.isoformat() if limits.pending_raise_at else None,
            "halted": limits.halted}


class LimitsIn(BaseModel):
    daily_loss_eur: Amount | None = None
    max_position_pct: Annotated[Decimal, Field(gt=0, le=100, decimal_places=4)] | None = None
    max_orders_per_min: Annotated[int, Field(ge=1, le=100)] | None = None


@router.get("/risk-limits")
async def get_limits(request: Request, user: User = Depends(current_user)):
    return limits_json(await engine(request).risk_limits(user.id))


@router.put("/risk-limits")
async def put_limits(body: LimitsIn, request: Request, user: User = Depends(current_user),
                     db: AsyncSession = Depends(get_db), vault: Vault = Depends(get_vault)):
    changes = body.model_dump(exclude_none=True)
    has_code = bool(request.headers.get("x-totp"))
    if has_code and not await fresh_totp(request, user, db, vault):
        raise ApiError(401, "TOTP_REQUIRED", "That 2FA code isn't valid")
    return limits_json(await engine(request).update_limits(user.id, changes, has_code))


# --- watchlists ---------------------------------------------------------------
class WatchlistIn(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    symbols: list[str] = Field(default_factory=list, max_length=200)


class WatchlistPatch(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=64)
    symbols: list[str] | None = Field(None, max_length=200)


def watchlist_json(w: Watchlist) -> dict:
    return {"id": w.id, "name": w.name, "symbols": w.symbols}


async def check_symbols(db: AsyncSession, symbols: list[str]) -> list[str]:
    unique = list(dict.fromkeys(symbols))
    if unique:
        known = set((await db.execute(select(Instrument.id).where(Instrument.id.in_(unique)))).scalars())
        if missing := [s for s in unique if s not in known]:
            raise ApiError(422, "INSTRUMENT_NOT_FOUND", f"Unknown instrument {missing[0]}")
    return unique


async def owned_watchlist(db: AsyncSession, user: User, watchlist_id: str) -> Watchlist:
    watchlist = await db.get(Watchlist, watchlist_id)
    if watchlist is None or watchlist.user_id != user.id:
        raise ApiError(404, "WATCHLIST_NOT_FOUND", "Watchlist not found")
    return watchlist


@router.get("/watchlists")
async def list_watchlists(request: Request, db: AsyncSession = Depends(get_db),
                          user: User = Depends(current_user)):
    rows = list((await db.execute(select(Watchlist).where(Watchlist.user_id == user.id)
                                  .order_by(Watchlist.created_at))).scalars())
    if not rows:
        tracked = await check_symbols(db, request.app.state.settings.tracked_symbols)
        first = Watchlist(user_id=user.id, name="Crypto", symbols=tracked)
        db.add(first)
        await db.commit()
        rows = [first]
    return [watchlist_json(w) for w in rows]


@router.post("/watchlists", status_code=201)
async def create_watchlist(body: WatchlistIn, db: AsyncSession = Depends(get_db),
                           user: User = Depends(current_user)):
    watchlist = Watchlist(user_id=user.id, name=body.name, symbols=await check_symbols(db, body.symbols))
    db.add(watchlist)
    await db.commit()
    return watchlist_json(watchlist)


@router.patch("/watchlists/{watchlist_id}")
async def patch_watchlist(watchlist_id: str, body: WatchlistPatch, db: AsyncSession = Depends(get_db),
                          user: User = Depends(current_user)):
    watchlist = await owned_watchlist(db, user, watchlist_id)
    if body.name is not None:
        watchlist.name = body.name
    if body.symbols is not None:
        watchlist.symbols = await check_symbols(db, body.symbols)
    await db.commit()
    return watchlist_json(watchlist)


@router.delete("/watchlists/{watchlist_id}", status_code=204)
async def delete_watchlist(watchlist_id: str, db: AsyncSession = Depends(get_db),
                           user: User = Depends(current_user)):
    await db.delete(await owned_watchlist(db, user, watchlist_id))
    await db.commit()
    return Response(status_code=204)


# --- drawings -----------------------------------------------------------------
class DrawingsIn(BaseModel):
    items: list[dict] = Field(max_length=200)


@router.get("/drawings/{instrument_id:path}")
async def get_drawings(instrument_id: str, db: AsyncSession = Depends(get_db),
                       user: User = Depends(current_user)):
    row = await db.get(Drawing, (user.id, instrument_id))
    return {"instrument_id": instrument_id, "items": row.items if row else []}


@router.put("/drawings/{instrument_id:path}")
async def put_drawings(instrument_id: str, body: DrawingsIn, db: AsyncSession = Depends(get_db),
                       user: User = Depends(current_user)):
    if await db.get(Instrument, instrument_id) is None:
        raise ApiError(404, "INSTRUMENT_NOT_FOUND", f"Unknown instrument {instrument_id}")
    row = await db.get(Drawing, (user.id, instrument_id))
    if row is None:
        row = Drawing(user_id=user.id, instrument_id=instrument_id, items=body.items, updated_at=utcnow())
        db.add(row)
    else:
        row.items = body.items
        row.updated_at = utcnow()
    await db.commit()
    return {"instrument_id": instrument_id, "items": row.items}

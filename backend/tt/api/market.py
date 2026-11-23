"""Instruments, bars and the live WebSocket (FSD API contract, phase 1 subset)."""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone
from typing import Literal

from fastapi import APIRouter, Depends, Query, Request, WebSocket, WebSocketDisconnect
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from tt.api.deps import current_user, extract_token, get_db, user_from_token
from tt.core.errors import ApiError
from tt.db.models import Instrument, User
from tt.marketdata.bars import INTERVALS, aggregate, bucket_start
from tt.marketdata.serialize import candle_json, quote_json
from tt.marketdata.store import load_bars

router = APIRouter(prefix="/instruments", tags=["instruments"])
ws_router = APIRouter()

Interval = Literal["1m", "5m", "15m", "1h", "4h", "1d", "1w"]
MAX_BARS = 5000
HEARTBEAT_S = 15


def instrument_json(i: Instrument) -> dict:
    return {"id": i.id, "broker": i.broker, "symbol": i.symbol, "name": i.name, "isin": i.isin,
            "exchange": i.exchange, "currency": i.currency, "asset_class": i.asset_class}


@router.get("/search")
async def search(q: str = Query(min_length=1, max_length=64), db: AsyncSession = Depends(get_db),
                 _: User = Depends(current_user)):
    like = f"%{q}%"
    rows = (await db.execute(
        select(Instrument).where(or_(Instrument.symbol.ilike(like), Instrument.name.ilike(like),
                                     Instrument.isin.ilike(like), Instrument.id.ilike(like)))
        .order_by(Instrument.symbol).limit(25)
    )).scalars().all()
    return [instrument_json(i) for i in rows]


# Bars route is declared before the detail route: instrument ids contain "/" (BTC/EUR).
@router.get("/{instrument_id:path}/bars")
async def bars(instrument_id: str, request: Request, interval: Interval = "1m",
               start: datetime | None = Query(None, alias="from"),
               end: datetime | None = Query(None, alias="to"),
               limit: int = Query(500, ge=1, le=MAX_BARS),
               db: AsyncSession = Depends(get_db), _: User = Depends(current_user)):
    if await db.get(Instrument, instrument_id) is None:
        raise ApiError(404, "INSTRUMENT_NOT_FOUND", f"Unknown instrument {instrument_id}")
    now = datetime.now(timezone.utc)
    end = _utc(end) if end else now
    span = INTERVALS[interval] * limit
    start = _utc(start) if start else end - span
    start = bucket_start(start, interval)
    minute_limit = min(int((end - start) / timedelta(minutes=1)) + 1, 200_000)
    candles = await load_bars(db, instrument_id, start, end, minute_limit)

    md = getattr(request.app.state, "marketdata", None)
    live = md.builders[instrument_id].current if md and instrument_id in md.builders else None
    if live is not None and start <= live.ts < end and (not candles or live.ts > candles[-1].ts):
        candles.append(live)

    out = candles if interval == "1m" else aggregate(candles, interval)
    return {"instrument_id": instrument_id, "interval": interval,
            "bars": [candle_json(c) for c in out[-limit:]]}


@router.get("/{instrument_id:path}")
async def detail(instrument_id: str, request: Request, db: AsyncSession = Depends(get_db),
                 _: User = Depends(current_user)):
    inst = await db.get(Instrument, instrument_id)
    if inst is None:
        raise ApiError(404, "INSTRUMENT_NOT_FOUND", f"Unknown instrument {instrument_id}")
    md = getattr(request.app.state, "marketdata", None)
    q = md.last_quote.get(instrument_id) if md else None
    return {**instrument_json(inst), "quote": quote_json(q) if q else None,
            "stale": md.stale.get(instrument_id) if md else None}


def _utc(dt: datetime) -> datetime:
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)


# --- WebSocket ---------------------------------------------------------------
@ws_router.websocket("/ws")
async def live(ws: WebSocket):
    """Protocol: {"op":"subscribe","channel":"quotes"|"candles:1m","symbols":[ids]} /
    {"op":"unsubscribe",...}. Server sends {"channel","data","ts"} and heartbeats.
    Alerts are always delivered."""
    app = ws.app
    async with app.state.db.sessionmaker() as db:
        user = await user_from_token(db, extract_token(ws.cookies, ws.headers)
                                     or ws.query_params.get("token"))
    if user is None:
        await ws.close(code=4401, reason="UNAUTHENTICATED")
        return
    await ws.accept()
    hub = app.state.hub
    md = getattr(app.state, "marketdata", None)
    sub = hub.subscribe()
    sub.add("alerts")
    sub.add(f"orders:{user.id}")

    async def sender():
        while True:
            try:
                topic, data = await asyncio.wait_for(sub.queue.get(), timeout=HEARTBEAT_S)
            except asyncio.TimeoutError:
                await ws.send_json({"channel": "heartbeat", "ts": _now()})
                continue
            channel = topic.rsplit(":", 2)[0] if topic.startswith("candles:") else topic.split(":")[0]
            await ws.send_json({"channel": channel, "data": data, "ts": _now()})

    send_task = asyncio.create_task(sender())
    try:
        while True:
            try:
                msg = json.loads(await ws.receive_text())
                op, channel, symbols = msg["op"], msg["channel"], list(msg.get("symbols", []))
            except (ValueError, KeyError, TypeError):
                await ws.send_json({"channel": "error", "data": {"code": "BAD_MESSAGE"}})
                continue
            if channel not in ("quotes", "candles:1m") or op not in ("subscribe", "unsubscribe"):
                await ws.send_json({"channel": "error", "data": {"code": "UNKNOWN_CHANNEL"}})
                continue
            for iid in symbols:
                topic = f"{channel}:{iid}"
                if op == "subscribe":
                    sub.add(topic)
                    if channel == "quotes" and md and (q := md.last_quote.get(iid)):
                        await ws.send_json({"channel": "quotes", "data": quote_json(q),
                                            "ts": _now()})
                else:
                    sub.remove(topic)
            await ws.send_json({"channel": "ack", "data": {"op": op, "channel": channel,
                                                           "symbols": symbols}})
    except WebSocketDisconnect:
        pass
    finally:
        send_task.cancel()
        sub.close()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()

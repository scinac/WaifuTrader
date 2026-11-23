from datetime import datetime, timedelta, timezone
from typing import Literal

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from tt.api.deps import audit, client_ip, get_db, require_admin
from tt.core.security import hash_token, new_token
from tt.db.models import Bar, Invite, User

router = APIRouter(prefix="/admin", tags=["admin"])


class InviteIn(BaseModel):
    role: Literal["trader", "admin"] = "trader"


@router.post("/invites", status_code=201)
async def create_invite(body: InviteIn, request: Request, db: AsyncSession = Depends(get_db),
                        admin: User = Depends(require_admin)):
    token = new_token()
    expires = datetime.now(timezone.utc) + timedelta(hours=request.app.state.settings.invite_ttl_hours)
    db.add(Invite(token_hash=hash_token(token), role=body.role, created_by=admin.id,
                  expires_at=expires))
    await audit(db, admin.id, "admin.invite_created", {"role": body.role}, client_ip(request))
    await db.commit()
    return {"invite_token": token, "role": body.role, "expires_at": expires.isoformat()}


@router.get("/users")
async def list_users(db: AsyncSession = Depends(get_db), _: User = Depends(require_admin)):
    users = (await db.execute(select(User).order_by(User.created_at))).scalars().all()
    return [{"id": u.id, "username": u.username, "role": u.role, "totp_enabled": u.totp_enabled,
             "disabled": u.disabled, "locked_until": u.locked_until.isoformat() if u.locked_until else None,
             "created_at": u.created_at.isoformat()} for u in users]


@router.get("/health")
async def health(request: Request, db: AsyncSession = Depends(get_db),
                 _: User = Depends(require_admin)):
    md = getattr(request.app.state, "marketdata", None)
    bars = (await db.execute(
        select(Bar.instrument_id, func.count(), func.max(Bar.ts))
        .where(Bar.interval == "1m").group_by(Bar.instrument_id)
    )).all()
    return {
        "server_time": datetime.now(timezone.utc).isoformat(),
        "marketdata": md.status() if md else {"enabled": False},
        "bars_1m": {i: {"count": c, "latest": t.isoformat() if t else None} for i, c, t in bars},
    }

"""Shared FastAPI dependencies: DB session, current user, admin check, fresh 2FA, audit."""
from collections.abc import AsyncIterator
from datetime import datetime, timezone

from fastapi import Depends, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tt.core.errors import ApiError
from tt.core.security import Vault, hash_token, verify_totp
from tt.db.models import AuditLog, Session, User

SESSION_COOKIE = "tt_session"


async def get_db(request: Request) -> AsyncIterator[AsyncSession]:
    async with request.app.state.db.sessionmaker() as s:
        yield s


def get_vault(request: Request) -> Vault:
    return request.app.state.vault


def client_ip(request: Request) -> str | None:
    return request.client.host if request.client else None


def extract_token(cookies, headers) -> str | None:
    if token := cookies.get(SESSION_COOKIE):
        return token
    auth = headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip() or None
    return None


async def user_from_token(db: AsyncSession, token: str | None) -> User | None:
    if not token:
        return None
    row = (
        await db.execute(
            select(Session, User)
            .join(User, User.id == Session.user_id)
            .where(Session.token_hash == hash_token(token))
        )
    ).first()
    if row is None:
        return None
    session, user = row
    if session.revoked or user.disabled or session.expires_at <= datetime.now(timezone.utc):
        return None
    return user


async def current_user(request: Request, db: AsyncSession = Depends(get_db)) -> User:
    user = await user_from_token(db, extract_token(request.cookies, request.headers))
    if user is None:
        raise ApiError(401, "UNAUTHENTICATED", "Login required")
    return user


async def require_admin(user: User = Depends(current_user)) -> User:
    if user.role != "admin":
        raise ApiError(403, "FORBIDDEN", "Admin only")
    return user


async def require_fresh_totp(
    request: Request,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
    vault: Vault = Depends(get_vault),
) -> User:
    """For 2FA-gated actions (FSD security): needs a fresh code in the X-TOTP header."""
    code = request.headers.get("x-totp", "")
    step = verify_totp(vault.decrypt(user.totp_secret_enc), code, user.totp_last_step)
    if step is None:
        raise ApiError(401, "TOTP_REQUIRED", "A fresh 2FA code is required for this action")
    user.totp_last_step = step
    await db.commit()
    return user


async def audit(db: AsyncSession, user_id: str | None, action: str, details: dict | None = None,
                ip: str | None = None) -> None:
    db.add(AuditLog(user_id=user_id, action=action, details=details or {}, ip=ip))

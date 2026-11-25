"""Auth flow (FSD security):

0. On a fresh install with no users, the first admin signs up via POST /auth/bootstrap
   (or `tt.cli create-admin`). The endpoint closes as soon as any user exists.
1. Anyone who can reach the server signs up as a trader (POST /auth/register); an admin
   promotes them via PATCH /admin/users/{id}. An optional invite (POST /admin/invites)
   can set the role up front. Registration returns a TOTP secret/URI.
3. User confirms 2FA with a code from their authenticator (POST /auth/totp/setup).
4. Login always needs username + password + TOTP (POST /auth/login).
"""
from datetime import datetime, timedelta, timezone
from typing import Literal

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tt.api.deps import (
    SESSION_COOKIE,
    audit,
    client_ip,
    current_user,
    extract_token,
    get_db,
    get_vault,
)
from tt.core.errors import ApiError
from tt.core.security import (
    Vault,
    check_password_policy,
    hash_password,
    hash_token,
    new_token,
    new_totp_secret,
    totp_uri,
    verify_password,
    verify_totp,
)
from tt.db.models import Invite, Session, User

router = APIRouter(prefix="/auth", tags=["auth"])
me_router = APIRouter(tags=["me"])

# Used to equalise timing when the username does not exist.
_DUMMY_HASH = hash_password("timing-equaliser-password")


class RegisterIn(BaseModel):
    invite_token: str | None = None  # optional: without one the account is a trader
    username: str = Field(min_length=3, max_length=64, pattern=r"^[a-zA-Z0-9_.-]+$")
    password: str


class TotpSetupOut(BaseModel):
    username: str
    totp_secret: str
    totp_uri: str


class TotpConfirmIn(BaseModel):
    username: str
    password: str
    code: str


class LoginIn(BaseModel):
    username: str
    password: str
    totp: str
    client: Literal["web", "cli"] = "web"


class UserOut(BaseModel):
    id: str
    username: str
    role: str
    leverage_opt_in: bool
    settings: dict


def user_out(u: User) -> UserOut:
    return UserOut(id=u.id, username=u.username, role=u.role,
                   leverage_opt_in=u.leverage_opt_in, settings=u.settings or {})


class BootstrapIn(BaseModel):
    username: str = Field(min_length=3, max_length=64, pattern=r"^[a-zA-Z0-9_.-]+$")
    password: str


async def _new_user(db: AsyncSession, vault: Vault, username: str, password: str,
                    role: str) -> tuple[User, str]:
    """Adds a user with 2FA not yet confirmed. Returns the user and its TOTP secret."""
    try:
        check_password_policy(password)
    except ValueError as e:
        raise ApiError(400, "WEAK_PASSWORD", str(e)) from e
    if (await db.execute(select(User).where(User.username == username))).scalar_one_or_none():
        raise ApiError(409, "USERNAME_TAKEN", "Username already exists")
    secret = new_totp_secret()
    user = User(username=username, password_hash=hash_password(password), role=role,
                totp_secret_enc=vault.encrypt(secret), totp_enabled=False)
    db.add(user)
    await db.flush()
    return user, secret


def _setup_out(user: User, secret: str) -> TotpSetupOut:
    return TotpSetupOut(username=user.username, totp_secret=secret,
                        totp_uri=totp_uri(secret, user.username))


async def _no_users(db: AsyncSession) -> bool:
    return (await db.execute(select(User.id).limit(1))).first() is None


@router.get("/bootstrap")
async def bootstrap_status(db: AsyncSession = Depends(get_db)):
    """Whether the first admin account can still be created from the sign-in screen."""
    return {"open": await _no_users(db)}


@router.post("/bootstrap", response_model=TotpSetupOut, status_code=201)
async def bootstrap(body: BootstrapIn, request: Request, db: AsyncSession = Depends(get_db),
                    vault: Vault = Depends(get_vault)):
    """Create the first admin on a fresh install. Closed for good once any user exists."""
    async with request.app.state.bootstrap_lock:
        if not await _no_users(db):
            raise ApiError(403, "BOOTSTRAP_CLOSED", "An account already exists. Ask the admin for an invite.")
        user, secret = await _new_user(db, vault, body.username, body.password, "admin")
        await audit(db, user.id, "user.bootstrapped", ip=client_ip(request))
        await db.commit()
    return _setup_out(user, secret)


@router.post("/register", response_model=TotpSetupOut, status_code=201)
async def register(body: RegisterIn, request: Request, db: AsyncSession = Depends(get_db),
                   vault: Vault = Depends(get_vault)):
    now = datetime.now(timezone.utc)
    invite = None
    if body.invite_token:
        invite = (await db.execute(
            select(Invite).where(Invite.token_hash == hash_token(body.invite_token))
        )).scalar_one_or_none()
        if invite is None or invite.used_at is not None or invite.expires_at <= now:
            raise ApiError(400, "INVITE_INVALID", "Invite is invalid, used or expired")
    user, secret = await _new_user(db, vault, body.username, body.password,
                                   invite.role if invite else "trader")
    if invite:
        invite.used_at = now
    await audit(db, user.id, "user.registered", {"invite_id": invite.id if invite else None},
                client_ip(request))
    await db.commit()
    return _setup_out(user, secret)


@router.post("/totp/setup", status_code=204)
async def totp_setup(body: TotpConfirmIn, request: Request, db: AsyncSession = Depends(get_db),
                     vault: Vault = Depends(get_vault)):
    """Confirm the authenticator app by submitting a valid code. Enables login."""
    user = (await db.execute(select(User).where(User.username == body.username))).scalar_one_or_none()
    if user is None or not verify_password(user.password_hash, body.password):
        raise ApiError(401, "INVALID_CREDENTIALS", "Invalid credentials")
    if user.totp_enabled:
        raise ApiError(409, "TOTP_ALREADY_ENABLED", "2FA is already enabled")
    step = verify_totp(vault.decrypt(user.totp_secret_enc), body.code, None)
    if step is None:
        raise ApiError(400, "TOTP_INVALID", "Code does not match")
    user.totp_enabled = True
    user.totp_last_step = step
    await audit(db, user.id, "user.totp_enabled", ip=client_ip(request))
    await db.commit()
    return Response(status_code=204)


@router.post("/login")
async def login(body: LoginIn, request: Request, response: Response,
                db: AsyncSession = Depends(get_db), vault: Vault = Depends(get_vault)):
    settings = request.app.state.settings
    now = datetime.now(timezone.utc)
    ip = client_ip(request)
    user = (await db.execute(select(User).where(User.username == body.username))).scalar_one_or_none()

    if user is None or user.disabled:
        verify_password(_DUMMY_HASH, body.password)
        raise ApiError(401, "INVALID_CREDENTIALS", "Invalid username, password or 2FA code")

    if user.locked_until and user.locked_until > now:
        raise ApiError(423, "ACCOUNT_LOCKED", "Too many failed logins",
                       {"locked_until": user.locked_until.isoformat()})

    password_ok = verify_password(user.password_hash, body.password)
    step = None
    if password_ok and user.totp_enabled:
        step = verify_totp(vault.decrypt(user.totp_secret_enc), body.totp, user.totp_last_step)

    if step is None:
        user.failed_logins += 1
        details = {"reason": "password" if not password_ok else
                   ("totp_not_enabled" if not user.totp_enabled else "totp")}
        if user.failed_logins >= settings.max_failed_logins:
            user.locked_until = now + timedelta(minutes=settings.lockout_minutes)
            user.failed_logins = 0
            details["locked"] = True
        await audit(db, user.id, "auth.login_failed", details, ip)
        await db.commit()
        if password_ok and not user.totp_enabled:
            # The password is right and 2FA was never confirmed: hand the key out again so
            # someone who closed the setup screen can still scan it.
            secret = vault.decrypt(user.totp_secret_enc)
            raise ApiError(403, "TOTP_SETUP_REQUIRED", "Finish 2FA setup before logging in",
                           _setup_out(user, secret).model_dump())
        raise ApiError(401, "INVALID_CREDENTIALS", "Invalid username, password or 2FA code")

    user.failed_logins = 0
    user.locked_until = None
    user.totp_last_step = step
    token = new_token()
    ttl = (timedelta(days=settings.cli_token_ttl_days) if body.client == "cli"
           else timedelta(hours=settings.session_ttl_hours))
    db.add(Session(user_id=user.id, token_hash=hash_token(token), kind=body.client,
                   expires_at=now + ttl))
    await audit(db, user.id, "auth.login", {"client": body.client}, ip)
    await db.commit()

    if body.client == "web":
        response.set_cookie(SESSION_COOKIE, token, httponly=True, secure=settings.cookie_secure,
                            samesite="strict", max_age=int(ttl.total_seconds()))
        return {"user": user_out(user)}
    return {"user": user_out(user), "token": token, "expires_at": (now + ttl).isoformat()}


@router.post("/logout", status_code=204)
async def logout(request: Request, db: AsyncSession = Depends(get_db),
                 user: User = Depends(current_user)):
    token = extract_token(request.cookies, request.headers)
    session = (await db.execute(
        select(Session).where(Session.token_hash == hash_token(token or ""))
    )).scalar_one_or_none()
    if session:
        session.revoked = True
    await audit(db, user.id, "auth.logout", ip=client_ip(request))
    await db.commit()
    resp = Response(status_code=204)
    resp.delete_cookie(SESSION_COOKIE)
    return resp


@me_router.get("/me", response_model=UserOut)
async def me(user: User = Depends(current_user)):
    return user_out(user)


class SettingsPatch(BaseModel):
    settings: dict


@me_router.patch("/me/settings", response_model=UserOut)
async def patch_settings(body: SettingsPatch, db: AsyncSession = Depends(get_db),
                         user: User = Depends(current_user)):
    """UI layout, chart settings etc. Shallow-merged. Never holds risk settings."""
    db_user = await db.get(User, user.id)
    db_user.settings = {**(db_user.settings or {}), **body.settings}
    await db.commit()
    return user_out(db_user)

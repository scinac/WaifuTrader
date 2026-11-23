import pyotp
import pytest
from fastapi.testclient import TestClient

from tt.config import Settings
from tt.core import security
from tt.core.security import generate_master_key
from tt.main import create_app


class FakeClock:
    """Controls the clock used for TOTP checks so tests can step through 30 s windows."""

    def __init__(self, start: float = 1_800_000_000.0):
        self.now = start

    def time(self) -> float:
        return self.now

    def advance(self, seconds: float = 30) -> None:
        self.now += seconds


@pytest.fixture
def clock(monkeypatch):
    c = FakeClock()
    monkeypatch.setattr(security, "time", c)
    return c


@pytest.fixture
def settings(tmp_path):
    return Settings(database_url=f"sqlite+aiosqlite:///{tmp_path}/test.db", auto_create_schema=True,
                    master_key=generate_master_key(), marketdata_enabled=False, cookie_secure=False)


@pytest.fixture
def client(settings):
    with TestClient(create_app(settings)) as c:
        yield c


def code_for(secret: str, clock: FakeClock) -> str:
    return pyotp.TOTP(secret).at(int(clock.time()))


def bootstrap_admin(client: TestClient, clock: FakeClock, username="admin",
                    password="correct horse battery") -> dict:
    """Create the first admin directly (like `tt.cli create-admin`) and enable 2FA."""
    import asyncio

    from tt.core.security import hash_password, new_totp_secret
    from tt.db.models import User

    secret = new_totp_secret()

    async def create():
        async with client.app.state.db.sessionmaker() as s:
            s.add(User(username=username, password_hash=hash_password(password), role="admin",
                       totp_secret_enc=client.app.state.vault.encrypt(secret)))
            await s.commit()

    client.portal.call(create) if hasattr(client, "portal") and client.portal else asyncio.run(create())
    r = client.post("/api/v1/auth/totp/setup",
                    json={"username": username, "password": password, "code": code_for(secret, clock)})
    assert r.status_code == 204, r.text
    clock.advance()
    return {"username": username, "password": password, "secret": secret}


def login(client: TestClient, clock: FakeClock, creds: dict, client_kind="cli") -> str | None:
    r = client.post("/api/v1/auth/login", json={
        "username": creds["username"], "password": creds["password"],
        "totp": code_for(creds["secret"], clock), "client": client_kind})
    clock.advance()
    assert r.status_code == 200, r.text
    return r.json().get("token")


def register_user(client: TestClient, clock: FakeClock, admin_token: str, username: str,
                  password="another long password") -> dict:
    inv = client.post("/api/v1/admin/invites", json={"role": "trader"},
                      headers={"Authorization": f"Bearer {admin_token}"}).json()["invite_token"]
    r = client.post("/api/v1/auth/register",
                    json={"invite_token": inv, "username": username, "password": password})
    assert r.status_code == 201, r.text
    secret = r.json()["totp_secret"]
    r = client.post("/api/v1/auth/totp/setup", json={
        "username": username, "password": password, "code": code_for(secret, clock)})
    assert r.status_code == 204, r.text
    clock.advance()
    return {"username": username, "password": password, "secret": secret}

from conftest import bootstrap_admin, code_for, login, register_user


def auth(token):
    return {"Authorization": f"Bearer {token}"}


def test_full_flow_invite_register_2fa_login(client, clock):
    admin = bootstrap_admin(client, clock)
    admin_token = login(client, clock, admin)
    bob = register_user(client, clock, admin_token, "bob")
    bob_token = login(client, clock, bob)
    me = client.get("/api/v1/me", headers=auth(bob_token)).json()
    assert me["username"] == "bob" and me["role"] == "trader"


def test_login_without_valid_totp_fails(client, clock):
    admin = bootstrap_admin(client, clock)
    r = client.post("/api/v1/auth/login", json={**admin, "totp": "000000", "client": "cli"})
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "INVALID_CREDENTIALS"


def test_totp_code_cannot_be_replayed(client, clock):
    admin = bootstrap_admin(client, clock)
    code = code_for(admin["secret"], clock)
    body = {"username": admin["username"], "password": admin["password"], "totp": code, "client": "cli"}
    assert client.post("/api/v1/auth/login", json=body).status_code == 200
    assert client.post("/api/v1/auth/login", json=body).status_code == 401


def test_five_failures_lock_account_for_15_minutes(client, clock):
    admin = bootstrap_admin(client, clock)
    bad = {"username": admin["username"], "password": "wrong password!!", "totp": "123456",
           "client": "cli"}
    for _ in range(5):
        assert client.post("/api/v1/auth/login", json=bad).status_code == 401
    good = {"username": admin["username"], "password": admin["password"],
            "totp": code_for(admin["secret"], clock), "client": "cli"}
    r = client.post("/api/v1/auth/login", json=good)
    assert r.status_code == 423
    assert r.json()["error"]["code"] == "ACCOUNT_LOCKED"


def test_user_without_2fa_cannot_login(client, clock):
    admin = bootstrap_admin(client, clock)
    token = login(client, clock, admin)
    inv = client.post("/api/v1/admin/invites", json={}, headers=auth(token)).json()["invite_token"]
    client.post("/api/v1/auth/register",
                json={"invite_token": inv, "username": "carol", "password": "a long password 1"})
    r = client.post("/api/v1/auth/login", json={"username": "carol", "password": "a long password 1",
                                               "totp": "123456", "client": "cli"})
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "TOTP_SETUP_REQUIRED"


def test_invite_single_use_and_weak_password(client, clock):
    admin = bootstrap_admin(client, clock)
    token = login(client, clock, admin)
    inv = client.post("/api/v1/admin/invites", json={}, headers=auth(token)).json()["invite_token"]
    r = client.post("/api/v1/auth/register", json={"invite_token": inv, "username": "dave",
                                                  "password": "short"})
    assert r.json()["error"]["code"] == "WEAK_PASSWORD"
    ok = client.post("/api/v1/auth/register", json={"invite_token": inv, "username": "dave",
                                                   "password": "long enough password"})
    assert ok.status_code == 201
    again = client.post("/api/v1/auth/register", json={"invite_token": inv, "username": "eve",
                                                      "password": "long enough password"})
    assert again.json()["error"]["code"] == "INVITE_INVALID"


def test_trader_cannot_use_admin_endpoints(client, clock):
    admin = bootstrap_admin(client, clock)
    bob = register_user(client, clock, login(client, clock, admin), "bob")
    bob_token = login(client, clock, bob)
    assert client.post("/api/v1/admin/invites", json={}, headers=auth(bob_token)).status_code == 403
    assert client.get("/api/v1/admin/users", headers=auth(bob_token)).status_code == 403


def test_logout_revokes_token(client, clock):
    admin = bootstrap_admin(client, clock)
    token = login(client, clock, admin)
    assert client.post("/api/v1/auth/logout", headers=auth(token)).status_code == 204
    assert client.get("/api/v1/me", headers=auth(token)).status_code == 401


def test_web_login_sets_httponly_cookie(client, clock):
    admin = bootstrap_admin(client, clock)
    r = client.post("/api/v1/auth/login", json={**{k: admin[k] for k in ("username", "password")},
                                               "totp": code_for(admin["secret"], clock),
                                               "client": "web"})
    assert r.status_code == 200
    assert "token" not in r.json()
    cookie = r.headers["set-cookie"].lower()
    assert "tt_session=" in cookie and "httponly" in cookie and "samesite=strict" in cookie
    assert client.get("/api/v1/me").status_code == 200  # cookie sent automatically


def test_settings_are_per_user(client, clock):
    admin = bootstrap_admin(client, clock)
    admin_token = login(client, clock, admin)
    bob = register_user(client, clock, admin_token, "bob")
    bob_token = login(client, clock, bob)
    client.patch("/api/v1/me/settings", json={"settings": {"layout": "A"}}, headers=auth(admin_token))
    assert client.get("/api/v1/me", headers=auth(bob_token)).json()["settings"] == {}


def test_secrets_encrypted_at_rest(client, clock):
    import sqlite3

    admin = bootstrap_admin(client, clock)
    path = client.app.state.settings.database_url.split("///")[1]
    row = sqlite3.connect(path).execute("select totp_secret_enc from users").fetchone()
    assert admin["secret"] not in row[0]

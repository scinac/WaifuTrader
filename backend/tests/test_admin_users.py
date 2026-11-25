from conftest import bootstrap_admin, code_for, login, register_user


def auth(token, totp=None):
    headers = {"Authorization": f"Bearer {token}"}
    if totp:
        headers["X-TOTP"] = totp
    return headers


def test_admin_promotes_and_demotes_a_user_with_2fa(client, clock):
    admin = bootstrap_admin(client, clock)
    admin_token = login(client, clock, admin)
    register_user(client, clock, admin_token, "bob")
    bob_id = next(u["id"] for u in client.get("/api/v1/admin/users", headers=auth(admin_token)).json()
                  if u["username"] == "bob")

    r = client.patch(f"/api/v1/admin/users/{bob_id}", json={"role": "admin"}, headers=auth(admin_token))
    assert r.status_code == 401 and r.json()["error"]["code"] == "TOTP_REQUIRED"

    r = client.patch(f"/api/v1/admin/users/{bob_id}", json={"role": "admin"},
                     headers=auth(admin_token, code_for(admin["secret"], clock)))
    assert r.status_code == 200 and r.json()["role"] == "admin"
    clock.advance()

    r = client.patch(f"/api/v1/admin/users/{bob_id}", json={"role": "trader"},
                     headers=auth(admin_token, code_for(admin["secret"], clock)))
    assert r.json()["role"] == "trader"


def test_trader_cannot_change_roles_and_admin_cannot_change_own(client, clock):
    admin = bootstrap_admin(client, clock)
    admin_token = login(client, clock, admin)
    bob = register_user(client, clock, admin_token, "bob")
    bob_token = login(client, clock, bob)
    users = {u["username"]: u["id"] for u in client.get("/api/v1/admin/users", headers=auth(admin_token)).json()}

    r = client.patch(f"/api/v1/admin/users/{users['bob']}", json={"role": "admin"},
                     headers=auth(bob_token, code_for(bob["secret"], clock)))
    assert r.status_code == 403

    r = client.patch(f"/api/v1/admin/users/{users['admin']}", json={"role": "trader"},
                     headers=auth(admin_token, code_for(admin["secret"], clock)))
    assert r.status_code == 409 and r.json()["error"]["code"] == "OWN_ROLE"

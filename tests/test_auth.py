import jwt
import pytest
from fastapi import Depends
from fastapi.testclient import TestClient

from src.auth import (create_token, hash_password, require_roles, require_super_admin,
                      verify_password)
from src.config import settings
from src.models import Role, User

from .conftest import PASSWORD


def test_a_password_verifies_against_its_own_hash_only():
    hashed = hash_password("hunter2-hunter2")
    assert verify_password("hunter2-hunter2", hashed)
    assert not verify_password("hunter2-hunter3", hashed)
    assert hash_password("hunter2-hunter2") != hashed


def test_a_token_names_the_account_it_was_minted_for(world):
    payload = jwt.decode(create_token(world.user_a1), settings.JWT_SECRET,
                         algorithms=[settings.JWT_ALGORITHM])
    assert payload["sub"] == str(world.user_a1.id)
    assert payload["role"] == "user"


def test_a_token_signed_with_another_secret_is_refused(world, client):
    forged = jwt.encode({"sub": str(world.root.id), "role": "super_admin"},
                        "some-other-secret", algorithm=settings.JWT_ALGORITHM)
    assert client.get("/auth/me", headers={"Authorization": f"Bearer {forged}"}).status_code == 401


def test_an_expired_token_is_refused(world, client, monkeypatch):
    monkeypatch.setattr(settings, "JWT_EXPIRE_MINUTES", -1)
    stale = create_token(world.user_a1)
    assert client.get("/auth/me", headers={"Authorization": f"Bearer {stale}"}).status_code == 401


def test_garbage_in_the_header_is_refused(client):
    assert client.get("/auth/me", headers={"Authorization": "Bearer not.a.token"}).status_code == 401


def test_no_header_at_all_is_401_not_403(client):
    assert client.get("/auth/me").status_code == 401


def test_a_token_for_a_deleted_account_stops_working(world, db, client, as_user):
    ghost = as_user(world.user_a1)
    assert ghost("GET", "/auth/me").status_code == 200
    db.user.delete(where={"id": world.user_a1.id})
    assert ghost("GET", "/auth/me").status_code == 401


def test_the_role_is_read_from_the_database_not_from_the_token(world, db, as_user):
    was_super = as_user(world.root)
    assert was_super("POST", "/orgs", json={"name": "org-c"}).status_code == 201

    db.user.update(where={"id": world.root.id}, data={"role": Role.user})
    assert was_super("POST", "/orgs", json={"name": "org-d"}).status_code == 403


def test_blocking_kills_a_token_that_was_already_issued(world, db, as_user):
    blocked = as_user(world.user_a1)
    assert blocked("GET", "/auth/me").status_code == 200

    db.user.update(where={"id": world.user_a1.id}, data={"is_active": False})
    response = blocked("GET", "/auth/me")
    assert response.status_code == 403
    assert response.json()["detail"] == "Account is blocked"


def test_login_returns_a_usable_token(world, client):
    response = client.post("/auth/login", json={"username": "user-a1", "password": PASSWORD})
    assert response.status_code == 200
    body = response.json()
    assert body["role"] == "user"
    assert client.get("/auth/me",
                      headers={"Authorization": f"Bearer {body['access_token']}"}).status_code == 200


def test_a_wrong_password_is_401(world, client):
    assert client.post("/auth/login",
                       json={"username": "user-a1", "password": "wrong-wrong"}).status_code == 401


def test_an_unknown_username_is_401_and_not_404(world, client):
    assert client.post("/auth/login",
                       json={"username": "nobody", "password": PASSWORD}).status_code == 401


def test_a_blocked_account_is_told_apart_from_a_wrong_password(world, db, client):
    db.user.update(where={"id": world.user_a1.id}, data={"is_active": False})
    response = client.post("/auth/login", json={"username": "user-a1", "password": PASSWORD})
    assert response.status_code == 403


def test_a_blocked_org_admin_does_not_block_its_organization(world, db, client):
    db.user.update(where={"id": world.admin_a.id}, data={"is_active": False})
    assert client.post("/auth/login",
                       json={"username": "admin-a", "password": PASSWORD}).status_code == 403
    assert client.post("/auth/login",
                       json={"username": "user-a1", "password": PASSWORD}).status_code == 200


def test_an_account_changes_its_own_password(world, as_user, client):
    response = as_user(world.user_a1)("POST", "/auth/password",
                                      json={"current_password": PASSWORD,
                                            "new_password": "Brand-new-one"})
    assert response.status_code == 200
    assert client.post("/auth/login", json={"username": "user-a1",
                                            "password": "Brand-new-one"}).status_code == 200
    assert client.post("/auth/login", json={"username": "user-a1",
                                            "password": PASSWORD}).status_code == 401


def test_a_super_admin_can_change_its_own_password(world, as_user, client):
    root = as_user(world.root)
    assert root("POST", f"/accounts/{world.root.id}/password",
                json={"password": "Brand-new-one"}).status_code == 403
    assert root("POST", "/auth/password",
                json={"current_password": PASSWORD,
                      "new_password": "Brand-new-one"}).status_code == 200
    assert client.post("/auth/login", json={"username": "root",
                                            "password": "Brand-new-one"}).status_code == 200


def test_the_current_password_is_required_and_checked(world, as_user, client):
    caller = as_user(world.admin_a)
    assert caller("POST", "/auth/password",
                  json={"current_password": "not-the-one",
                        "new_password": "Brand-new-one"}).status_code == 401
    assert caller("POST", "/auth/password",
                  json={"new_password": "Brand-new-one"}).status_code == 422
    assert client.post("/auth/login", json={"username": "admin-a",
                                            "password": PASSWORD}).status_code == 200


def test_a_short_new_password_is_refused(world, as_user):
    assert as_user(world.user_a1)("POST", "/auth/password",
                                  json={"current_password": PASSWORD,
                                        "new_password": "short"}).status_code == 422


def test_changing_a_password_needs_a_token(client):
    assert client.post("/auth/password", json={"current_password": "x",
                                               "new_password": "Brand-new-one"}).status_code == 401


def test_me_names_the_organization_the_caller_sits_in(world, as_user):
    body = as_user(world.user_a1)("GET", "/auth/me").json()
    assert body["organization_id"] == world.org_a.id
    assert body["organization"]["name"] == "org-a"

    admin = as_user(world.admin_a)("GET", "/auth/me").json()
    assert admin["organization"]["name"] == "org-a"

    root = as_user(world.root)("GET", "/auth/me").json()
    assert root["organization_id"] is None and root["organization"] is None


@pytest.mark.parametrize("account, allowed", [("root", True), ("admin_a", True),
                                              ("user_a1", False), ("user_a2", False)])
def test_require_roles_gates_on_the_listed_roles(world, app, account, allowed):
    gate = require_roles(Role.super_admin, Role.org_admin)

    @app.get("/gated")
    def gated(actor: User = Depends(gate)):
        return {"who": actor.username}

    caller = getattr(world, account)
    response = TestClient(app).get(
        "/gated", headers={"Authorization": f"Bearer {create_token(caller)}"})
    assert response.status_code == (200 if allowed else 403)


def test_require_super_admin_is_the_moderation_gate(world, app):
    @app.get("/moderated", dependencies=[Depends(require_super_admin)])
    def moderated():
        return {"ok": True}

    client = TestClient(app)
    for account, expected in [(world.root, 200), (world.admin_a, 403),
                              (world.user_a1, 403)]:
        response = client.get("/moderated",
                              headers={"Authorization": f"Bearer {create_token(account)}"})
        assert response.status_code == expected, account.username

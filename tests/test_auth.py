"""`src/auth.py`: what a token proves, and what it deliberately does not.

The rule the whole file exists for: a token is an identity claim, not a permission
claim. Role, scope and the blocked flag are re-read from the database on every request,
so a token minted before a change carries none of the rights it had when it was signed.
"""

import jwt
import pytest
from fastapi import Depends
from fastapi.testclient import TestClient

from src.auth import (create_token, hash_password, require_roles, require_super_admin,
                      verify_password)
from src.config import settings
from src.models import Role, User

from .conftest import PASSWORD


# ---------- passwords ----------

def test_a_password_verifies_against_its_own_hash_only():
    hashed = hash_password("hunter2-hunter2")
    assert verify_password("hunter2-hunter2", hashed)
    assert not verify_password("hunter2-hunter3", hashed)
    # bcrypt salts, so the same password twice is two different hashes
    assert hash_password("hunter2-hunter2") != hashed


# ---------- tokens ----------

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


# ---------- the token is not the authority ----------

def test_the_role_is_read_from_the_database_not_from_the_token(world, db, as_user):
    """A super_admin's token keeps saying `super_admin` after the account is demoted;
    the request must not."""
    was_super = as_user(world.root)
    assert was_super("POST", "/orgs", json={"name": "org-c"}).status_code == 201

    # Written through the client, not by assigning to the object: a Prisma model is a
    # plain value and changing it changes nothing in the database. `ck_users_scope` still
    # applies — a super_admin has both scope columns NULL, which `user` also allows.
    db.user.update(where={"id": world.root.id}, data={"role": Role.user})
    assert was_super("POST", "/orgs", json={"name": "org-d"}).status_code == 403


def test_blocking_kills_a_token_that_was_already_issued(world, db, as_user):
    """Checked on every request, not only at login — otherwise a blocked account keeps
    working until its token happens to expire, up to a day later."""
    blocked = as_user(world.user_a1)
    assert blocked("GET", "/auth/me").status_code == 200

    db.user.update(where={"id": world.user_a1.id}, data={"is_active": False})
    response = blocked("GET", "/auth/me")
    assert response.status_code == 403
    assert response.json()["detail"] == "Account is blocked"


# ---------- login ----------

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
    """Answering 404 would tell an attacker which usernames exist."""
    assert client.post("/auth/login",
                       json={"username": "nobody", "password": PASSWORD}).status_code == 401


def test_a_blocked_account_is_told_apart_from_a_wrong_password(world, db, client):
    """403, not 401: the password *was* right, and the person needs to know to ask their
    admin rather than keep retrying it."""
    db.user.update(where={"id": world.user_a1.id}, data={"is_active": False})
    response = client.post("/auth/login", json={"username": "user-a1", "password": PASSWORD})
    assert response.status_code == 403


def test_a_blocked_unit_admin_does_not_block_its_unit(world, db, client):
    """Blocking is not inherited."""
    db.user.update(where={"id": world.admin_a1.id}, data={"is_active": False})
    assert client.post("/auth/login",
                       json={"username": "admin-a1", "password": PASSWORD}).status_code == 403
    assert client.post("/auth/login",
                       json={"username": "user-a1", "password": PASSWORD}).status_code == 200


# ---------- POST /auth/password: the caller's own ----------

def test_an_account_changes_its_own_password(world, as_user, client):
    response = as_user(world.user_a1)("POST", "/auth/password",
                                      json={"current_password": PASSWORD,
                                            "new_password": "brand-new-one"})
    assert response.status_code == 200
    assert client.post("/auth/login", json={"username": "user-a1",
                                            "password": "brand-new-one"}).status_code == 200
    assert client.post("/auth/login", json={"username": "user-a1",
                                            "password": PASSWORD}).status_code == 401


def test_a_super_admin_can_change_its_own_password(world, as_user, client):
    """The case the endpoint exists for: `/accounts/{id}/password` refuses the caller's
    own row, and a super_admin has nobody above them to do it for them."""
    root = as_user(world.root)
    assert root("POST", f"/accounts/{world.root.id}/password",
                json={"password": "brand-new-one"}).status_code == 403
    assert root("POST", "/auth/password",
                json={"current_password": PASSWORD,
                      "new_password": "brand-new-one"}).status_code == 200
    assert client.post("/auth/login", json={"username": "root",
                                            "password": "brand-new-one"}).status_code == 200


def test_the_current_password_is_required_and_checked(world, as_user, client):
    """The token alone is not enough — otherwise a session left open on a shared machine
    would be a permanent takeover rather than an hour of borrowed access."""
    caller = as_user(world.admin_a1)
    assert caller("POST", "/auth/password",
                  json={"current_password": "not-the-one",
                        "new_password": "brand-new-one"}).status_code == 401
    assert caller("POST", "/auth/password",
                  json={"new_password": "brand-new-one"}).status_code == 422
    # and nothing was changed by either attempt
    assert client.post("/auth/login", json={"username": "admin-a1",
                                            "password": PASSWORD}).status_code == 200


def test_a_short_new_password_is_refused(world, as_user):
    assert as_user(world.user_a1)("POST", "/auth/password",
                                  json={"current_password": PASSWORD,
                                        "new_password": "short"}).status_code == 422


def test_changing_a_password_needs_a_token(client):
    assert client.post("/auth/password", json={"current_password": "x",
                                               "new_password": "brand-new-one"}).status_code == 401


def test_me_resolves_the_organization_through_the_unit(world, as_user):
    """`users.organization_id` is NULL for everyone below an org_admin — their
    organization is reached through their unit, so it can never drift out of step."""
    body = as_user(world.user_a1)("GET", "/auth/me").json()
    assert body["organization_id"] is None
    assert body["organization"]["name"] == "org-a"
    assert body["unit"]["name"] == "unit-a1"

    admin = as_user(world.admin_a)("GET", "/auth/me").json()
    assert admin["organization"]["name"] == "org-a"
    assert admin["unit"] is None


# ---------- require_roles ----------

@pytest.mark.parametrize("account, allowed", [("root", True), ("admin_a", True),
                                              ("admin_a1", False), ("user_a1", False)])
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
    """Moderation is super-admin-only including for org and unit admins: an approval
    writes into the one corpus every organization searches."""
    @app.get("/moderated", dependencies=[Depends(require_super_admin)])
    def moderated():
        return {"ok": True}

    client = TestClient(app)
    for account, expected in [(world.root, 200), (world.admin_a, 403),
                              (world.admin_a1, 403), (world.user_a1, 403)]:
        response = client.get("/moderated",
                              headers={"Authorization": f"Bearer {create_token(account)}"})
        assert response.status_code == expected, account.username

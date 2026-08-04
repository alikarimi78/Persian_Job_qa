"""`/accounts/*` end to end: the provisioning chain, and every way out of it.

Two checks stand between a caller and an account, and both have to be in place — the
role gate (`require_roles`, "may this kind of caller call this endpoint at all") and the
scope check in the handler ("is this particular record inside their span of control").
An org_admin passing the first and failing the second is the case that keeps coming
back, so most of what follows is that shape.
"""

import pytest

from app.models import Organization, Unit, User

from .conftest import PASSWORD


def _free_unit(db, world, name="unit-a9"):
    """A unit of org_a with no admin sitting in it."""
    unit = Unit(name=name, organization_id=world.org_a.id)
    db.add(unit)
    db.commit()
    return unit


# ---------- POST /accounts/super-admins ----------

@pytest.mark.parametrize("caller, expected", [("root", 201), ("admin_a", 403),
                                              ("admin_a1", 403), ("user_a1", 403)])
def test_only_a_super_admin_makes_another_one(world, as_user, caller, expected):
    response = as_user(getattr(world, caller))(
        "POST", "/accounts/super-admins",
        json={"username": "root-2", "password": "password12"})
    assert response.status_code == expected


def test_a_new_super_admin_belongs_to_no_organization(world, as_user):
    body = as_user(world.root)("POST", "/accounts/super-admins",
                               json={"username": "root-2",
                                     "password": "password12"}).json()
    assert body["organization_id"] is None and body["unit_id"] is None


# ---------- POST /accounts/org-admins ----------

def test_an_organizations_admin_is_appointed_by_a_super_admin(world, as_user, db):
    fresh = Organization(name="org-c")
    db.add(fresh)
    db.commit()

    response = as_user(world.root)("POST", "/accounts/org-admins",
                                   json={"username": "admin-c", "password": "password12",
                                         "organization_id": fresh.id})
    assert response.status_code == 201
    assert response.json()["organization_id"] == fresh.id


def test_an_org_admin_cannot_appoint_a_peer(world, as_user):
    assert as_user(world.admin_a)("POST", "/accounts/org-admins",
                                  json={"username": "admin-c", "password": "password12",
                                        "organization_id": world.org_b.id}).status_code == 403


def test_a_second_admin_for_one_organization_is_409(world, as_user):
    response = as_user(world.root)("POST", "/accounts/org-admins",
                                   json={"username": "admin-a-2", "password": "password12",
                                         "organization_id": world.org_a.id})
    assert response.status_code == 409
    assert "admin-a" in response.json()["detail"]


def test_an_organization_that_does_not_exist_is_404(world, as_user):
    assert as_user(world.root)("POST", "/accounts/org-admins",
                               json={"username": "admin-x", "password": "password12",
                                     "organization_id": 9999}).status_code == 404


# ---------- POST /accounts/unit-admins ----------

def test_an_org_admin_staffs_the_units_of_its_own_organization(world, db, as_user):
    unit = _free_unit(db, world)
    response = as_user(world.admin_a)("POST", "/accounts/unit-admins",
                                      json={"username": "admin-a9", "password": "password12",
                                            "unit_id": unit.id})
    assert response.status_code == 201
    assert response.json()["unit_id"] == unit.id


def test_an_org_admin_may_not_staff_another_organizations_unit(world, db, as_user):
    """Passes the role gate and is stopped by the scope check on the target unit."""
    db.delete(world.admin_b1)
    db.commit()
    assert as_user(world.admin_a)("POST", "/accounts/unit-admins",
                                  json={"username": "admin-b9", "password": "password12",
                                        "unit_id": world.unit_b1.id}).status_code == 403


@pytest.mark.parametrize("caller", ["admin_a1", "user_a1"])
def test_nobody_below_an_org_admin_appoints_a_unit_admin(world, db, as_user, caller):
    unit = _free_unit(db, world)
    assert as_user(getattr(world, caller))(
        "POST", "/accounts/unit-admins",
        json={"username": "admin-a9", "password": "password12",
              "unit_id": unit.id}).status_code == 403


# ---------- POST /accounts/users ----------

def test_a_unit_admin_staffs_its_own_unit_without_naming_it(world, as_user):
    response = as_user(world.admin_a1)("POST", "/accounts/users",
                                       json={"username": "user-a1c",
                                             "password": "password12"})
    assert response.status_code == 201
    assert response.json()["unit_id"] == world.unit_a1.id


def test_a_unit_admin_naming_another_unit_is_refused(world, as_user):
    assert as_user(world.admin_a1)("POST", "/accounts/users",
                                   json={"username": "user-a2c", "password": "password12",
                                         "unit_id": world.unit_a2.id}).status_code == 403


def test_an_org_admin_deliberately_cannot_create_users(world, as_user):
    """It creates the units and their admins; those admins staff their own unit."""
    assert as_user(world.admin_a)("POST", "/accounts/users",
                                  json={"username": "user-a1c", "password": "password12",
                                        "unit_id": world.unit_a1.id}).status_code == 403


def test_a_super_admin_must_say_which_unit(world, as_user):
    """It has no scope of its own to default to."""
    assert as_user(world.root)("POST", "/accounts/users",
                               json={"username": "user-x",
                                     "password": "password12"}).status_code == 422
    assert as_user(world.root)("POST", "/accounts/users",
                               json={"username": "user-x", "password": "password12",
                                     "unit_id": world.unit_b1.id}).status_code == 201


def test_a_user_provisions_nobody(world, as_user):
    assert as_user(world.user_a1)("POST", "/accounts/users",
                                  json={"username": "user-a1c",
                                        "password": "password12"}).status_code == 403


def test_a_created_account_can_log_in_with_the_password_it_was_given(world, as_user, client):
    as_user(world.admin_a1)("POST", "/accounts/users",
                            json={"username": "user-a1c", "password": "password12"})
    assert client.post("/auth/login", json={"username": "user-a1c",
                                            "password": "password12"}).status_code == 200


# ---------- GET /accounts ----------

def test_the_listing_is_scoped_to_the_caller(world, as_user):
    everyone = {u["username"] for u in as_user(world.root)("GET", "/accounts").json()}
    assert "user-b1" in everyone and "admin-b" in everyone

    org = {u["username"] for u in as_user(world.admin_a)("GET", "/accounts").json()}
    assert org == {"admin-a1", "admin-a2", "user-a1", "user-a1b", "user-a2"}

    unit = {u["username"] for u in as_user(world.admin_a1)("GET", "/accounts").json()}
    assert unit == {"admin-a1", "user-a1", "user-a1b"}


def test_an_ordinary_user_may_not_list_accounts(world, as_user):
    assert as_user(world.user_a1)("GET", "/accounts").status_code == 403


def test_the_filters_narrow_within_the_scope_and_never_widen_it(world, as_user):
    admin_a = as_user(world.admin_a)
    users = admin_a("GET", "/accounts?role=user").json()
    assert {u["username"] for u in users} == {"user-a1", "user-a1b", "user-a2"}

    in_unit = admin_a("GET", f"/accounts?unit_id={world.unit_a1.id}").json()
    assert {u["username"] for u in in_unit} == {"admin-a1", "user-a1", "user-a1b"}

    # asking for the other organization returns nothing rather than its roster
    assert admin_a("GET", f"/accounts?organization_id={world.org_b.id}").json() == []


def test_filtering_by_organization_finds_its_admin_too(world, as_user):
    """An org_admin is matched by its own column, everyone else through their unit."""
    seen = {u["username"] for u in
            as_user(world.root)("GET", f"/accounts?organization_id={world.org_a.id}").json()}
    assert "admin-a" in seen and "user-a1" in seen and "user-b1" not in seen


# ---------- block / unblock / password ----------

def test_a_unit_admin_blocks_its_own_users_only(world, as_user):
    admin_a1 = as_user(world.admin_a1)
    assert admin_a1("POST", f"/accounts/{world.user_a1.id}/block").json()["is_active"] is False
    assert admin_a1("POST", f"/accounts/{world.user_a2.id}/block").status_code == 403
    assert admin_a1("POST", f"/accounts/{world.admin_a2.id}/block").status_code == 403


def test_an_org_admin_blocks_a_unit_admin_of_its_organization(world, as_user):
    assert as_user(world.admin_a)(
        "POST", f"/accounts/{world.admin_a1.id}/block").status_code == 200
    assert as_user(world.admin_a)(
        "POST", f"/accounts/{world.admin_b1.id}/block").status_code == 403


@pytest.mark.parametrize("caller", ["root", "admin_a", "admin_a1"])
def test_no_admin_may_block_itself(world, as_user, caller):
    actor = getattr(world, caller)
    assert as_user(actor)("POST", f"/accounts/{actor.id}/block").status_code == 403


def test_unblocking_restores_login(world, as_user, client):
    admin_a1 = as_user(world.admin_a1)
    admin_a1("POST", f"/accounts/{world.user_a1.id}/block")
    assert client.post("/auth/login",
                       json={"username": "user-a1", "password": PASSWORD}).status_code == 403
    admin_a1("POST", f"/accounts/{world.user_a1.id}/unblock")
    assert client.post("/auth/login",
                       json={"username": "user-a1", "password": PASSWORD}).status_code == 200


def test_an_admin_sets_a_password_without_being_asked_for_the_old_one(world, as_user, client):
    assert as_user(world.admin_a1)("POST", f"/accounts/{world.user_a1.id}/password",
                                   json={"password": "brand-new-one"}).status_code == 200
    assert client.post("/auth/login", json={"username": "user-a1",
                                            "password": "brand-new-one"}).status_code == 200
    assert client.post("/auth/login", json={"username": "user-a1",
                                            "password": PASSWORD}).status_code == 401


def test_resetting_the_password_of_someone_elses_user_is_refused(world, as_user):
    assert as_user(world.admin_b1)("POST", f"/accounts/{world.user_a1.id}/password",
                                   json={"password": "brand-new-one"}).status_code == 403


def test_an_account_that_does_not_exist_is_404(world, as_user):
    assert as_user(world.root)("POST", "/accounts/9999/block").status_code == 404


# ---------- move ----------

def test_a_unit_admin_does_not_move_people(world, as_user):
    """They run one unit; a move is a decision about two of them."""
    assert as_user(world.admin_a1)("POST", f"/accounts/{world.user_a1.id}/unit",
                                   json={"unit_id": world.unit_a2.id}).status_code == 403


def test_an_org_admin_moves_within_its_organization(world, as_user):
    response = as_user(world.admin_a)("POST", f"/accounts/{world.user_a1.id}/unit",
                                      json={"unit_id": world.unit_a2.id})
    assert response.status_code == 200
    assert response.json()["unit_id"] == world.unit_a2.id
    assert response.json()["role"] == "user"


def test_an_org_admin_may_not_move_anyone_out_of_it(world, as_user):
    """The destination is checked as well as the account — that pair is what keeps the
    move inside one organization."""
    assert as_user(world.admin_a)("POST", f"/accounts/{world.user_a1.id}/unit",
                                  json={"unit_id": world.unit_b1.id}).status_code == 403


def test_an_org_admin_may_not_move_anyone_into_it_either(world, as_user):
    assert as_user(world.admin_a)("POST", f"/accounts/{world.user_b1.id}/unit",
                                  json={"unit_id": world.unit_a1.id}).status_code == 403


def test_moving_an_account_that_lives_in_no_unit_is_409(world, as_user):
    assert as_user(world.root)("POST", f"/accounts/{world.admin_a.id}/unit",
                               json={"unit_id": world.unit_a1.id}).status_code == 409


# ---------- delete ----------

def test_a_unit_admin_deletes_its_own_users_only(world, db, as_user):
    admin_a1 = as_user(world.admin_a1)
    assert admin_a1("DELETE", f"/accounts/{world.user_a1.id}").status_code == 204
    assert db.query(User).filter(User.username == "user-a1").first() is None
    assert admin_a1("DELETE", f"/accounts/{world.user_a2.id}").status_code == 403


def test_nobody_deletes_themselves(world, as_user):
    """Which is also why no sequence of these actions can leave the system without a
    super_admin: they all go downwards, and the caller is always still standing."""
    assert as_user(world.root)("DELETE", f"/accounts/{world.root.id}").status_code == 403


def test_a_deleted_accounts_token_is_worthless(world, as_user):
    victim = as_user(world.user_a1)
    as_user(world.admin_a1)("DELETE", f"/accounts/{world.user_a1.id}")
    assert victim("GET", "/auth/me").status_code == 401


def test_an_org_admin_deletes_a_unit_admin_and_the_unit_can_be_restaffed(world, as_user):
    admin_a = as_user(world.admin_a)
    assert admin_a("DELETE", f"/accounts/{world.admin_a1.id}").status_code == 204
    assert admin_a("POST", "/accounts/unit-admins",
                   json={"username": "admin-a1-new", "password": "password12",
                         "unit_id": world.unit_a1.id}).status_code == 201


def test_the_role_gate_and_the_scope_check_are_both_needed(world, as_user):
    """An ordinary user is stopped by the role gate; an admin of the wrong organization
    is stopped by the scope check. Dropping either one opens the endpoint."""
    assert as_user(world.user_a1)(
        "DELETE", f"/accounts/{world.user_a1b.id}").status_code == 403
    assert as_user(world.admin_b)(
        "DELETE", f"/accounts/{world.user_a1b.id}").status_code == 403

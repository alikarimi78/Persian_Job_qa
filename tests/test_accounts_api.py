"""`/accounts/*` end to end: the provisioning chain, and every way out of it.

Two checks stand between a caller and an account, and both have to be in place — the
role gate (`require_roles`, "may this kind of caller call this endpoint at all") and the
scope check in the handler ("is this particular record inside their span of control").
An org_admin passing the first and failing the second is the case that keeps coming
back, so most of what follows is that shape.
"""

import pytest

from src.models import full_name

from .conftest import PASSWORD

# `AccountIn` requires the person's name as well as the credential (migration 0007), so
# every creation payload below carries one. It is spread rather than written out because
# what these tests are about is the permission chain, not the name — the name has its own
# tests at the end of this file.
NAME = {"first_name": "زهرا", "last_name": "کریمی"}


def _free_unit(db, world, name="unit-a9"):
    """A unit of org_a with no admin sitting in it."""
    return db.unit.create(data={"name": name, "organization_id": world.org_a.id})


# ---------- POST /accounts/super-admins ----------

@pytest.mark.parametrize("caller, expected", [("root", 201), ("admin_a", 403),
                                              ("admin_a1", 403), ("user_a1", 403)])
def test_only_a_super_admin_makes_another_one(world, as_user, caller, expected):
    response = as_user(getattr(world, caller))(
        "POST", "/accounts/super-admins",
        json={"username": "root-2", "password": "password12", **NAME})
    assert response.status_code == expected


def test_a_new_super_admin_belongs_to_no_organization(world, as_user):
    body = as_user(world.root)("POST", "/accounts/super-admins",
                               json={"username": "root-2",
                                     "password": "password12", **NAME}).json()
    assert body["organization_id"] is None and body["unit_id"] is None


# ---------- POST /accounts/org-admins ----------

def test_an_organizations_admin_is_appointed_by_a_super_admin(world, as_user, db):
    fresh = db.organization.create(data={"name": "org-c"})

    response = as_user(world.root)("POST", "/accounts/org-admins",
                                   json={"username": "admin-c", "password": "password12", **NAME,
                                         "organization_id": fresh.id})
    assert response.status_code == 201
    assert response.json()["organization_id"] == fresh.id


def test_an_org_admin_cannot_appoint_a_peer(world, as_user):
    assert as_user(world.admin_a)("POST", "/accounts/org-admins",
                                  json={"username": "admin-c", "password": "password12", **NAME,
                                        "organization_id": world.org_b.id}).status_code == 403


def test_a_second_admin_for_one_organization_is_409(world, as_user):
    response = as_user(world.root)("POST", "/accounts/org-admins",
                                   json={"username": "admin-a-2", "password": "password12", **NAME,
                                         "organization_id": world.org_a.id})
    assert response.status_code == 409
    assert "admin-a" in response.json()["detail"]


def test_an_organization_that_does_not_exist_is_404(world, as_user):
    assert as_user(world.root)("POST", "/accounts/org-admins",
                               json={"username": "admin-x", "password": "password12", **NAME,
                                     "organization_id": 9999}).status_code == 404


# ---------- POST /accounts/unit-admins ----------

def test_an_org_admin_staffs_the_units_of_its_own_organization(world, db, as_user):
    unit = _free_unit(db, world)
    response = as_user(world.admin_a)("POST", "/accounts/unit-admins",
                                      json={"username": "admin-a9", "password": "password12", **NAME,
                                            "unit_id": unit.id})
    assert response.status_code == 201
    assert response.json()["unit_id"] == unit.id


def test_an_org_admin_may_not_staff_another_organizations_unit(world, db, as_user):
    """Passes the role gate and is stopped by the scope check on the target unit."""
    db.user.delete(where={"id": world.admin_b1.id})
    assert as_user(world.admin_a)("POST", "/accounts/unit-admins",
                                  json={"username": "admin-b9", "password": "password12", **NAME,
                                        "unit_id": world.unit_b1.id}).status_code == 403


@pytest.mark.parametrize("caller", ["admin_a1", "user_a1"])
def test_nobody_below_an_org_admin_appoints_a_unit_admin(world, db, as_user, caller):
    unit = _free_unit(db, world)
    assert as_user(getattr(world, caller))(
        "POST", "/accounts/unit-admins",
        json={"username": "admin-a9", "password": "password12", **NAME,
              "unit_id": unit.id}).status_code == 403


# ---------- POST /accounts/users ----------

def test_a_unit_admin_staffs_its_own_unit_without_naming_it(world, as_user):
    response = as_user(world.admin_a1)("POST", "/accounts/users",
                                       json={"username": "user-a1c",
                                             "password": "password12", **NAME})
    assert response.status_code == 201
    assert response.json()["unit_id"] == world.unit_a1.id


def test_a_unit_admin_naming_another_unit_is_refused(world, as_user):
    assert as_user(world.admin_a1)("POST", "/accounts/users",
                                   json={"username": "user-a2c", "password": "password12", **NAME,
                                         "unit_id": world.unit_a2.id}).status_code == 403


def test_an_org_admin_deliberately_cannot_create_users(world, as_user):
    """It creates the units and their admins; those admins staff their own unit."""
    assert as_user(world.admin_a)("POST", "/accounts/users",
                                  json={"username": "user-a1c", "password": "password12", **NAME,
                                        "unit_id": world.unit_a1.id}).status_code == 403


def test_a_super_admin_must_say_which_unit(world, as_user):
    """It has no scope of its own to default to."""
    assert as_user(world.root)("POST", "/accounts/users",
                               json={"username": "user-x",
                                     "password": "password12", **NAME}).status_code == 422
    assert as_user(world.root)("POST", "/accounts/users",
                               json={"username": "user-x", "password": "password12", **NAME,
                                     "unit_id": world.unit_b1.id}).status_code == 201


def test_a_user_provisions_nobody(world, as_user):
    assert as_user(world.user_a1)("POST", "/accounts/users",
                                  json={"username": "user-a1c",
                                        "password": "password12", **NAME}).status_code == 403


def test_a_created_account_can_log_in_with_the_password_it_was_given(world, as_user, client):
    as_user(world.admin_a1)("POST", "/accounts/users",
                            json={"username": "user-a1c", "password": "password12", **NAME})
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


# ---------- move an org_admin between organizations ----------

def test_an_organizations_admin_moves_to_another_organization(world, db, as_user):
    """The `/unit` endpoint has nothing to offer an org_admin — it lives in an
    organization, not in a unit — so this is the whole of «ویرایش» for that row."""
    fresh = db.organization.create(data={"name": "org-c"})

    response = as_user(world.root)("POST", f"/accounts/{world.admin_a.id}/organization",
                                   json={"organization_id": fresh.id})
    assert response.status_code == 200
    assert response.json()["organization_id"] == fresh.id
    assert response.json()["role"] == "org_admin"


def test_the_organization_it_leaves_can_be_restaffed(world, db, as_user):
    fresh = db.organization.create(data={"name": "org-c"})

    root = as_user(world.root)
    root("POST", f"/accounts/{world.admin_a.id}/organization",
         json={"organization_id": fresh.id})
    assert root("POST", "/accounts/org-admins",
                json={"username": "admin-a-new", "password": "password12", **NAME,
                      "organization_id": world.org_a.id}).status_code == 201


def test_moving_into_an_organization_that_already_has_an_admin_is_409(world, as_user):
    response = as_user(world.root)("POST", f"/accounts/{world.admin_a.id}/organization",
                                   json={"organization_id": world.org_b.id})
    assert response.status_code == 409
    assert "admin-b" in response.json()["detail"]


def test_submitting_the_organization_it_is_already_in_is_not_a_conflict(world, as_user):
    """Otherwise re-saving an unchanged form would 409 against the row itself."""
    assert as_user(world.root)("POST", f"/accounts/{world.admin_a.id}/organization",
                               json={"organization_id": world.org_a.id}).status_code == 200


def test_moving_an_account_that_lives_in_no_organization_is_409(world, as_user):
    """Everyone below an org_admin belongs to a unit; `/unit` is their move."""
    assert as_user(world.root)("POST", f"/accounts/{world.user_a1.id}/organization",
                               json={"organization_id": world.org_b.id}).status_code == 409


@pytest.mark.parametrize("caller", ["admin_a", "admin_b", "admin_a1", "user_a1"])
def test_only_a_super_admin_moves_an_organizations_admin(world, db, as_user, caller):
    """An org_admin has no second organization to move a peer into, and cannot manage
    one in the first place."""
    fresh = db.organization.create(data={"name": "org-c"})
    assert as_user(getattr(world, caller))(
        "POST", f"/accounts/{world.admin_a.id}/organization",
        json={"organization_id": fresh.id}).status_code == 403


def test_moving_to_an_organization_that_does_not_exist_is_404(world, as_user):
    assert as_user(world.root)("POST", f"/accounts/{world.admin_a.id}/organization",
                               json={"organization_id": 9999}).status_code == 404


# ---------- delete ----------

def test_a_unit_admin_deletes_its_own_users_only(world, db, as_user):
    admin_a1 = as_user(world.admin_a1)
    assert admin_a1("DELETE", f"/accounts/{world.user_a1.id}").status_code == 204
    assert db.user.find_unique(where={"username": "user-a1"}) is None
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
                   json={"username": "admin-a1-new", "password": "password12", **NAME,
                         "unit_id": world.unit_a1.id}).status_code == 201


def test_the_role_gate_and_the_scope_check_are_both_needed(world, as_user):
    """An ordinary user is stopped by the role gate; an admin of the wrong organization
    is stopped by the scope check. Dropping either one opens the endpoint."""
    assert as_user(world.user_a1)(
        "DELETE", f"/accounts/{world.user_a1b.id}").status_code == 403
    assert as_user(world.admin_b)(
        "DELETE", f"/accounts/{world.user_a1b.id}").status_code == 403


# ---------- the person's name ----------
# `username` is the credential and never changes; these two columns are who the account
# *is*, and exist because the PDF report is read outside the system, where a username
# names nobody. Required of every account the API creates, nullable in the database only
# for the rows that predate migration 0007.

def test_a_new_account_carries_the_persons_name(world, as_user):
    response = as_user(world.admin_a1)(
        "POST", "/accounts/users",
        json={"username": "user-a1n", "password": "password12",
              "first_name": "زهرا", "last_name": "کریمی"})

    assert response.status_code == 201
    body = response.json()
    assert (body["first_name"], body["last_name"]) == ("زهرا", "کریمی")
    assert body["full_name"] == "زهرا کریمی"


@pytest.mark.parametrize("missing", ["first_name", "last_name"])
def test_an_account_cannot_be_created_without_a_name(world, as_user, missing):
    body = {"username": "user-a1n", "password": "password12", **NAME}
    del body[missing]
    assert as_user(world.admin_a1)("POST", "/accounts/users", json=body).status_code == 422


def test_a_blank_name_is_refused(world, as_user):
    """« » passes min_length and would print as a blank line in the report masthead."""
    assert as_user(world.admin_a1)(
        "POST", "/accounts/users",
        json={"username": "user-a1n", "password": "password12",
              "first_name": "   ", "last_name": "کریمی"}).status_code == 422


def test_an_admin_fixes_the_name_on_an_account_below_them(world, db, as_user):
    response = as_user(world.admin_a1)("POST", f"/accounts/{world.user_a1.id}/name",
                                       json={"first_name": " علی ", "last_name": "کریمی"})

    assert response.status_code == 200
    stored = world.reload(world.user_a1)
    assert full_name(stored) == "علی کریمی"                # trimmed on the way in
    assert stored.username == "user-a1"                    # the credential is untouched


@pytest.mark.parametrize("caller, expected", [("root", 200), ("admin_a", 200),
                                              ("admin_a1", 200), ("admin_b1", 403),
                                              ("user_a1b", 403)])
def test_renaming_takes_the_same_authority_as_every_other_action(world, as_user,
                                                                 caller, expected):
    response = as_user(getattr(world, caller))(
        "POST", f"/accounts/{world.user_a1.id}/name", json=NAME)
    assert response.status_code == expected


def test_nobody_renames_themselves_here(world, as_user):
    """The same rule as blocking and deletion — and `POST /auth/name` is the way an
    account fixes its own, which is what the top of the hierarchy needs."""
    assert as_user(world.root)("POST", f"/accounts/{world.root.id}/name",
                               json=NAME).status_code == 403


def test_an_account_sets_its_own_name(world, db, as_user):
    """No password asked for, unlike `/auth/password`: a name is not a credential. This
    is how the seeded first super_admin — created from two environment variables that
    carry no name — ever gets one."""
    response = as_user(world.root)("POST", "/auth/name",
                                   json={"first_name": "مهدی", "last_name": "رضایی"})

    assert response.status_code == 200
    assert response.json()["full_name"] == "مهدی رضایی"
    assert full_name(world.reload(world.root)) == "مهدی رضایی"


def test_me_reports_the_name(world, as_user):
    as_user(world.user_a1)("POST", "/auth/name",
                           json={"first_name": "سارا", "last_name": "محمدی"})
    body = as_user(world.user_a1)("GET", "/auth/me").json()
    assert (body["first_name"], body["full_name"]) == ("سارا", "سارا محمدی")


def test_an_account_from_before_the_migration_has_no_name(world, as_user):
    """The world fixture builds its accounts directly, the way rows created before 0007
    look. Nothing breaks: the columns are null and `full_name` is null with them."""
    body = as_user(world.root)("GET", "/accounts").json()
    legacy = next(row for row in body if row["username"] == "user-a1")
    assert legacy["first_name"] is None and legacy["full_name"] is None

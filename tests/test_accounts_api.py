import pytest

from src.models import full_name

from .conftest import PASSWORD

NAME = {"first_name": "زهرا", "last_name": "کریمی"}
NEW_PASSWORD = "Password-12"


@pytest.mark.parametrize("caller, expected", [("root", 201), ("admin_a", 403),
                                              ("user_a1", 403)])
def test_only_a_super_admin_makes_another_one(world, as_user, caller, expected):
    response = as_user(getattr(world, caller))(
        "POST", "/accounts/super-admins",
        json={"username": "root-2", "password": NEW_PASSWORD, **NAME})
    assert response.status_code == expected


def test_a_new_super_admin_belongs_to_no_organization(world, as_user):
    body = as_user(world.root)("POST", "/accounts/super-admins",
                               json={"username": "root-2",
                                     "password": NEW_PASSWORD, **NAME}).json()
    assert body["organization_id"] is None


def test_an_organizations_admin_is_appointed_by_a_super_admin(world, as_user, db):
    fresh = db.organization.create(data={"name": "org-c"})

    response = as_user(world.root)("POST", "/accounts/org-admins",
                                   json={"username": "admin-c", "password": NEW_PASSWORD,
                                         **NAME, "organization_id": fresh.id})
    assert response.status_code == 201
    assert response.json()["organization_id"] == fresh.id


def test_an_org_admin_cannot_appoint_a_peer(world, as_user):
    assert as_user(world.admin_a)("POST", "/accounts/org-admins",
                                  json={"username": "admin-c", "password": NEW_PASSWORD,
                                        **NAME,
                                        "organization_id": world.org_b.id}).status_code == 403


def test_a_second_admin_for_one_organization_is_409(world, as_user):
    response = as_user(world.root)("POST", "/accounts/org-admins",
                                   json={"username": "admin-a-2", "password": NEW_PASSWORD,
                                         **NAME, "organization_id": world.org_a.id})
    assert response.status_code == 409
    assert "admin-a" in response.json()["detail"]


def test_an_organization_that_does_not_exist_is_404(world, as_user):
    assert as_user(world.root)("POST", "/accounts/org-admins",
                               json={"username": "admin-x", "password": NEW_PASSWORD,
                                     **NAME, "organization_id": 9999}).status_code == 404


def test_an_org_admin_staffs_its_own_organization_without_naming_it(world, as_user):
    response = as_user(world.admin_a)("POST", "/accounts/users",
                                      json={"username": "user-a3",
                                            "password": NEW_PASSWORD, **NAME})
    assert response.status_code == 201
    assert response.json()["organization_id"] == world.org_a.id


def test_an_org_admin_naming_another_organization_is_refused(world, as_user):
    assert as_user(world.admin_a)("POST", "/accounts/users",
                                  json={"username": "user-b2", "password": NEW_PASSWORD,
                                        **NAME,
                                        "organization_id": world.org_b.id}).status_code == 403


def test_a_super_admin_must_say_which_organization(world, as_user):
    assert as_user(world.root)("POST", "/accounts/users",
                               json={"username": "user-x",
                                     "password": NEW_PASSWORD, **NAME}).status_code == 422
    assert as_user(world.root)("POST", "/accounts/users",
                               json={"username": "user-x", "password": NEW_PASSWORD,
                                     **NAME,
                                     "organization_id": world.org_b.id}).status_code == 201


def test_a_user_provisions_nobody(world, as_user):
    assert as_user(world.user_a1)("POST", "/accounts/users",
                                  json={"username": "user-a3",
                                        "password": NEW_PASSWORD, **NAME}).status_code == 403


def test_a_created_account_can_log_in_with_the_password_it_was_given(world, as_user, client):
    as_user(world.admin_a)("POST", "/accounts/users",
                           json={"username": "user-a3", "password": NEW_PASSWORD, **NAME})
    assert client.post("/auth/login", json={"username": "user-a3",
                                            "password": NEW_PASSWORD}).status_code == 200


@pytest.mark.parametrize("password", [
    "password-12",
    "PASSWORD-12",
    "Password12",
    "Pa-1",
])
def test_a_weak_password_is_refused_at_creation(world, as_user, password):
    assert as_user(world.admin_a)("POST", "/accounts/users",
                                  json={"username": "user-a3", "password": password,
                                        **NAME}).status_code == 422


def test_a_weak_password_is_refused_when_an_admin_resets_one(world, as_user):
    assert as_user(world.admin_a)("POST", f"/accounts/{world.user_a1.id}/password",
                                  json={"password": "password12"}).status_code == 422


def test_a_weak_password_is_refused_when_an_account_changes_its_own(world, as_user):
    assert as_user(world.user_a1)("POST", "/auth/password",
                                  json={"current_password": PASSWORD,
                                        "new_password": "password12"}).status_code == 422


def test_the_listing_is_scoped_to_the_caller(world, as_user):
    everyone = {u["username"] for u in as_user(world.root)("GET", "/accounts").json()}
    assert "user-b1" in everyone and "admin-b" in everyone

    org = {u["username"] for u in as_user(world.admin_a)("GET", "/accounts").json()}
    assert org == {"user-a1", "user-a2"}


def test_an_ordinary_user_may_not_list_accounts(world, as_user):
    assert as_user(world.user_a1)("GET", "/accounts").status_code == 403


def test_the_filters_narrow_within_the_scope_and_never_widen_it(world, as_user):
    admin_a = as_user(world.admin_a)
    users = admin_a("GET", "/accounts?role=user").json()
    assert {u["username"] for u in users} == {"user-a1", "user-a2"}

    assert admin_a("GET", f"/accounts?organization_id={world.org_b.id}").json() == []


def test_filtering_by_organization_finds_its_admin_too(world, as_user):
    seen = {u["username"] for u in
            as_user(world.root)("GET", f"/accounts?organization_id={world.org_a.id}").json()}
    assert "admin-a" in seen and "user-a1" in seen and "user-b1" not in seen


def test_an_org_admin_blocks_its_own_users_only(world, as_user):
    admin_a = as_user(world.admin_a)
    assert admin_a("POST", f"/accounts/{world.user_a1.id}/block").json()["is_active"] is False
    assert admin_a("POST", f"/accounts/{world.user_b1.id}/block").status_code == 403
    assert admin_a("POST", f"/accounts/{world.admin_b.id}/block").status_code == 403


@pytest.mark.parametrize("caller", ["root", "admin_a"])
def test_no_admin_may_block_itself(world, as_user, caller):
    actor = getattr(world, caller)
    assert as_user(actor)("POST", f"/accounts/{actor.id}/block").status_code == 403


def test_unblocking_restores_login(world, as_user, client):
    admin_a = as_user(world.admin_a)
    admin_a("POST", f"/accounts/{world.user_a1.id}/block")
    assert client.post("/auth/login",
                       json={"username": "user-a1", "password": PASSWORD}).status_code == 403
    admin_a("POST", f"/accounts/{world.user_a1.id}/unblock")
    assert client.post("/auth/login",
                       json={"username": "user-a1", "password": PASSWORD}).status_code == 200


def test_an_admin_sets_a_password_without_being_asked_for_the_old_one(world, as_user, client):
    assert as_user(world.admin_a)("POST", f"/accounts/{world.user_a1.id}/password",
                                  json={"password": "Brand-new-one"}).status_code == 200
    assert client.post("/auth/login", json={"username": "user-a1",
                                            "password": "Brand-new-one"}).status_code == 200
    assert client.post("/auth/login", json={"username": "user-a1",
                                            "password": PASSWORD}).status_code == 401


def test_resetting_the_password_of_someone_elses_user_is_refused(world, as_user):
    assert as_user(world.admin_b)("POST", f"/accounts/{world.user_a1.id}/password",
                                  json={"password": "Brand-new-one"}).status_code == 403


def test_an_account_that_does_not_exist_is_404(world, as_user):
    assert as_user(world.root)("POST", "/accounts/9999/block").status_code == 404


def test_an_organizations_admin_moves_to_another_organization(world, db, as_user):
    fresh = db.organization.create(data={"name": "org-c"})

    response = as_user(world.root)("POST", f"/accounts/{world.admin_a.id}/organization",
                                   json={"organization_id": fresh.id})
    assert response.status_code == 200
    assert response.json()["organization_id"] == fresh.id
    assert response.json()["role"] == "org_admin"


def test_an_ordinary_user_moves_too_and_keeps_its_role(world, as_user):
    response = as_user(world.root)("POST", f"/accounts/{world.user_a1.id}/organization",
                                   json={"organization_id": world.org_b.id})
    assert response.status_code == 200
    assert response.json()["organization_id"] == world.org_b.id
    assert response.json()["role"] == "user"


def test_the_organization_it_leaves_can_be_restaffed(world, db, as_user):
    fresh = db.organization.create(data={"name": "org-c"})

    root = as_user(world.root)
    root("POST", f"/accounts/{world.admin_a.id}/organization",
         json={"organization_id": fresh.id})
    assert root("POST", "/accounts/org-admins",
                json={"username": "admin-a-new", "password": NEW_PASSWORD, **NAME,
                      "organization_id": world.org_a.id}).status_code == 201


def test_moving_an_admin_into_an_organization_that_already_has_one_is_409(world, as_user):
    response = as_user(world.root)("POST", f"/accounts/{world.admin_a.id}/organization",
                                   json={"organization_id": world.org_b.id})
    assert response.status_code == 409
    assert "admin-b" in response.json()["detail"]


def test_submitting_the_organization_it_is_already_in_is_not_a_conflict(world, as_user):
    assert as_user(world.root)("POST", f"/accounts/{world.admin_a.id}/organization",
                               json={"organization_id": world.org_a.id}).status_code == 200


def test_moving_an_account_that_lives_in_no_organization_is_409(world, db, as_user):
    other = db.user.create(data={"username": "root-2", "hashed_password": "x",
                                 "role": "super_admin"})
    assert as_user(world.root)("POST", f"/accounts/{other.id}/organization",
                               json={"organization_id": world.org_b.id}).status_code == 409


@pytest.mark.parametrize("caller", ["admin_a", "admin_b", "user_a1"])
def test_only_a_super_admin_moves_an_account(world, db, as_user, caller):
    fresh = db.organization.create(data={"name": "org-c"})
    assert as_user(getattr(world, caller))(
        "POST", f"/accounts/{world.admin_a.id}/organization",
        json={"organization_id": fresh.id}).status_code == 403


def test_moving_to_an_organization_that_does_not_exist_is_404(world, as_user):
    assert as_user(world.root)("POST", f"/accounts/{world.admin_a.id}/organization",
                               json={"organization_id": 9999}).status_code == 404


def test_an_org_admin_deletes_its_own_users_only(world, db, as_user):
    admin_a = as_user(world.admin_a)
    assert admin_a("DELETE", f"/accounts/{world.user_a1.id}").status_code == 204
    assert db.user.find_unique(where={"username": "user-a1"}) is None
    assert admin_a("DELETE", f"/accounts/{world.user_b1.id}").status_code == 403


def test_nobody_deletes_themselves(world, as_user):
    assert as_user(world.root)("DELETE", f"/accounts/{world.root.id}").status_code == 403


def test_a_deleted_accounts_token_is_worthless(world, as_user):
    victim = as_user(world.user_a1)
    as_user(world.admin_a)("DELETE", f"/accounts/{world.user_a1.id}")
    assert victim("GET", "/auth/me").status_code == 401


def test_a_super_admin_deletes_an_org_admin_and_the_organization_is_restaffed(world, as_user):
    root = as_user(world.root)
    assert root("DELETE", f"/accounts/{world.admin_a.id}").status_code == 204
    assert root("POST", "/accounts/org-admins",
                json={"username": "admin-a-new", "password": NEW_PASSWORD, **NAME,
                      "organization_id": world.org_a.id}).status_code == 201


def test_the_role_gate_and_the_scope_check_are_both_needed(world, as_user):
    assert as_user(world.user_a1)(
        "DELETE", f"/accounts/{world.user_a2.id}").status_code == 403
    assert as_user(world.admin_b)(
        "DELETE", f"/accounts/{world.user_a2.id}").status_code == 403


def test_a_new_account_carries_the_persons_name(world, as_user):
    response = as_user(world.admin_a)(
        "POST", "/accounts/users",
        json={"username": "user-a3", "password": NEW_PASSWORD,
              "first_name": "زهرا", "last_name": "کریمی"})

    assert response.status_code == 201
    body = response.json()
    assert (body["first_name"], body["last_name"]) == ("زهرا", "کریمی")
    assert body["full_name"] == "زهرا کریمی"


@pytest.mark.parametrize("missing", ["first_name", "last_name"])
def test_an_account_cannot_be_created_without_a_name(world, as_user, missing):
    body = {"username": "user-a3", "password": NEW_PASSWORD, **NAME}
    del body[missing]
    assert as_user(world.admin_a)("POST", "/accounts/users", json=body).status_code == 422


def test_a_blank_name_is_refused(world, as_user):
    assert as_user(world.admin_a)(
        "POST", "/accounts/users",
        json={"username": "user-a3", "password": NEW_PASSWORD,
              "first_name": "   ", "last_name": "کریمی"}).status_code == 422


def test_an_admin_fixes_the_name_on_an_account_below_them(world, db, as_user):
    response = as_user(world.admin_a)("POST", f"/accounts/{world.user_a1.id}/name",
                                      json={"first_name": " علی ", "last_name": "کریمی"})

    assert response.status_code == 200
    stored = world.reload(world.user_a1)
    assert full_name(stored) == "علی کریمی"
    assert stored.username == "user-a1"


@pytest.mark.parametrize("caller, expected", [("root", 200), ("admin_a", 200),
                                              ("admin_b", 403), ("user_a2", 403)])
def test_renaming_takes_the_same_authority_as_every_other_action(world, as_user,
                                                                 caller, expected):
    response = as_user(getattr(world, caller))(
        "POST", f"/accounts/{world.user_a1.id}/name", json=NAME)
    assert response.status_code == expected


def test_nobody_renames_themselves_here(world, as_user):
    assert as_user(world.root)("POST", f"/accounts/{world.root.id}/name",
                               json=NAME).status_code == 403


def test_an_account_sets_its_own_name(world, db, as_user):
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
    body = as_user(world.root)("GET", "/accounts").json()
    legacy = next(row for row in body if row["username"] == "user-a1")
    assert legacy["first_name"] is None and legacy["full_name"] is None

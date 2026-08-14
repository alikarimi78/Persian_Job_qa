"""`/orgs` and `/units`: the containers the accounts sit in.

Both routers ask `accounts.assert_manages_organization` / `assert_manages_unit` the same
questions the account endpoints do, so what is tested here is the part that differs —
who may create and delete a container, and the rule that a non-empty one does not go.
"""

import pytest

from app.models import Unit, User


# ---------- organizations ----------

@pytest.mark.parametrize("caller, expected", [("root", 201), ("admin_a", 403),
                                              ("admin_a1", 403), ("user_a1", 403)])
def test_only_a_super_admin_creates_an_organization(world, as_user, caller, expected):
    assert as_user(getattr(world, caller))(
        "POST", "/orgs", json={"name": "org-c"}).status_code == expected


def test_an_org_admin_reads_its_own_organization_and_no_other(world, as_user):
    admin_a = as_user(world.admin_a)
    assert [o["name"] for o in admin_a("GET", "/orgs").json()] == ["org-a"]
    assert admin_a("GET", f"/orgs/{world.org_a.id}").status_code == 200
    assert admin_a("GET", f"/orgs/{world.org_b.id}").status_code == 403


def test_a_unit_admin_has_no_business_with_organizations(world, as_user):
    """Which is why `/manage` skips this call for them rather than catching the 403."""
    assert as_user(world.admin_a1)("GET", "/orgs").status_code == 403


def test_an_organization_still_holding_units_is_not_deleted(world, as_user):
    response = as_user(world.root)("DELETE", f"/orgs/{world.org_a.id}")
    assert response.status_code == 409
    assert "unit" in response.json()["detail"]


def test_an_emptied_organization_goes(world, db, as_user):
    root = as_user(world.root)
    for account in [world.admin_b1, world.user_b1, world.admin_b]:
        assert root("DELETE", f"/accounts/{account.id}").status_code == 204
    assert root("DELETE", f"/units/{world.unit_b1.id}").status_code == 204
    assert root("DELETE", f"/orgs/{world.org_b.id}").status_code == 204


# ---------- units ----------

def test_an_org_admin_creates_units_without_naming_its_organization(world, as_user):
    response = as_user(world.admin_a)("POST", "/units", json={"name": "unit-a3"})
    assert response.status_code == 201
    assert response.json()["organization_id"] == world.org_a.id


def test_an_org_admin_naming_another_organization_is_refused(world, as_user):
    """A scope error rather than a silent redirect to their own organization."""
    assert as_user(world.admin_a)(
        "POST", "/units",
        json={"name": "unit-b2", "organization_id": world.org_b.id}).status_code == 403


def test_a_super_admin_must_name_the_organization(world, as_user):
    assert as_user(world.root)("POST", "/units", json={"name": "unit-x"}).status_code == 422
    assert as_user(world.root)(
        "POST", "/units",
        json={"name": "unit-x", "organization_id": world.org_b.id}).status_code == 201


def test_a_unit_admin_reads_its_unit_but_does_not_decide_whether_it_exists(world, as_user):
    admin_a1 = as_user(world.admin_a1)
    assert [u["name"] for u in admin_a1("GET", "/units").json()] == ["unit-a1"]
    assert admin_a1("GET", f"/units/{world.unit_a1.id}").status_code == 200
    assert admin_a1("GET", f"/units/{world.unit_a2.id}").status_code == 403
    assert admin_a1("POST", "/units", json={"name": "unit-a4"}).status_code == 403
    assert admin_a1("DELETE", f"/units/{world.unit_a1.id}").status_code == 403


def test_a_unit_still_holding_accounts_is_not_deleted(world, as_user):
    response = as_user(world.admin_a)("DELETE", f"/units/{world.unit_a1.id}")
    assert response.status_code == 409
    assert "account" in response.json()["detail"]


def test_an_emptied_unit_goes_and_takes_nobody_with_it(world, db, as_user):
    admin_a = as_user(world.admin_a)
    for account in [world.user_a2, world.admin_a2]:
        assert admin_a("DELETE", f"/accounts/{account.id}").status_code == 204
    assert admin_a("DELETE", f"/units/{world.unit_a2.id}").status_code == 204
    assert db.query(Unit).filter(Unit.name == "unit-a2").first() is None
    # the neighbouring unit is untouched
    assert db.query(User).filter(User.unit_id == world.unit_a1.id).count() == 3


def test_units_of_one_organization_may_share_a_name_with_anothers(world, as_user):
    """Unit names are unique inside their organization only."""
    assert as_user(world.root)(
        "POST", "/units",
        json={"name": "unit-a1", "organization_id": world.org_b.id}).status_code == 201
    assert as_user(world.admin_a)("POST", "/units",
                                  json={"name": "unit-a1"}).status_code == 409


# ---------- renaming ----------

@pytest.mark.parametrize("caller, expected", [("root", 200), ("admin_a", 403),
                                              ("admin_a1", 403), ("user_a1", 403)])
def test_only_a_super_admin_renames_an_organization(world, as_user, caller, expected):
    """Renaming carries the authority creating does — an org_admin reads its
    organization and staffs it, but does not relabel the tenancy it sits in."""
    response = as_user(getattr(world, caller))(
        "PATCH", f"/orgs/{world.org_a.id}", json={"name": "org-a-renamed"})
    assert response.status_code == expected


def test_renaming_an_organization_moves_nothing_else(world, db, as_user):
    response = as_user(world.root)("PATCH", f"/orgs/{world.org_a.id}",
                                   json={"name": "ستاد مرکزی"})
    assert response.status_code == 200
    assert response.json() == {"id": world.org_a.id, "name": "ستاد مرکزی"}
    assert db.query(Unit).filter(Unit.organization_id == world.org_a.id).count() == 2


def test_an_organization_cannot_take_a_name_already_in_use(world, as_user):
    root = as_user(world.root)
    assert root("PATCH", f"/orgs/{world.org_a.id}",
                json={"name": "org-b"}).status_code == 409
    # its own current name is not a clash with itself
    assert root("PATCH", f"/orgs/{world.org_a.id}",
                json={"name": "org-a"}).status_code == 200


@pytest.mark.parametrize("caller, expected", [("root", 200), ("admin_a", 200),
                                              ("admin_a1", 403), ("user_a1", 403)])
def test_units_are_renamed_by_whoever_decides_they_exist(world, as_user, caller, expected):
    """The same two roles as create and delete. A unit_admin staffs the unit; they do
    not define it."""
    response = as_user(getattr(world, caller))(
        "PATCH", f"/units/{world.unit_a1.id}", json={"name": "واحد آموزش"})
    assert response.status_code == expected


def test_an_org_admin_does_not_rename_another_organizations_unit(world, as_user):
    assert as_user(world.admin_a)(
        "PATCH", f"/units/{world.unit_b1.id}", json={"name": "unit-x"}).status_code == 403


def test_a_renamed_unit_stays_in_its_organization(world, as_user):
    response = as_user(world.admin_a)("PATCH", f"/units/{world.unit_a1.id}",
                                      json={"name": "unit-a1-renamed"})
    assert response.status_code == 200
    assert response.json()["organization_id"] == world.org_a.id


def test_a_unit_cannot_take_a_sibling_name_but_may_take_a_stranger_s(world, as_user):
    """Uniqueness is the question creation asks — inside this organization only."""
    admin_a = as_user(world.admin_a)
    assert admin_a("PATCH", f"/units/{world.unit_a1.id}",
                   json={"name": "unit-a2"}).status_code == 409
    assert admin_a("PATCH", f"/units/{world.unit_a1.id}",
                   json={"name": "unit-b1"}).status_code == 200

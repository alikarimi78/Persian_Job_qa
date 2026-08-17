"""`/orgs` and `/units`: the containers the accounts sit in.

Both routers ask `accounts.assert_manages_organization` / `assert_manages_unit` the same
questions the account endpoints do, so what is tested here is the part that differs —
who may create and delete a container, and the rule that a non-empty one does not go.
"""

import pytest


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
    assert db.unit.find_first(where={"name": "unit-a2"}) is None
    # the neighbouring unit is untouched
    assert db.user.count(where={"unit_id": world.unit_a1.id}) == 3


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
    # The profile columns come back empty rather than absent: a rename is a rename, and
    # a client sending only a name still gets the whole record described.
    assert response.json() == {"id": world.org_a.id, "name": "ستاد مرکزی",
                               "code": None, "address": None, "phone": None,
                               "email": None, "has_logo": False}
    assert db.unit.count(where={"organization_id": world.org_a.id}) == 2


# ---------- the organization profile (admin_panel.mp4's «افزودن سازمان») ----------

# A real 1×1 PNG, so the magic-byte check in `decode_logo` sees what it expects.
PNG = ("data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4"
       "2mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==")

PROFILE = {"name": "org-c", "code": "۲۱۳", "address": "تهران، خیابان آزادی",
           "phone": "۰۹۱۰۲۱۷۸۱۴۶", "email": "Info@Example.COM"}


def test_an_organization_is_created_with_its_whole_profile(world, as_user):
    body = as_user(world.root)("POST", "/orgs", json=PROFILE).json()
    assert body["code"] == "۲۱۳"          # the code is theirs, stored as typed
    assert body["address"] == "تهران، خیابان آزادی"
    assert body["phone"] == "09102178146"  # Persian digits normalized to ASCII
    assert body["email"] == "info@example.com"
    assert body["has_logo"] is False


def test_the_profile_is_optional_so_older_organizations_stay_editable(world, as_user):
    """The reference form marks four of these required; the column is not. An
    organization created before this migration has none of them, and renaming it must
    not require inventing an email first."""
    root = as_user(world.root)
    assert root("POST", "/orgs", json={"name": "org-plain"}).status_code == 201
    response = root("PATCH", f"/orgs/{world.org_a.id}", json={"name": "org-a-renamed"})
    assert response.status_code == 200
    assert response.json()["email"] is None


@pytest.mark.parametrize("field, value", [("email", "not-an-email"),
                                          ("email", "a@b"),
                                          ("phone", "12345"),
                                          ("phone", "not a phone")])
def test_a_malformed_contact_detail_is_refused(world, as_user, field, value):
    assert as_user(world.root)(
        "POST", "/orgs", json={"name": "org-bad", field: value}).status_code == 422


def test_a_patch_touches_only_the_fields_it_sends(world, as_user):
    root = as_user(world.root)
    created = root("POST", "/orgs", json=PROFILE).json()

    patched = root("PATCH", f"/orgs/{created['id']}", json={"phone": "021-88776655"})
    assert patched.status_code == 200
    assert patched.json()["phone"] == "021-88776655"
    assert patched.json()["email"] == "info@example.com"   # untouched, not cleared

    # An empty box in the form is how a value is removed — absent and empty are
    # deliberately different things.
    cleared = root("PATCH", f"/orgs/{created['id']}", json={"address": ""})
    assert cleared.json()["address"] is None
    assert cleared.json()["phone"] == "021-88776655"


def test_a_name_cannot_be_cleared(world, as_user):
    """Every other field may go to null; the name is what the organization *is*."""
    assert as_user(world.root)(
        "PATCH", f"/orgs/{world.org_a.id}", json={"name": None}).status_code == 422


def test_a_logo_survives_the_round_trip_but_stays_out_of_the_list(world, as_user):
    root = as_user(world.root)
    created = root("POST", "/orgs", json={**PROFILE, "logo": PNG}).json()
    assert created["has_logo"] is True
    assert "logo" not in created

    assert root("GET", f"/orgs/{created['id']}/logo").json()["logo"] == PNG

    # The whole point of the separate endpoint: a page listing organizations pays for
    # names, not for images.
    listed = root("GET", "/orgs").json()
    assert all("logo" not in org for org in listed)
    assert [org["has_logo"] for org in listed if org["id"] == created["id"]] == [True]


def test_an_organization_without_a_logo_answers_null_rather_than_404(world, as_user):
    """Not having one is not an error — the client draws a placeholder either way."""
    response = as_user(world.root)("GET", f"/orgs/{world.org_a.id}/logo")
    assert response.status_code == 200
    assert response.json() == {"logo": None}


def test_a_logo_is_cleared_by_sending_an_empty_string(world, as_user):
    root = as_user(world.root)
    created = root("POST", "/orgs", json={**PROFILE, "logo": PNG}).json()
    assert root("PATCH", f"/orgs/{created['id']}", json={"logo": ""}).json()["has_logo"] is False
    assert root("GET", f"/orgs/{created['id']}/logo").json()["logo"] is None


def test_a_logo_that_is_not_the_type_it_claims_is_refused(world, as_user):
    """The declared mime decides how the bytes are served back, so it is checked against
    the file's own signature rather than believed."""
    import base64
    payload = base64.b64encode(b"<svg onload=alert(1)>").decode()
    assert as_user(world.root)(
        "POST", "/orgs",
        json={"name": "org-x", "logo": f"data:image/png;base64,{payload}"}
    ).status_code == 422


def test_svg_is_not_an_accepted_logo_type(world, as_user):
    """It is a document that can carry script, and this one is served back to a page."""
    import base64
    payload = base64.b64encode(b"<svg xmlns='http://www.w3.org/2000/svg'/>").decode()
    assert as_user(world.root)(
        "POST", "/orgs",
        json={"name": "org-y", "logo": f"data:image/svg+xml;base64,{payload}"}
    ).status_code == 422


def test_an_oversized_logo_is_refused(world, as_user):
    import base64
    from src.routers.orgs import MAX_LOGO_BYTES
    raw = b"\x89PNG\r\n\x1a\n" + b"\x00" * MAX_LOGO_BYTES
    payload = base64.b64encode(raw).decode()
    assert as_user(world.root)(
        "POST", "/orgs",
        json={"name": "org-z", "logo": f"data:image/png;base64,{payload}"}
    ).status_code == 413


def test_an_org_admin_reads_its_own_logo_and_no_other(world, as_user):
    """The same scope question `GET /orgs/{id}` asks — a logo is part of the record."""
    admin_a = as_user(world.admin_a)
    assert admin_a("GET", f"/orgs/{world.org_a.id}/logo").status_code == 200
    assert admin_a("GET", f"/orgs/{world.org_b.id}/logo").status_code == 403


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

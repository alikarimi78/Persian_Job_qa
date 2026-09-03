import pytest


@pytest.mark.parametrize("caller, expected", [("root", 201), ("admin_a", 403),
                                              ("user_a1", 403)])
def test_only_a_super_admin_creates_an_organization(world, as_user, caller, expected):
    assert as_user(getattr(world, caller))(
        "POST", "/orgs", json={"name": "org-c"}).status_code == expected


def test_an_org_admin_reads_its_own_organization_and_no_other(world, as_user):
    admin_a = as_user(world.admin_a)
    assert [o["name"] for o in admin_a("GET", "/orgs").json()] == ["org-a"]
    assert admin_a("GET", f"/orgs/{world.org_a.id}").status_code == 200
    assert admin_a("GET", f"/orgs/{world.org_b.id}").status_code == 403


def test_an_ordinary_user_has_no_business_with_organizations(world, as_user):
    assert as_user(world.user_a1)("GET", "/orgs").status_code == 403


def test_an_organization_still_holding_accounts_is_not_deleted(world, as_user):
    response = as_user(world.root)("DELETE", f"/orgs/{world.org_a.id}")
    assert response.status_code == 409
    assert "account" in response.json()["detail"]


def test_an_emptied_organization_goes(world, db, as_user):
    root = as_user(world.root)
    for account in [world.user_b1, world.admin_b]:
        assert root("DELETE", f"/accounts/{account.id}").status_code == 204
    assert root("DELETE", f"/orgs/{world.org_b.id}").status_code == 204
    assert db.organization.find_first(where={"name": "org-b"}) is None


@pytest.mark.parametrize("caller, expected", [("root", 200), ("admin_a", 403),
                                              ("user_a1", 403)])
def test_only_a_super_admin_renames_an_organization(world, as_user, caller, expected):
    response = as_user(getattr(world, caller))(
        "PATCH", f"/orgs/{world.org_a.id}", json={"name": "org-a-renamed"})
    assert response.status_code == expected


def test_renaming_an_organization_moves_nothing_else(world, db, as_user):
    response = as_user(world.root)("PATCH", f"/orgs/{world.org_a.id}",
                                   json={"name": "ستاد مرکزی"})
    assert response.status_code == 200
    assert response.json() == {"id": world.org_a.id, "name": "ستاد مرکزی",
                               "code": None, "address": None, "phone": None,
                               "email": None, "has_logo": False}
    assert db.user.count(where={"organization_id": world.org_a.id}) == 3


PNG = ("data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4"
       "2mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==")

PROFILE = {"name": "org-c", "code": "۲۱۳", "address": "تهران، خیابان آزادی",
           "phone": "۰۹۱۰۲۱۷۸۱۴۶", "email": "Info@Example.COM"}


def test_an_organization_is_created_with_its_whole_profile(world, as_user):
    body = as_user(world.root)("POST", "/orgs", json=PROFILE).json()
    assert body["code"] == "۲۱۳"
    assert body["address"] == "تهران، خیابان آزادی"
    assert body["phone"] == "09102178146"
    assert body["email"] == "info@example.com"
    assert body["has_logo"] is False


def test_the_profile_is_optional_so_older_organizations_stay_editable(world, as_user):
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
    assert patched.json()["email"] == "info@example.com"

    cleared = root("PATCH", f"/orgs/{created['id']}", json={"address": ""})
    assert cleared.json()["address"] is None
    assert cleared.json()["phone"] == "021-88776655"


def test_a_name_cannot_be_cleared(world, as_user):
    assert as_user(world.root)(
        "PATCH", f"/orgs/{world.org_a.id}", json={"name": None}).status_code == 422


def test_a_logo_survives_the_round_trip_but_stays_out_of_the_list(world, as_user):
    root = as_user(world.root)
    created = root("POST", "/orgs", json={**PROFILE, "logo": PNG}).json()
    assert created["has_logo"] is True
    assert "logo" not in created

    assert root("GET", f"/orgs/{created['id']}/logo").json()["logo"] == PNG

    listed = root("GET", "/orgs").json()
    assert all("logo" not in org for org in listed)
    assert [org["has_logo"] for org in listed if org["id"] == created["id"]] == [True]


def test_an_organization_without_a_logo_answers_null_rather_than_404(world, as_user):
    response = as_user(world.root)("GET", f"/orgs/{world.org_a.id}/logo")
    assert response.status_code == 200
    assert response.json() == {"logo": None}


def test_a_logo_is_cleared_by_sending_an_empty_string(world, as_user):
    root = as_user(world.root)
    created = root("POST", "/orgs", json={**PROFILE, "logo": PNG}).json()
    assert root("PATCH", f"/orgs/{created['id']}", json={"logo": ""}).json()["has_logo"] is False
    assert root("GET", f"/orgs/{created['id']}/logo").json()["logo"] is None


def test_a_logo_that_is_not_the_type_it_claims_is_refused(world, as_user):
    import base64
    payload = base64.b64encode(b"<svg onload=alert(1)>").decode()
    assert as_user(world.root)(
        "POST", "/orgs",
        json={"name": "org-x", "logo": f"data:image/png;base64,{payload}"}
    ).status_code == 422


def test_svg_is_not_an_accepted_logo_type(world, as_user):
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
    admin_a = as_user(world.admin_a)
    assert admin_a("GET", f"/orgs/{world.org_a.id}/logo").status_code == 200
    assert admin_a("GET", f"/orgs/{world.org_b.id}/logo").status_code == 403


def test_an_organization_cannot_take_a_name_already_in_use(world, as_user):
    root = as_user(world.root)
    assert root("PATCH", f"/orgs/{world.org_a.id}",
                json={"name": "org-b"}).status_code == 409
    assert root("PATCH", f"/orgs/{world.org_a.id}",
                json={"name": "org-a"}).status_code == 200

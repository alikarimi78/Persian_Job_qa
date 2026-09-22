from datetime import datetime, timedelta, timezone

from src.rate_limit import login_limiter

from .conftest import PASSWORD

NAME = {"first_name": "زهرا", "last_name": "کریمی"}
NEW_PASSWORD = "Password-12"


def created(as_user, actor, url, username, **extra):
    response = as_user(actor)("POST", url, json={"username": username,
                                                 "password": NEW_PASSWORD, **NAME, **extra})
    assert response.status_code == 201
    return response.json()


def test_every_creation_path_records_who_made_the_account(world, db, as_user):
    fresh = db.organization.create(data={"name": "org-c"})
    made = [created(as_user, world.root, "/accounts/super-admins", "root-2"),
            created(as_user, world.root, "/accounts/org-admins", "admin-c",
                    organization_id=fresh.id),
            created(as_user, world.root, "/accounts/users", "user-c1", organization_id=fresh.id),
            created(as_user, world.admin_a, "/accounts/users", "user-a3")]

    creators = [db.user.find_unique(where={"id": body["id"]}).created_by for body in made]
    assert creators == [world.root.id, world.root.id, world.root.id, world.admin_a.id]


def test_the_listing_names_the_creator_to_an_org_admin(world, as_user):
    created(as_user, world.root, "/accounts/users", "user-a3", organization_id=world.org_a.id)
    created(as_user, world.admin_a, "/accounts/users", "user-a4")

    rows = {row["username"]: row for row in as_user(world.admin_a)("GET", "/accounts").json()}
    assert rows["user-a3"]["creator"]["username"] == "root"
    assert rows["user-a4"]["creator"] == {"id": world.admin_a.id, "username": "admin-a",
                                          "first_name": None, "last_name": None,
                                          "full_name": None}
    assert rows["user-a1"]["creator"] is None
    assert "user-b1" not in rows


def test_the_listing_carries_the_three_timestamps(world, as_user):
    row = next(r for r in as_user(world.root)("GET", "/accounts").json()
               if r["username"] == "user-a1")
    assert row["created_at"] and row["updated_at"]
    assert row["last_login"] is None


def test_a_login_stamps_last_login_and_is_not_an_edit(world, db, client):
    before = world.reload(world.user_a1)
    assert client.post("/auth/login", json={"username": "user-a1",
                                            "password": PASSWORD}).status_code == 200

    after = world.reload(world.user_a1)
    assert abs(datetime.now(timezone.utc) - after.last_login) < timedelta(minutes=1)
    assert after.updated_at == before.updated_at


def test_a_refused_login_stamps_nothing(world, db, client):
    login_limiter._hits.clear()
    db.user.update(where={"id": world.user_a2.id}, data={"is_active": False})
    assert client.post("/auth/login", json={"username": "user-a1",
                                            "password": "wrong-Password-1"}).status_code == 401
    assert client.post("/auth/login", json={"username": "user-a2",
                                            "password": PASSWORD}).status_code == 403
    login_limiter._hits.clear()

    assert world.reload(world.user_a1).last_login is None
    assert world.reload(world.user_a2).last_login is None


def test_an_edit_moves_updated_at(world, as_user):
    before = world.reload(world.user_a1).updated_at
    assert as_user(world.admin_a)("POST", f"/accounts/{world.user_a1.id}/name",
                                  json=NAME).status_code == 200
    assert world.reload(world.user_a1).updated_at > before


def test_deleting_the_creator_keeps_the_accounts_it_made(world, db, as_user):
    root_2 = created(as_user, world.root, "/accounts/super-admins", "root-2")
    made = created(as_user, db.user.find_unique(where={"id": root_2["id"]}),
                   "/accounts/users", "user-a3", organization_id=world.org_a.id)

    assert as_user(world.root)("DELETE", f"/accounts/{root_2['id']}").status_code == 204
    survivor = db.user.find_unique(where={"id": made["id"]})
    assert survivor is not None and survivor.created_by is None

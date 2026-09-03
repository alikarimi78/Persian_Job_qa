import pytest

from src.models import JobStatus


@pytest.mark.parametrize("caller, expected", [("root", 200), ("admin_a", 200),
                                              ("user_a1", 403)])
def test_only_admins_have_a_dashboard(world, as_user, caller, expected):
    assert as_user(getattr(world, caller))("GET", "/stats").status_code == expected


def test_a_super_admin_counts_everything(world, as_user):
    body = as_user(world.root)("GET", "/stats").json()
    assert body["scope"] == "global"
    assert body["scope_name"] is None
    assert (body["organizations"], body["accounts"]) == (2, 6)
    assert {r["role"]: r["count"] for r in body["accounts_by_role"]} == {
        "super_admin": 1, "org_admin": 2, "user": 3}


def test_an_org_admin_counts_its_own_organization(world, as_user):
    body = as_user(world.admin_a)("GET", "/stats").json()
    assert body["scope"] == "organization"
    assert body["scope_name"] == "org-a"
    assert (body["organizations"], body["accounts"]) == (1, 2)


def test_a_role_with_nobody_in_it_is_reported_as_zero(world, as_user):
    body = as_user(world.admin_a)("GET", "/stats").json()
    assert {r["role"]: r["count"] for r in body["accounts_by_role"]} == {
        "super_admin": 0, "org_admin": 0, "user": 2}


def test_the_dataset_size_is_global_but_the_queue_is_scoped(world, db, as_user):
    db.jobrecord.create_many(data=[
        {"job_title": "seeded", "status": JobStatus.approved},
        {"job_title": "from-a", "status": JobStatus.pending,
         "suggested_by": world.user_a1.id},
        {"job_title": "from-b", "status": JobStatus.pending,
         "suggested_by": world.user_b1.id},
    ])

    root = as_user(world.root)("GET", "/stats").json()["jobs"]
    assert (root["corpus_records"], root["pending"]) == (1, 2)
    assert root["engine_records"] is None

    admin_a = as_user(world.admin_a)("GET", "/stats").json()["jobs"]
    assert (admin_a["corpus_records"], admin_a["pending"]) == (1, 1)


def test_an_admins_own_suggestion_counts_towards_their_scope(world, db, as_user):
    db.jobrecord.create(data={"job_title": "mine", "status": JobStatus.pending,
                              "suggested_by": world.admin_a.id})
    assert as_user(world.admin_a)("GET", "/stats").json()["jobs"]["pending"] == 1


def test_creation_dates_come_back_as_one_count_per_day(world, as_user):
    body = as_user(world.root)("GET", "/stats").json()
    assert sum(point["count"] for point in body["accounts_series"]) == 6
    assert sum(point["count"] for point in body["organizations_series"]) == 2
    assert [point["date"] for point in body["accounts_series"]] == sorted(
        point["date"] for point in body["accounts_series"])

"""`/stats`: the dashboard's numbers, and the promise that they are the caller's own.

The endpoint reuses `accounts.visible_users` and the units router's narrowing, so what
is worth testing is not the arithmetic but the scope — that an org_admin's totals stop
at its organization and a unit_admin's at its unit. The world fixture is built for
exactly that: org_a holds two units and five accounts below its admin, org_b one unit
and two, and a wrong scope shows up as a number from the other organization.
"""

import pytest

from app.models import JobRecord, JobStatus


@pytest.mark.parametrize("caller, expected", [("root", 200), ("admin_a", 200),
                                              ("admin_a1", 200), ("user_a1", 403)])
def test_only_admins_have_a_dashboard(world, as_user, caller, expected):
    assert as_user(getattr(world, caller))("GET", "/stats").status_code == expected


def test_a_super_admin_counts_everything(world, as_user):
    body = as_user(world.root)("GET", "/stats").json()
    assert body["scope"] == "global"
    assert body["scope_name"] is None
    assert (body["organizations"], body["units"], body["accounts"]) == (2, 3, 10)
    assert {r["role"]: r["count"] for r in body["accounts_by_role"]} == {
        "super_admin": 1, "org_admin": 2, "unit_admin": 3, "user": 4}


def test_an_org_admin_counts_its_own_organization(world, as_user):
    """Five accounts, not ten: `visible_users` leaves out both org_b and the caller's
    own row, which is the same set `GET /accounts` returns them."""
    body = as_user(world.admin_a)("GET", "/stats").json()
    assert body["scope"] == "organization"
    assert body["scope_name"] == "org-a"
    assert (body["organizations"], body["units"], body["accounts"]) == (1, 2, 5)
    assert sorted(u["name"] for u in body["accounts_per_unit"]) == ["unit-a1", "unit-a2"]
    assert {u["name"]: u["accounts"] for u in body["accounts_per_unit"]} == {
        "unit-a1": 3, "unit-a2": 2}


def test_a_unit_admin_counts_its_own_unit(world, as_user):
    body = as_user(world.admin_a1)("GET", "/stats").json()
    assert body["scope"] == "unit"
    assert body["scope_name"] == "unit-a1"
    assert (body["units"], body["accounts"]) == (1, 3)
    assert [u["name"] for u in body["accounts_per_unit"]] == ["unit-a1"]


def test_a_role_with_nobody_in_it_is_reported_as_zero(world, as_user):
    """A bar that vanishes when a unit empties reads as a missing category, not a count."""
    body = as_user(world.admin_a1)("GET", "/stats").json()
    assert {r["role"]: r["count"] for r in body["accounts_by_role"]} == {
        "super_admin": 0, "org_admin": 0, "unit_admin": 1, "user": 2}


def test_the_dataset_size_is_global_but_the_queue_is_scoped(world, db, as_user):
    """`corpus_records` is the one shared dataset everyone searches, so it does not
    shrink to a tenant. The pending/approved counts do: they are what *these* accounts
    suggested."""
    db.add_all([
        JobRecord(job_title="seeded", status=JobStatus.approved),          # no suggester
        JobRecord(job_title="from-a", status=JobStatus.pending, suggested_by=world.user_a1.id),
        JobRecord(job_title="from-b", status=JobStatus.pending, suggested_by=world.user_b1.id),
    ])
    db.commit()

    root = as_user(world.root)("GET", "/stats").json()["jobs"]
    assert (root["corpus_records"], root["pending"]) == (1, 2)
    # No engine is loaded in the test app; None is not the same answer as zero.
    assert root["engine_records"] is None

    admin_a = as_user(world.admin_a)("GET", "/stats").json()["jobs"]
    assert (admin_a["corpus_records"], admin_a["pending"]) == (1, 1)


def test_an_admins_own_suggestion_counts_towards_their_scope(world, db, as_user):
    """`visible_users` excludes the caller — they do not manage themselves — but what
    they suggested is still their organization's."""
    db.add(JobRecord(job_title="mine", status=JobStatus.pending, suggested_by=world.admin_a.id))
    db.commit()
    assert as_user(world.admin_a)("GET", "/stats").json()["jobs"]["pending"] == 1


def test_creation_dates_come_back_as_one_count_per_day(world, as_user):
    """The client groups these into Persian months, which is why they arrive as days."""
    body = as_user(world.root)("GET", "/stats").json()
    assert sum(point["count"] for point in body["accounts_series"]) == 10
    assert sum(point["count"] for point in body["units_series"]) == 3
    assert [point["date"] for point in body["accounts_series"]] == sorted(
        point["date"] for point in body["accounts_series"])

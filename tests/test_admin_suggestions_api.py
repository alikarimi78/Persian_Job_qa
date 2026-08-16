"""`PUT /admin/suggestions/{id}` — a reviewer correcting a record before deciding on it.

The queue's other two actions are the decision itself; this is the edit that usually
comes first, and it has to hold the same two rules they do. Moderation is
**super-admin-only** whatever authority an org or unit admin has inside their own
tenancy, because the corpus is one shared dataset; and a record that has already been
decided on is closed — an approved row is in the corpus every organization searches, so
changing it there is a dataset edit (and a rebuild), not a review.
"""

import pytest

from app.engine_manager import manager
from app.models import JobRecord, JobStatus

COLUMNS = {
    "job_title": "راننده خودرو زرهی",
    "aliases": "راننده نفربر | راننده تانک",
    "tools": "نفربر زرهی | بی‌سیم",
    "skills": "رانندگی در زمین ناهموار | تعمیرات اولیه",
    "knowledge": "مکانیک خودرو | آیین‌نامه",
    "abilities": "تمرکز بالا | واکنش سریع",
    "work_context": "زمین ناهموار و شرایط سخت",
    "career_path_next": "سرپرست گروهان تانک",
    "description": "هدایت و نگهداری خودروهای زرهی در ماموریت‌ها",
    "responsibilities": "هدایت خودرو | نگهداری روزانه | گزارش خرابی",
}


@pytest.fixture
def pending(db, world) -> JobRecord:
    record = JobRecord(**COLUMNS, status=JobStatus.pending, suggested_by=world.user_a1.id)
    db.add(record)
    db.commit()
    db.refresh(record)
    return record


@pytest.fixture
def rebuilds(monkeypatch) -> list:
    """Records what the handlers asked the engine manager for, without starting a thread
    that would try to build a real engine from a database this suite does not have."""
    calls = []
    monkeypatch.setattr(manager, "rebuild_async",
                        lambda force_embeddings=False: calls.append(force_embeddings) or True)
    return calls


def edited(**overrides) -> dict:
    return {**COLUMNS, **overrides}


def test_super_admin_edits_a_pending_suggestion(as_user, world, pending, db):
    body = edited(job_title="راننده نفربر زرهی", skills="رانندگی در زمین ناهموار | امدادرسانی")
    response = as_user(world.root)("PUT", f"/admin/suggestions/{pending.id}", json=body)

    assert response.status_code == 200
    assert response.json()["job_title"] == "راننده نفربر زرهی"
    db.refresh(pending)
    assert pending.skills == "رانندگی در زمین ناهموار | امدادرسانی"
    # An edit is not a decision: the record is still waiting for one.
    assert pending.status == JobStatus.pending
    assert pending.suggested_by == world.user_a1.id


def test_the_edit_survives_into_the_approval(as_user, world, pending, db):
    as_user(world.root)("PUT", f"/admin/suggestions/{pending.id}",
                        json=edited(description="شرح اصلاح‌شده"))
    response = as_user(world.root)("POST", f"/admin/suggestions/{pending.id}/approve")

    assert response.status_code == 200
    assert response.json()["description"] == "شرح اصلاح‌شده"
    assert response.json()["status"] == "approved"


@pytest.mark.parametrize("account", ["admin_a", "admin_a1", "user_a1"])
def test_only_a_super_admin_may_edit(as_user, world, pending, account):
    response = as_user(getattr(world, account))(
        "PUT", f"/admin/suggestions/{pending.id}", json=edited(job_title="عنوان دیگر"))
    assert response.status_code == 403


def test_a_reviewed_record_is_closed_to_editing(as_user, world, pending, db):
    pending.status = JobStatus.approved
    db.commit()

    response = as_user(world.root)("PUT", f"/admin/suggestions/{pending.id}",
                                   json=edited(job_title="عنوان دیگر"))
    assert response.status_code == 409
    assert "approved" in response.json()["detail"]


def test_approving_starts_a_rebuild(as_user, world, pending, rebuilds):
    response = as_user(world.root)("POST", f"/admin/suggestions/{pending.id}/approve")

    assert response.status_code == 200
    # Not forced: the embedding store is keyed on each record's text, so this encodes the
    # two texts of the new record and reuses the rest.
    assert rebuilds == [False]


def test_adding_a_record_directly_starts_a_rebuild(as_user, world, rebuilds):
    response = as_user(world.root)("POST", "/admin/jobs", json=edited(job_title="راننده لجستیک"))

    assert response.status_code == 201
    assert rebuilds == [False]


@pytest.mark.parametrize("route,expected", [("reject", 200), ("approve", 200)])
def test_only_approval_touches_the_corpus(as_user, world, pending, rebuilds, route, expected):
    response = as_user(world.root)("POST", f"/admin/suggestions/{pending.id}/{route}")

    assert response.status_code == expected
    # Rejecting changes nothing a search can reach, so it must not spend a rebuild.
    assert rebuilds == ([] if route == "reject" else [False])


def test_editing_does_not_start_a_rebuild(as_user, world, pending, rebuilds):
    as_user(world.root)("PUT", f"/admin/suggestions/{pending.id}", json=edited(job_title="عنوان دیگر"))
    assert rebuilds == []


def test_missing_record_is_404(as_user, world):
    response = as_user(world.root)("PUT", "/admin/suggestions/9999", json=edited())
    assert response.status_code == 404


def test_every_column_is_still_required(as_user, world, pending):
    body = edited()
    del body["skills"]
    response = as_user(world.root)("PUT", f"/admin/suggestions/{pending.id}", json=body)
    assert response.status_code == 422
    assert "skills" in str(response.json()["detail"])

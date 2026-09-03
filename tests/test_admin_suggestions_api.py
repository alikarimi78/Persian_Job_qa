import pytest

from src.engine_manager import manager
from src.models import JobRecord, JobStatus

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
    return db.jobrecord.create(data={**COLUMNS, "status": JobStatus.pending,
                                     "suggested_by": world.user_a1.id})


@pytest.fixture
def rebuilds(monkeypatch) -> list:
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
    stored = db.jobrecord.find_unique(where={"id": pending.id})
    assert stored.skills == "رانندگی در زمین ناهموار | امدادرسانی"
    assert stored.status == JobStatus.pending
    assert stored.suggested_by == world.user_a1.id


def test_the_edit_survives_into_the_approval(as_user, world, pending, db):
    as_user(world.root)("PUT", f"/admin/suggestions/{pending.id}",
                        json=edited(description="شرح اصلاح‌شده"))
    response = as_user(world.root)("POST", f"/admin/suggestions/{pending.id}/approve")

    assert response.status_code == 200
    assert response.json()["description"] == "شرح اصلاح‌شده"
    assert response.json()["status"] == "approved"


@pytest.mark.parametrize("account", ["admin_a", "user_a1", "user_a2"])
def test_only_a_super_admin_may_edit(as_user, world, pending, account):
    response = as_user(getattr(world, account))(
        "PUT", f"/admin/suggestions/{pending.id}", json=edited(job_title="عنوان دیگر"))
    assert response.status_code == 403


def test_a_reviewed_record_is_closed_to_editing(as_user, world, pending, db):
    db.jobrecord.update(where={"id": pending.id}, data={"status": JobStatus.approved})

    response = as_user(world.root)("PUT", f"/admin/suggestions/{pending.id}",
                                   json=edited(job_title="عنوان دیگر"))
    assert response.status_code == 409
    assert "approved" in response.json()["detail"]


def test_approving_starts_a_rebuild(as_user, world, pending, rebuilds):
    response = as_user(world.root)("POST", f"/admin/suggestions/{pending.id}/approve")

    assert response.status_code == 200
    assert rebuilds == [False]


def test_adding_a_record_directly_starts_a_rebuild(as_user, world, rebuilds):
    response = as_user(world.root)("POST", "/admin/jobs", json=edited(job_title="راننده لجستیک"))

    assert response.status_code == 201
    assert rebuilds == [False]


@pytest.mark.parametrize("route,expected", [("reject", 200), ("approve", 200)])
def test_only_approval_touches_the_corpus(as_user, world, pending, rebuilds, route, expected):
    response = as_user(world.root)("POST", f"/admin/suggestions/{pending.id}/{route}")

    assert response.status_code == expected
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

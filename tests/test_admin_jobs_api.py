import pytest

from src.engine_manager import manager
from src.models import JobStatus

COLUMNS = {
    "job_title": "راننده خودرو زرهی",
    "aliases": "راننده نفربر | راننده تانک",
    "tools": "نفربر زرهی | بی‌سیم",
    "skills": "رانندگی در زمین ناهموار | تعمیرات اولیه",
    "knowledge": "مکانیک خودرو | آیین‌نامه",
    "abilities": "تمرکز بالا | واکنش سریع",
    "work_context": "زمین ناهموار | شرایط سخت",
    "career_path_next": "سرپرست گروهان تانک",
    "description": "هدایت و نگهداری خودروهای زرهی در ماموریت‌ها",
    "responsibilities": "هدایت خودرو | نگهداری روزانه | گزارش خرابی",
}

TITLES = [
    "افسران توپخانه",
    "برنامه‌نویسان سمت سرور",
    "برنامه‌نویسان کامپیوتر",
    "پرستاران بخش ویژه",
    "حسابداران و حسابرسان",
]


def edited(**overrides) -> dict:
    return {**COLUMNS, **overrides}


@pytest.fixture
def rebuilds(monkeypatch) -> list:
    calls = []
    monkeypatch.setattr(manager, "rebuild_async",
                        lambda force_embeddings=False: calls.append(force_embeddings) or True)
    return calls


@pytest.fixture
def corpus(db, world) -> list:
    rows = [db.jobrecord.create(data={**COLUMNS, "job_title": title,
                                      "status": JobStatus.approved,
                                      "reviewed_by": world.root.id})
            for title in TITLES]
    db.jobrecord.create(data={**COLUMNS, "job_title": "افسران پیشنهادی",
                              "status": JobStatus.pending,
                              "suggested_by": world.user_a1.id})
    db.jobrecord.create(data={**COLUMNS, "job_title": "افسران ردشده",
                              "status": JobStatus.rejected,
                              "reviewed_by": world.root.id})
    return rows


def test_the_listing_holds_only_approved_records(as_user, world, corpus):
    body = as_user(world.root)("GET", "/admin/jobs").json()

    assert body["total"] == len(TITLES)
    assert [it["job_title"] for it in body["items"]] == TITLES
    assert {it["status"] for it in body["items"]} == {"approved"}


def test_a_page_carries_the_whole_record(as_user, world, corpus):
    first = as_user(world.root)("GET", "/admin/jobs").json()["items"][0]

    assert set(COLUMNS) <= set(first)
    assert first["responsibilities"] == COLUMNS["responsibilities"]
    assert first["updated_at"] is not None


def test_pagination_splits_the_corpus_and_total_stays_the_whole_of_it(as_user, world, corpus):
    page_1 = as_user(world.root)("GET", "/admin/jobs?page=1&page_size=2").json()
    page_3 = as_user(world.root)("GET", "/admin/jobs?page=3&page_size=2").json()

    assert [it["job_title"] for it in page_1["items"]] == TITLES[:2]
    assert [it["job_title"] for it in page_3["items"]] == TITLES[4:]
    assert page_1["total"] == page_3["total"] == len(TITLES)
    assert (page_1["page"], page_1["page_size"]) == (1, 2)


def test_a_page_past_the_end_is_empty_rather_than_an_error(as_user, world, corpus):
    body = as_user(world.root)("GET", "/admin/jobs?page=99&page_size=2").json()
    assert body["items"] == []
    assert body["total"] == len(TITLES)


@pytest.mark.parametrize("params,page,page_size", [
    ("page=0&page_size=0", 1, 1),
    ("page=-5&page_size=5000", 1, 100),
])
def test_the_pager_is_clamped_rather_than_refused(as_user, world, corpus, params, page, page_size):
    body = as_user(world.root)("GET", f"/admin/jobs?{params}").json()
    assert (body["page"], body["page_size"]) == (page, page_size)


def test_search_narrows_the_listing_and_the_pager_with_it(as_user, world, corpus):
    body = as_user(world.root)("GET", "/admin/jobs?q=برنامه‌نویسان").json()

    assert body["total"] == 2
    assert [it["job_title"] for it in body["items"]] == TITLES[1:3]


def test_a_title_typed_with_a_space_finds_the_zwnj_the_corpus_stores(as_user, world, corpus):
    body = as_user(world.root)("GET", "/admin/jobs?q=برنامه نویسان").json()
    assert body["total"] == 2


def test_arabic_letters_are_folded_onto_the_persian_ones(as_user, world, corpus):
    body = as_user(world.root)("GET", "/admin/jobs?q=پرستاران بخش ويژه").json()
    assert body["total"] == 1


def test_a_search_matching_nothing_is_an_empty_page(as_user, world, corpus):
    body = as_user(world.root)("GET", "/admin/jobs?q=خلبان").json()
    assert body == {"items": [], "total": 0, "page": 1, "page_size": 20}


def test_the_other_statuses_are_reachable_but_never_by_default(as_user, world, corpus):
    body = as_user(world.root)("GET", "/admin/jobs?job_status=pending").json()
    assert [it["job_title"] for it in body["items"]] == ["افسران پیشنهادی"]


@pytest.mark.parametrize("account", ["admin_a", "user_a1", "user_a2"])
def test_only_a_super_admin_may_read_the_corpus(as_user, world, corpus, account):
    assert as_user(getattr(world, account))("GET", "/admin/jobs").status_code == 403


def test_a_super_admin_edits_a_record_in_the_corpus(as_user, world, corpus, db, rebuilds):
    record = corpus[0]
    body = edited(job_title="افسران توپخانه و موشک", skills="هدایت آتش | کار با سامانه")
    response = as_user(world.root)("PUT", f"/admin/jobs/{record.id}", json=body)

    assert response.status_code == 200
    stored = db.jobrecord.find_unique(where={"id": record.id})
    assert stored.job_title == "افسران توپخانه و موشک"
    assert stored.skills == "هدایت آتش | کار با سامانه"
    assert stored.status == JobStatus.approved
    assert stored.reviewed_by == world.root.id


def test_editing_a_record_starts_a_rebuild(as_user, world, corpus, rebuilds):
    response = as_user(world.root)("PUT", f"/admin/jobs/{corpus[0].id}",
                                   json=edited(description="شرح اصلاح‌شده"))

    assert response.status_code == 200
    assert rebuilds == [False]


@pytest.mark.parametrize("job_status", [JobStatus.pending, JobStatus.rejected])
def test_only_a_record_in_the_corpus_may_be_edited_here(as_user, world, db, rebuilds, job_status):
    record = db.jobrecord.create(data={**COLUMNS, "status": job_status})

    response = as_user(world.root)("PUT", f"/admin/jobs/{record.id}",
                                   json=edited(job_title="عنوان دیگر"))
    assert response.status_code == 409
    assert job_status in response.json()["detail"]
    assert rebuilds == []


def test_a_missing_record_is_404(as_user, world, rebuilds):
    assert as_user(world.root)("PUT", "/admin/jobs/9999", json=edited()).status_code == 404
    assert rebuilds == []


def test_every_column_is_required(as_user, world, corpus, rebuilds):
    body = edited()
    del body["knowledge"]

    response = as_user(world.root)("PUT", f"/admin/jobs/{corpus[0].id}", json=body)
    assert response.status_code == 422
    assert "knowledge" in str(response.json()["detail"])
    assert rebuilds == []


@pytest.mark.parametrize("account", ["admin_a", "user_a1", "user_a2"])
def test_only_a_super_admin_may_edit_the_corpus(as_user, world, corpus, rebuilds, account):
    response = as_user(getattr(world, account))(
        "PUT", f"/admin/jobs/{corpus[0].id}", json=edited(job_title="عنوان دیگر"))

    assert response.status_code == 403
    assert rebuilds == []

"""`GET /admin/jobs` and `PUT /admin/jobs/{id}` — the panel that edits the corpus.

The moderation queue next door decides what *enters* the dataset; this pair is about
the records already in it, and the difference is load-bearing in both directions. An
approved record is what every organization searches, so editing one is a dataset edit
rather than a review — which is why it is a second endpoint confined to `approved`, and
why it is the third path (with approving and adding directly) that **starts a rebuild**.
A correction the search still answers from the old wording is the same failure as an
approved record nobody can find.
"""

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

# Titles chosen for what the listing has to get right, not for variety: they sort in a
# known order, two of them share a prefix so a search can be seen to narrow, and
# «برنامه‌نویسان سمت سرور» carries the ZWNJ that `_title_filters` exists for.
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
    """What the handlers asked the engine manager for, without starting a thread that
    would try to build a real engine out of a database this suite does not have."""
    calls = []
    monkeypatch.setattr(manager, "rebuild_async",
                        lambda force_embeddings=False: calls.append(force_embeddings) or True)
    return calls


@pytest.fixture
def corpus(db, world) -> list:
    """Five approved records, plus one pending and one rejected that must never show up
    in a listing this panel asks for."""
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


# ---------- listing ----------

def test_the_listing_holds_only_approved_records(as_user, world, corpus):
    body = as_user(world.root)("GET", "/admin/jobs").json()

    assert body["total"] == len(TITLES)
    assert [it["job_title"] for it in body["items"]] == TITLES  # ordered by title
    assert {it["status"] for it in body["items"]} == {"approved"}


def test_a_page_carries_the_whole_record(as_user, world, corpus):
    """The rows are complete on purpose: the editor opens on what the table already has,
    with no second request and no loading state inside the dialog."""
    first = as_user(world.root)("GET", "/admin/jobs").json()["items"][0]

    assert set(COLUMNS) <= set(first)
    assert first["responsibilities"] == COLUMNS["responsibilities"]
    assert first["updated_at"] is not None


def test_pagination_splits_the_corpus_and_total_stays_the_whole_of_it(as_user, world, corpus):
    page_1 = as_user(world.root)("GET", "/admin/jobs?page=1&page_size=2").json()
    page_3 = as_user(world.root)("GET", "/admin/jobs?page=3&page_size=2").json()

    assert [it["job_title"] for it in page_1["items"]] == TITLES[:2]
    assert [it["job_title"] for it in page_3["items"]] == TITLES[4:]
    # `total` is the size of the result, not of the page — it is what the pager is drawn
    # from, and a page that happens to be short must not shorten it.
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
    """A pager asking for page 0 has a bug in it; answering 422 would take the panel
    down over an off-by-one instead of showing the first page."""
    body = as_user(world.root)("GET", f"/admin/jobs?{params}").json()
    assert (body["page"], body["page_size"]) == (page, page_size)


def test_search_narrows_the_listing_and_the_pager_with_it(as_user, world, corpus):
    body = as_user(world.root)("GET", "/admin/jobs?q=برنامه‌نویسان").json()

    assert body["total"] == 2
    assert [it["job_title"] for it in body["items"]] == TITLES[1:3]


def test_a_title_typed_with_a_space_finds_the_zwnj_the_corpus_stores(as_user, world, corpus):
    """The corpus says «برنامه‌نویسان» because hazm's normalizer inserted the ZWNJ; an
    admin types «برنامه نویسان». `contains` is literal, so the query has to carry both."""
    body = as_user(world.root)("GET", "/admin/jobs?q=برنامه نویسان").json()
    assert body["total"] == 2


def test_arabic_letters_are_folded_onto_the_persian_ones(as_user, world, corpus):
    """«ي» and «ك» arrive from a paste and are different codepoints from the «ی»/«ک»
    the corpus is normalized to."""
    body = as_user(world.root)("GET", "/admin/jobs?q=پرستاران بخش ويژه").json()
    assert body["total"] == 1


def test_a_search_matching_nothing_is_an_empty_page(as_user, world, corpus):
    body = as_user(world.root)("GET", "/admin/jobs?q=خلبان").json()
    assert body == {"items": [], "total": 0, "page": 1, "page_size": 20}


def test_the_other_statuses_are_reachable_but_never_by_default(as_user, world, corpus):
    body = as_user(world.root)("GET", "/admin/jobs?job_status=pending").json()
    assert [it["job_title"] for it in body["items"]] == ["افسران پیشنهادی"]


@pytest.mark.parametrize("account", ["admin_a", "admin_a1", "user_a1"])
def test_only_a_super_admin_may_read_the_corpus(as_user, world, corpus, account):
    assert as_user(getattr(world, account))("GET", "/admin/jobs").status_code == 403


# ---------- editing ----------

def test_a_super_admin_edits_a_record_in_the_corpus(as_user, world, corpus, db, rebuilds):
    record = corpus[0]
    body = edited(job_title="افسران توپخانه و موشک", skills="هدایت آتش | کار با سامانه")
    response = as_user(world.root)("PUT", f"/admin/jobs/{record.id}", json=body)

    assert response.status_code == 200
    stored = db.jobrecord.find_unique(where={"id": record.id})
    assert stored.job_title == "افسران توپخانه و موشک"
    assert stored.skills == "هدایت آتش | کار با سامانه"
    # An edit is not a re-admission: it must not change what the record is or who let
    # it in.
    assert stored.status == JobStatus.approved
    assert stored.reviewed_by == world.root.id


def test_editing_a_record_starts_a_rebuild(as_user, world, corpus, rebuilds):
    """The third path that changes what a search can reach, and it pays the same price
    as the other two. Not forced: the embedding store is keyed on each record's text, so
    this encodes the texts of this record alone and reads the rest from the store."""
    response = as_user(world.root)("PUT", f"/admin/jobs/{corpus[0].id}",
                                   json=edited(description="شرح اصلاح‌شده"))

    assert response.status_code == 200
    assert rebuilds == [False]


@pytest.mark.parametrize("job_status", [JobStatus.pending, JobStatus.rejected])
def test_only_a_record_in_the_corpus_may_be_edited_here(as_user, world, db, rebuilds, job_status):
    """The two edit endpoints do not do each other's job. A pending record is reviewed
    through /admin/suggestions and a rejected one is closed; both answer 409 here rather
    than one endpoint quietly widening to cover every status."""
    record = db.jobrecord.create(data={**COLUMNS, "status": job_status})

    response = as_user(world.root)("PUT", f"/admin/jobs/{record.id}",
                                   json=edited(job_title="عنوان دیگر"))
    assert response.status_code == 409
    assert job_status in response.json()["detail"]
    # A refused edit must not spend a rebuild either.
    assert rebuilds == []


def test_a_missing_record_is_404(as_user, world, rebuilds):
    assert as_user(world.root)("PUT", "/admin/jobs/9999", json=edited()).status_code == 404
    assert rebuilds == []


def test_every_column_is_required(as_user, world, corpus, rebuilds):
    """The whole record is sent, not a patch — the same rule the suggestion form is held
    to, so an edit cannot drop a column by leaving it out."""
    body = edited()
    del body["knowledge"]

    response = as_user(world.root)("PUT", f"/admin/jobs/{corpus[0].id}", json=body)
    assert response.status_code == 422
    assert "knowledge" in str(response.json()["detail"])
    assert rebuilds == []


@pytest.mark.parametrize("account", ["admin_a", "admin_a1", "user_a1"])
def test_only_a_super_admin_may_edit_the_corpus(as_user, world, corpus, rebuilds, account):
    """Moderation is super-admin-only whatever authority an org or unit admin has inside
    their own tenancy: there is one shared corpus, and it is not an organization's."""
    response = as_user(getattr(world, account))(
        "PUT", f"/admin/jobs/{corpus[0].id}", json=edited(job_title="عنوان دیگر"))

    assert response.status_code == 403
    assert rebuilds == []

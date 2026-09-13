import pytest

from src.engine_manager import manager
from src.models import JobStatus

COLUMNS = {
    "job_title": "راهبر سامانه پایش",
    "aliases": "اپراتور پایش | ناظر سامانه",
    "tools": "سامانه پایش | بی‌سیم",
    "skills": "پایش پیوسته | گزارش‌نویسی",
    "knowledge": "سامانه‌های کنترلی | ایمنی",
    "abilities": "تمرکز بالا | دقت",
    "work_context": "اتاق کنترل | شیفت شبانه",
    "career_path_next": "سرپرست اتاق کنترل",
    "description": "پایش پیوسته سامانه‌ها و گزارش رویدادها",
    "responsibilities": "پایش سامانه | ثبت رویداد | گزارش خرابی",
}


def record(**overrides) -> dict:
    return {**COLUMNS, **overrides}


@pytest.fixture
def rebuilds(monkeypatch) -> list:
    calls = []
    monkeypatch.setattr(manager, "rebuild_async",
                        lambda force_embeddings=False: calls.append(force_embeddings) or True)
    return calls


@pytest.fixture
def owner(db, world):
    def _make(organization, status=JobStatus.pending, **overrides):
        return db.jobrecord.create(data={
            **record(**overrides), "status": status,
            "organization_id": None if organization is None else organization.id,
            "suggested_by": world.user_a1.id})

    return _make


# ── suggesting ────────────────────────────────────────────────────────────────

def test_a_suggestion_without_an_owner_is_public(as_user, world, db):
    response = as_user(world.user_a1)("POST", "/jobs/suggestions", json=record())

    assert response.status_code == 201
    assert response.json()["organization_id"] is None
    assert db.jobrecord.find_unique(where={"id": response.json()["id"]}).organization_id is None


def test_a_user_may_keep_a_suggestion_inside_their_own_organization(as_user, world, db):
    response = as_user(world.user_a1)(
        "POST", "/jobs/suggestions", json=record(organization_id=world.org_a.id))

    assert response.status_code == 201
    assert response.json()["organization_id"] == world.org_a.id
    assert response.json()["status"] == "pending"


def test_a_user_may_not_suggest_into_another_organization(as_user, world, db):
    response = as_user(world.user_a1)(
        "POST", "/jobs/suggestions", json=record(organization_id=world.org_b.id))

    assert response.status_code == 403
    assert db.jobrecord.count() == 0


def test_a_super_admin_may_suggest_into_any_organization(as_user, world):
    response = as_user(world.root)(
        "POST", "/jobs/suggestions", json=record(organization_id=world.org_b.id))

    assert response.status_code == 201
    assert response.json()["organization_id"] == world.org_b.id


def test_an_unknown_organization_is_404(as_user, world, db):
    response = as_user(world.root)("POST", "/jobs/suggestions",
                                   json=record(organization_id=9999))

    assert response.status_code == 404
    assert db.jobrecord.count() == 0


# ── moderation ────────────────────────────────────────────────────────────────

def test_an_org_admin_approves_their_own_organizations_suggestion(as_user, world, owner,
                                                                  db, rebuilds):
    pending = owner(world.org_a)

    response = as_user(world.admin_a)("POST", f"/admin/suggestions/{pending.id}/approve")

    assert response.status_code == 200
    stored = db.jobrecord.find_unique(where={"id": pending.id})
    assert stored.status == JobStatus.approved
    assert stored.reviewed_by == world.admin_a.id
    assert rebuilds == [False]


def test_an_org_admin_may_not_admit_a_record_into_the_public_corpus(as_user, world, owner,
                                                                    db, rebuilds):
    pending = owner(None)

    response = as_user(world.admin_a)("POST", f"/admin/suggestions/{pending.id}/approve")

    assert response.status_code == 403
    assert db.jobrecord.find_unique(where={"id": pending.id}).status == JobStatus.pending
    assert rebuilds == []


def test_an_org_admin_may_not_reach_another_organizations_suggestion(as_user, world, owner):
    pending = owner(world.org_b)

    assert as_user(world.admin_a)(
        "POST", f"/admin/suggestions/{pending.id}/reject").status_code == 403


def test_an_org_admin_adds_a_record_for_their_own_organization(as_user, world, db, rebuilds):
    response = as_user(world.admin_a)("POST", "/admin/jobs",
                                      json=record(organization_id=world.org_a.id))

    assert response.status_code == 201
    stored = db.jobrecord.find_unique(where={"id": response.json()["id"]})
    assert stored.status == JobStatus.approved
    assert stored.organization_id == world.org_a.id
    assert rebuilds == [False]


@pytest.mark.parametrize("organization", [None, "org_b"])
def test_an_org_admin_adds_nothing_outside_their_own_organization(as_user, world, db,
                                                                  rebuilds, organization):
    body = record() if organization is None else record(
        organization_id=getattr(world, organization).id)

    assert as_user(world.admin_a)("POST", "/admin/jobs", json=body).status_code == 403
    assert db.jobrecord.count() == 0
    assert rebuilds == []


def test_a_user_is_still_shut_out_of_moderation(as_user, world):
    assert as_user(world.user_a1)(
        "POST", "/admin/jobs", json=record(organization_id=world.org_a.id)).status_code == 403


# ── listing and filters ───────────────────────────────────────────────────────

@pytest.fixture
def three(owner, world):
    return {"public": owner(None, JobStatus.approved, job_title="راهبر عمومی"),
            "a": owner(world.org_a, JobStatus.approved, job_title="راهبر سازمان الف"),
            "b": owner(world.org_b, JobStatus.approved, job_title="راهبر سازمان ب")}


def test_a_super_admin_sees_every_record(as_user, world, three):
    body = as_user(world.root)("GET", "/admin/jobs").json()
    assert body["total"] == 3


def test_a_super_admin_filters_the_corpus_by_organization(as_user, world, three):
    body = as_user(world.root)("GET", f"/admin/jobs?organization_id={world.org_b.id}").json()

    assert [it["job_title"] for it in body["items"]] == [three["b"].job_title]


def test_a_super_admin_asks_for_the_public_corpus_alone(as_user, world, three):
    body = as_user(world.root)("GET", "/admin/jobs?public=true").json()

    assert [it["job_title"] for it in body["items"]] == [three["public"].job_title]


def test_the_two_filters_are_not_combined(as_user, world, three):
    response = as_user(world.root)(
        "GET", f"/admin/jobs?public=true&organization_id={world.org_a.id}")
    assert response.status_code == 422


def test_an_org_admin_asking_for_another_organization_is_told_nothing(as_user, world, three):
    body = as_user(world.admin_a)(
        "GET", f"/admin/jobs?organization_id={world.org_b.id}").json()
    assert body["items"] == []


def test_the_suggestion_queue_is_filtered_the_same_way(as_user, world, owner):
    mine, theirs = owner(world.org_a), owner(world.org_b)

    queue = as_user(world.admin_a)("GET", "/admin/suggestions").json()
    assert [it["id"] for it in queue] == [mine.id]

    queue = as_user(world.root)(
        "GET", f"/admin/suggestions?organization_id={theirs.organization_id}").json()
    assert [it["id"] for it in queue] == [theirs.id]


# ── moving a record between the two corpora ───────────────────────────────────

def test_a_super_admin_hands_a_public_record_to_an_organization(as_user, world, three,
                                                                db, rebuilds):
    response = as_user(world.root)(
        "PUT", f"/admin/jobs/{three['public'].id}",
        json=record(job_title="راهبر عمومی", organization_id=world.org_a.id))

    assert response.status_code == 200
    assert db.jobrecord.find_unique(
        where={"id": three["public"].id}).organization_id == world.org_a.id
    assert rebuilds == [False]


def test_a_super_admin_returns_a_record_to_the_public_corpus(as_user, world, three, db,
                                                             rebuilds):
    response = as_user(world.root)(
        "PUT", f"/admin/jobs/{three['a'].id}",
        json=record(job_title="راهبر سازمان الف", organization_id=None))

    assert response.status_code == 200
    assert db.jobrecord.find_unique(where={"id": three["a"].id}).organization_id is None


# The owner is not a column the edit form has to carry: a body that never mentions it
# leaves the record where it was, rather than publishing it to every organization.
def test_an_edit_that_never_mentions_the_owner_leaves_it_alone(as_user, world, three, db):
    response = as_user(world.root)("PUT", f"/admin/jobs/{three['a'].id}",
                                   json=record(job_title="راهبر سازمان الف",
                                               description="شرح اصلاح‌شده"))

    assert response.status_code == 200
    stored = db.jobrecord.find_unique(where={"id": three["a"].id})
    assert stored.organization_id == world.org_a.id
    assert stored.description == "شرح اصلاح‌شده"


def test_an_org_admin_may_not_publish_their_own_record(as_user, world, three, db, rebuilds):
    response = as_user(world.admin_a)(
        "PUT", f"/admin/jobs/{three['a'].id}",
        json=record(job_title="راهبر سازمان الف", organization_id=None))

    assert response.status_code == 403
    assert db.jobrecord.find_unique(
        where={"id": three["a"].id}).organization_id == world.org_a.id
    assert rebuilds == []


def test_an_org_admin_edits_their_own_organizations_record(as_user, world, three, db, rebuilds):
    response = as_user(world.admin_a)(
        "PUT", f"/admin/jobs/{three['a'].id}",
        json=record(job_title="راهبر سازمان الف", skills="پایش | گزارش",
                    organization_id=world.org_a.id))

    assert response.status_code == 200
    assert db.jobrecord.find_unique(where={"id": three["a"].id}).skills == "پایش | گزارش"
    assert rebuilds == [False]


def test_an_org_admin_may_not_edit_a_public_record(as_user, world, three, rebuilds):
    response = as_user(world.admin_a)(
        "PUT", f"/admin/jobs/{three['public'].id}",
        json=record(job_title="راهبر عمومی", organization_id=world.org_a.id))

    assert response.status_code == 403
    assert rebuilds == []


# ── deleting the organization takes its records with it ───────────────────────

def test_deleting_an_organization_deletes_its_records(as_user, world, three, db, rebuilds):
    for account in (world.admin_b, world.user_b1):
        db.user.delete(where={"id": account.id})

    response = as_user(world.root)("DELETE", f"/orgs/{world.org_b.id}")

    assert response.status_code == 204
    assert db.jobrecord.find_unique(where={"id": three["b"].id}) is None
    assert db.jobrecord.find_unique(where={"id": three["public"].id}) is not None
    assert rebuilds == [False]


def test_a_deletion_that_reaches_no_approved_record_starts_no_rebuild(as_user, world, db,
                                                                      owner, rebuilds):
    owner(world.org_b, JobStatus.pending)
    for account in (world.admin_b, world.user_b1):
        db.user.delete(where={"id": account.id})

    assert as_user(world.root)("DELETE", f"/orgs/{world.org_b.id}").status_code == 204
    assert rebuilds == []


def test_the_accounts_still_have_to_go_first(as_user, world, three, db):
    response = as_user(world.root)("DELETE", f"/orgs/{world.org_b.id}")

    assert response.status_code == 409
    assert db.jobrecord.find_unique(where={"id": three["b"].id}) is not None


def test_the_organization_list_counts_its_records(as_user, world, three):
    rows = {org["name"]: org for org in as_user(world.root)("GET", "/orgs").json()}

    assert rows["org-a"]["job_count"] == 1
    assert rows["org-b"]["job_count"] == 1


# A forced re-encode is a GPU hour and every organization's search rides on it, so the
# button stays the super admin's — but the status is readable by the admin whose own
# approval started a rebuild.
def test_only_a_super_admin_starts_a_rebuild(as_user, world, rebuilds):
    assert as_user(world.admin_a)("POST", "/admin/rebuild").status_code == 403
    assert rebuilds == []
    assert as_user(world.admin_a)("GET", "/admin/rebuild/status").status_code == 200
    assert as_user(world.root)("POST", "/admin/rebuild").status_code == 202

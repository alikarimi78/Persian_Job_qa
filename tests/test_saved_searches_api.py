import pytest

RESULT = {
    "mode": "single",
    "intent": "description",
    "answer": "پرستار بخش ویژه از بیماران بدحال مراقبت می‌کند.",
    "job": "پرستاران بخش ویژه",
    "score": 0.82,
    "details": [{
        "job_title": "پرستاران بخش ویژه",
        "fields": [
            {"key": "description", "label": "شرح شغل", "value": "مراقبت از بیماران بدحال",
             "items": [], "primary": True, "preview": 0},
            {"key": "skills", "label": "مهارت‌ها", "value": "پایش علائم حیاتی",
             "items": ["پایش علائم حیاتی", "احیای قلبی ریوی"], "primary": False, "preview": 5},
        ],
    }],
    "related_jobs": ["بهیاران"],
}


def saved_body(question="وظایف پرستار بخش ویژه چیست؟", **result):
    return {"question": question, "result": {**RESULT, **result}}


@pytest.fixture
def star(as_user, world):
    def _star(user, **kwargs):
        response = as_user(user)("POST", "/saved", json=saved_body(**kwargs))
        assert response.status_code == 201, response.text
        return response.json()

    return _star


def test_a_star_keeps_the_answer_it_was_set_on(star, as_user, world):
    row = star(world.user_a1)

    assert row["question"] == "وظایف پرستار بخش ویژه چیست؟"
    assert row["mode"] == "single"
    assert row["job_title"] == "پرستاران بخش ویژه"

    opened = as_user(world.user_a1)("GET", f"/saved/{row['id']}").json()
    assert opened["result"]["answer"] == RESULT["answer"]
    assert opened["result"]["details"][0]["fields"][1]["items"] == ["پایش علائم حیاتی", "احیای قلبی ریوی"]


def test_the_listing_is_the_readers_own_newest_first(star, as_user, world):
    star(world.user_a1, question="سوال اول")
    second = star(world.user_a1, question="سوال دوم")
    star(world.user_b1, question="سوال کاربر دیگر")

    body = as_user(world.user_a1)("GET", "/saved").json()

    assert [it["question"] for it in body["items"]] == ["سوال دوم", "سوال اول"]
    assert body["total"] == 2
    assert body["items"][0]["id"] == second["id"]


def test_another_readers_star_is_absent_rather_than_refused(star, as_user, world):
    row = star(world.user_b1)

    assert as_user(world.user_a1)("GET", f"/saved/{row['id']}").status_code == 404
    assert as_user(world.user_a1)("DELETE", f"/saved/{row['id']}").status_code == 404
    assert as_user(world.user_b1)("GET", f"/saved/{row['id']}").status_code == 200


def test_starring_the_same_question_again_refreshes_it(star, as_user, world, db):
    first = star(world.user_a1)
    again = star(world.user_a1, answer="پاسخ تازه‌تر", job="پرستاران بخش ویژه")

    assert again["id"] == first["id"]
    assert db.savedsearch.count(where={"user_id": world.user_a1.id}) == 1
    opened = as_user(world.user_a1)("GET", f"/saved/{first['id']}").json()
    assert opened["result"]["answer"] == "پاسخ تازه‌تر"


def test_a_star_is_removed_by_its_owner(star, as_user, world, db):
    row = star(world.user_a1)

    assert as_user(world.user_a1)("DELETE", f"/saved/{row['id']}").status_code == 204
    assert db.savedsearch.count(where={"user_id": world.user_a1.id}) == 0
    assert as_user(world.user_a1)("GET", "/saved").json()["total"] == 0


def test_the_page_is_clamped_rather_than_refused(star, as_user, world):
    star(world.user_a1)
    body = as_user(world.user_a1)("GET", "/saved?page=0&page_size=5000").json()
    assert (body["page"], body["page_size"]) == (1, 100)


def test_every_role_keeps_its_own_stars(star, as_user, world):
    for account in (world.root, world.admin_a, world.user_a1):
        star(account, question=f"سوال {account.username}")

    for account in (world.root, world.admin_a, world.user_a1):
        body = as_user(account)("GET", "/saved").json()
        assert [it["question"] for it in body["items"]] == [f"سوال {account.username}"]


def test_a_question_too_long_or_empty_is_refused(as_user, world):
    for question in ("", "پ" * 501):
        response = as_user(world.user_a1)("POST", "/saved", json=saved_body(question=question))
        assert response.status_code == 422


def test_an_anonymous_caller_reaches_none_of_it(client, world, star):
    row = star(world.user_a1)
    for method, url in [("GET", "/saved"), ("POST", "/saved"),
                        ("GET", f"/saved/{row['id']}"), ("DELETE", f"/saved/{row['id']}")]:
        assert client.request(method, url, json=saved_body()).status_code == 401


# The rows mean nothing without the account, so the database takes them with it rather than
# `accounts.delete` having to remember a second table.
def test_deleting_the_account_takes_its_stars(star, as_user, world, db):
    star(world.user_a1)

    assert as_user(world.root)("DELETE", f"/accounts/{world.user_a1.id}").status_code == 204
    assert db.savedsearch.count() == 0

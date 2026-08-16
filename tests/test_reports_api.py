"""`POST /reports/search` — the PDF download.

Unlike the rest of the suite this one exercises a renderer rather than a permission
rule, so the assertions are about the *document*: that a PDF comes back at all, that
its header names the caller from the token rather than from the body, and that the
things which are only wrong once they reach a page — an emoji with no glyph, an
unbounded payload — do not reach one. `build_html` is asserted on directly wherever
the question is what the report says, because a WeasyPrint render is ~0.5 s and the
text is already decided by then.

WeasyPrint imports at collection here, which the stubbed `job_qa_service` does not
cover: it is a real dependency of `app.routers.reports` and needs libpango present.
That is the same requirement the container has (see the Dockerfile).
"""

import pytest

from app.reports import build_html, filename, printable, render_pdf
from app.reports.jalali import fa_digits, format_datetime, to_jalali
from app.schemas import ReportIn
from datetime import datetime, timezone


def field(key, label, value="", items=(), primary=False):
    return {"key": key, "label": label, "value": value,
            "items": list(items), "primary": primary}


def payload(**overrides):
    body = {
        "question": "وظایف افسر توپخانه چیست؟",
        "mode": "single",
        "answer": "افسران توپخانه فرماندهی آتش پشتیبانی را بر عهده دارند.",
        "job": "افسران توپخانه و موشک",
        "details": [{
            "job_title": "افسران توپخانه و موشک",
            "fields": [
                field("responsibilities", "وظایف و مسئولیت‌ها",
                      items=["فرماندهی خدمه توپخانه.", "نظارت بر آموزش پرسنل."], primary=True),
                field("work_context", "محیط کاری", value="کار در فضای باز و مواضع پیش‌رونده."),
                field("skills", "مهارت‌ها و شایستگی‌ها", items=["رهبری", "محاسبات بالستیک"]),
            ],
        }],
    }
    body.update(overrides)
    return body


class Caller:
    """Stands in for the User row the renderer reads a name off.

    `display_name` mirrors `models.User`: the person's name if the account has one, and
    the username for an account created before migration 0007 gave them names."""
    def __init__(self, username="a.karimi", first_name=None, last_name=None):
        self.username = username
        self.first_name = first_name
        self.last_name = last_name

    @property
    def display_name(self):
        parts = [part for part in (self.first_name, self.last_name) if part]
        return " ".join(parts) if parts else self.username


# ---------- the endpoint ----------
def test_report_returns_a_pdf(as_user, world):
    response = as_user(world.user_a1)("POST", "/reports/search", json=payload())

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/pdf"
    assert response.content.startswith(b"%PDF-")
    assert "attachment" in response.headers["content-disposition"]


def test_filename_carries_the_jalali_date():
    moment = datetime(2026, 8, 6, 9, 0, tzinfo=timezone.utc)
    assert filename(moment) == "job-report-1405-05-15.pdf"


def test_report_requires_a_token(client):
    assert client.post("/reports/search", json=payload()).status_code == 401


def test_blocked_account_cannot_download(as_user, world, db):
    world.user_a1.is_active = False
    db.commit()
    assert as_user(world.user_a1)("POST", "/reports/search",
                                  json=payload()).status_code == 403


def test_every_role_may_download(as_user, world):
    """The report prints an answer, and every provisioned account may search."""
    for account in (world.root, world.admin_a, world.admin_a1, world.user_a1):
        response = as_user(account)("POST", "/reports/search", json=payload())
        assert response.status_code == 200, account.username


# ---------- what the page says ----------
def test_header_names_the_caller_not_the_body(as_user, world, db):
    """Identity comes from the token. The body has no field for it, and adding one
    would let a user issue a report over somebody else's name and unit."""
    world.user_a1.first_name, world.user_a1.last_name = "زهرا", "کریمی"
    db.commit()
    html = build_html(ReportIn(**payload()), world.user_a1, "org-a", "unit-a1")

    assert "org-a" in html and "unit-a1" in html


def test_the_masthead_prints_the_person_not_the_username(as_user, world, db):
    """A report is read away from the system, where «user-a1» identifies nobody."""
    world.user_a1.first_name, world.user_a1.last_name = "زهرا", "کریمی"
    db.commit()
    html = build_html(ReportIn(**payload()), world.user_a1, "org-a", "unit-a1")

    assert "زهرا کریمی" in html
    assert "user-a1" not in html


def test_an_account_with_no_name_still_prints_as_something(world):
    """Accounts that predate migration 0007 have neither column filled in; the masthead
    falls back to the username rather than to a blank line."""
    html = build_html(ReportIn(**payload()), world.user_a1, None, None)

    assert "user-a1" in html


def test_answer_and_every_field_reach_the_page():
    html = build_html(ReportIn(**payload()), Caller(), None, None)

    assert "افسران توپخانه فرماندهی آتش پشتیبانی را بر عهده دارند." in html
    for label in ("وظایف و مسئولیت‌ها", "محیط کاری", "مهارت‌ها و شایستگی‌ها"):
        assert label in html
    assert "محاسبات بالستیک" in html          # a folded box on screen is printed here


def test_generated_record_is_marked_as_unregistered():
    """A proposal is not a corpus record. Without the notice the report reads exactly
    like one describing a job the database already holds."""
    html = build_html(ReportIn(**payload(mode="job_generated")), Caller(), None, None)

    assert "هنوز در پایگاه داده ثبت نشده" in html


def test_matched_job_is_not_listed_as_its_own_neighbour():
    """`related_jobs` leads with the match itself on the discovery path; the client
    drops it and the report has to agree."""
    html = build_html(
        ReportIn(**payload(mode="job_match",
                           related_jobs=["افسران توپخانه و موشک", "خدمه توپخانه و موشک"])),
        Caller(), None, None)
    related = next(line for line in html.splitlines() if 'class="related"' in line)

    assert "خدمه توپخانه و موشک" in related
    assert "افسران توپخانه و موشک" not in related


def test_out_of_domain_report_has_no_job_section():
    html = build_html(ReportIn(**payload(mode="out_of_domain", job=None, details=[])),
                      Caller(), None, None)

    assert "خارج از دامنه" in html
    assert "مشخصات شغل" not in html


# ---------- emoji: rendered as a hex box, and not even in the right block ----------
@pytest.mark.parametrize("text, expected", [
    ("📌 افسران توپخانه", "افسران توپخانه"),           # the leading space goes too
    ("🔗 نقش تلفیقی: الف + ب", "نقش تلفیقی: الف + ب"),
    ("می‌شود", "می‌شود"),                               # ZWNJ has no glyph and must stay
    ("خط اول\nخط دوم", "خط اول\nخط دوم"),
])
def test_printable_drops_only_what_the_font_cannot_draw(text, expected):
    assert printable(text) == expected


def test_emoji_never_reaches_the_document():
    html = build_html(ReportIn(**payload(answer="📌 افسران توپخانه\n\nشرح: الف")),
                      Caller(), None, None)

    assert "📌" not in html
    assert "افسران توپخانه" in html


# ---------- the payload is untrusted, and bounded ----------
def test_oversized_answer_is_refused(as_user, world):
    """The body is the caller's own answer coming back, but nothing proves this server
    wrote it — so the size of what a download can be made to render is capped."""
    response = as_user(world.user_a1)("POST", "/reports/search",
                                      json=payload(answer="ا" * 20_001))
    assert response.status_code == 422


def test_too_many_detail_sections_are_refused(as_user, world):
    detail = payload()["details"][0]
    response = as_user(world.user_a1)("POST", "/reports/search",
                                      json=payload(details=[detail] * 5))
    assert response.status_code == 422


def test_empty_question_is_refused(as_user, world):
    assert as_user(world.user_a1)("POST", "/reports/search",
                                  json=payload(question="")).status_code == 422


# ---------- the date on the page ----------
@pytest.mark.parametrize("gregorian, jalali", [
    ((2026, 8, 6), (1405, 5, 15)),
    ((2026, 3, 21), (1405, 1, 1)),      # Nowruz
    ((2026, 3, 20), (1404, 12, 29)),    # the day before it, in the previous year
    ((2024, 2, 29), (1402, 12, 10)),    # a Gregorian leap day
])
def test_jalali_conversion(gregorian, jalali):
    assert to_jalali(*gregorian) == jalali


def test_report_is_dated_in_tehran_not_utc():
    """22:00 UTC is already the next day in Iran. Read off the container's clock the
    report would be stamped a day early every evening."""
    late = datetime(2026, 8, 6, 22, 0, tzinfo=timezone.utc)
    assert format_datetime(late).startswith(f"{fa_digits(16)} مرداد")


def test_the_pdf_itself_renders_for_every_mode():
    """The one place a real WeasyPrint render is worth its half-second: a template
    error only shows up here, not in the HTML."""
    for mode in ("single", "job_match", "job_generated", "interdisciplinary",
                 "out_of_domain"):
        pdf = render_pdf(ReportIn(**payload(mode=mode)), Caller(), "org-a", "unit-a1")
        assert pdf.startswith(b"%PDF-"), mode

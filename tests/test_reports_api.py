import pytest

from src.reports import build_html, filename, printable, render_pdf
from src.reports.jalali import fa_digits, format_datetime, to_jalali
from src.schemas import ReportIn
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
    def __init__(self, username="a.karimi", first_name=None, last_name=None):
        self.username = username
        self.first_name = first_name
        self.last_name = last_name


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
    db.user.update(where={"id": world.user_a1.id}, data={"is_active": False})
    assert as_user(world.user_a1)("POST", "/reports/search",
                                  json=payload()).status_code == 403


def test_every_role_may_download(as_user, world):
    for account in (world.root, world.admin_a, world.user_a1):
        response = as_user(account)("POST", "/reports/search", json=payload())
        assert response.status_code == 200, account.username


def test_header_names_the_caller_not_the_body(as_user, world, db):
    named = db.user.update(where={"id": world.user_a1.id},
                           data={"first_name": "زهرا", "last_name": "کریمی"})
    html = build_html(ReportIn(**payload()), named, "org-a")

    assert "org-a" in html


def test_the_masthead_prints_the_person_not_the_username(as_user, world, db):
    named = db.user.update(where={"id": world.user_a1.id},
                           data={"first_name": "زهرا", "last_name": "کریمی"})
    html = build_html(ReportIn(**payload()), named, "org-a")

    assert "زهرا کریمی" in html
    assert "user-a1" not in html


def test_an_account_with_no_name_still_prints_as_something(world):
    html = build_html(ReportIn(**payload()), world.user_a1, None, None)

    assert "user-a1" in html


def test_answer_and_every_field_reach_the_page():
    html = build_html(ReportIn(**payload()), Caller(), None, None)

    assert "افسران توپخانه فرماندهی آتش پشتیبانی را بر عهده دارند." in html
    for label in ("وظایف و مسئولیت‌ها", "محیط کاری", "مهارت‌ها و شایستگی‌ها"):
        assert label in html
    assert "محاسبات بالستیک" in html


def test_generated_record_is_marked_as_unregistered():
    html = build_html(ReportIn(**payload(mode="job_generated")), Caller(), None, None)

    assert "هنوز در پایگاه داده ثبت نشده" in html


def test_matched_job_is_not_listed_as_its_own_neighbour():
    html = build_html(
        ReportIn(**payload(mode="job_match",
                           related_jobs=["افسران توپخانه و موشک", "خدمه توپخانه و موشک"])),
        Caller(), None, None)
    related = next(line for line in html.splitlines() if 'class="related"' in line)

    assert "خدمه توپخانه و موشک" in related
    assert "افسران توپخانه و موشک" not in related


def test_a_single_answer_names_the_job_it_was_asked_about():
    html = build_html(
        ReportIn(**payload(related_jobs=["افسران توپخانه و موشک", "خدمه توپخانه و موشک"])),
        Caller(), None, None)
    related = next(line for line in html.splitlines() if 'class="related"' in line)

    assert "شغل مورد پرسش" in html
    assert "خدمه توپخانه و موشک" in related
    assert "افسران توپخانه و موشک" not in related


def test_a_composed_record_is_marked_as_unregistered():
    html = build_html(
        ReportIn(**payload(mode="job_adapted", job="تعمیرکار پهپاد کشاورزی",
                           related_jobs=["اپراتورهای پهپاد تاکتیکی"])),
        Caller(), None, None)
    related = next(line for line in html.splitlines() if 'class="related"' in line)

    assert "تعمیرکار پهپاد کشاورزی" in html
    assert "در پایگاه داده ثبت نشده" in html
    # The record was composed, so every retrieved neighbour is still a corpus title the
    # reader should see — none of them is the job the report is about.
    assert "اپراتورهای پهپاد تاکتیکی" in related


def test_out_of_domain_report_has_no_job_section():
    html = build_html(ReportIn(**payload(mode="out_of_domain", job=None, details=[])),
                      Caller(), None, None)

    assert "خارج از دامنه" in html
    assert "مشخصات شغل" not in html


@pytest.mark.parametrize("text, expected", [
    ("📌 افسران توپخانه", "افسران توپخانه"),
    ("🔗 نقش تلفیقی: الف + ب", "نقش تلفیقی: الف + ب"),
    ("می‌شود", "می‌شود"),
    ("خط اول\nخط دوم", "خط اول\nخط دوم"),
])
def test_printable_drops_only_what_the_font_cannot_draw(text, expected):
    assert printable(text) == expected


def test_emoji_never_reaches_the_document():
    html = build_html(ReportIn(**payload(answer="📌 افسران توپخانه\n\nشرح: الف")),
                      Caller(), None, None)

    assert "📌" not in html
    assert "افسران توپخانه" in html


def test_a_preview_carrying_payload_still_prints_every_item(as_user, world):
    body = payload()
    for box in body["details"][0]["fields"]:
        box["preview"] = 1
    html = build_html(ReportIn(**body), Caller(), "org-a")
    assert "فرماندهی خدمه توپخانه." in html
    assert "نظارت بر آموزش پرسنل." in html


def test_oversized_answer_is_refused(as_user, world):
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


@pytest.mark.parametrize("gregorian, jalali", [
    ((2026, 8, 6), (1405, 5, 15)),
    ((2026, 3, 21), (1405, 1, 1)),
    ((2026, 3, 20), (1404, 12, 29)),
    ((2024, 2, 29), (1402, 12, 10)),
])
def test_jalali_conversion(gregorian, jalali):
    assert to_jalali(*gregorian) == jalali


def test_report_is_dated_in_tehran_not_utc():
    late = datetime(2026, 8, 6, 22, 0, tzinfo=timezone.utc)
    assert format_datetime(late).startswith(f"{fa_digits(16)} مرداد")


def test_the_pdf_itself_renders_for_every_mode():
    for mode in ("single", "job_match", "job_adapted", "job_generated",
                 "interdisciplinary", "out_of_domain"):
        pdf = render_pdf(ReportIn(**payload(mode=mode)), Caller(), "org-a")
        assert pdf.startswith(b"%PDF-"), mode

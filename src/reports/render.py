import logging
import threading
from datetime import datetime
from functools import lru_cache
from pathlib import Path

from fontTools.ttLib import TTFont
from jinja2 import Environment, FileSystemLoader, select_autoescape
from weasyprint import HTML

from ..models import display_name
from .jalali import date_slug, fa_digits, format_datetime, now

log = logging.getLogger("reports")

_HERE = Path(__file__).parent
_ASSETS = _HERE / "assets"

TITLE = "سامانه تحلیل مشاغل"
SUBTITLE = "گزارش تحلیل شغل"
COLOPHON = "این گزارش به‌صورت خودکار از پاسخ سامانه تحلیل مشاغل تولید شده است."

SENTENCE_LISTS = {"responsibilities"}

# What the line under the question calls the job the answer is about — and, through
# `_related`, the set of modes whose subject is dropped from the neighbour list. Both
# `single` and `job_adapted` name the job the *user* asked about (the engine resolves
# the question to one before answering), so both belong here; `about` and
# `out_of_domain` carry no job at all.
MATCHED_LABEL = {
    "single": "شغل مورد پرسش",
    "job_match": "شغل منطبق",
    "job_adapted": "شغل مورد پرسش",
    "job_generated": "شغل پیشنهادی",
    "interdisciplinary": "مشاغل منطبق",
}

NOTICE = {
    "job_generated": "این شغل هنوز در پایگاه داده ثبت نشده است؛ آنچه در ادامه می‌آید "
                     "پیشنهاد سامانه است و تا تایید مدیر سامانه، بخشی از پایگاه داده "
                     "به شمار نمی‌رود.",
    "job_adapted": "این شغل در پایگاه داده ثبت نشده است؛ مشخصات آن بر اساس پرسش شما و "
                   "نزدیک‌ترین رکوردهای پایگاه داده تدوین شده و بخشی از پایگاه داده به "
                   "شمار نمی‌رود.",
    "out_of_domain": "این پرسش خارج از دامنه مشاغل سامانه تشخیص داده شد، بنابراین "
                     "گزارش شامل مشخصات هیچ شغلی نیست.",
}

_ALWAYS_KEEP = frozenset("\n\r\t  ‌‍‎‏")


@lru_cache(maxsize=1)
def _drawable() -> frozenset[int]:
    with TTFont(_ASSETS / "Vazirmatn-Regular.ttf") as font:
        return frozenset(font.getBestCmap())


def printable(value):
    if not isinstance(value, str):
        return value
    kept = "".join(c for c in value if c in _ALWAYS_KEEP or ord(c) in _drawable())
    return "\n".join(line.strip() for line in kept.split("\n"))


_environment = Environment(
    loader=FileSystemLoader(_HERE),
    autoescape=select_autoescape(["html"]),
    finalize=printable,
)
_environment.filters["fa"] = fa_digits
_environment.globals["SENTENCE_LISTS"] = SENTENCE_LISTS

_render_lock = threading.Lock()


_ASSET_BASE = f"{_ASSETS.as_uri()}/"


def _matched_titles(report) -> list[str]:
    if report.jobs:
        return list(report.jobs)
    if report.job:
        return [report.job]
    if report.mode == "job_generated" and report.details:
        return [report.details[0].job_title]
    return []


def _related(report, matched: list[str]) -> list[str]:
    seen = set(matched)
    return [title for title in (report.related_jobs or []) if title not in seen]


def build_html(report, user, organization: str | None,
               moment: datetime | None = None) -> str:
    moment = moment or now()
    matched = _matched_titles(report)
    template = _environment.get_template("template.html")
    return template.render(
        title=TITLE,
        subtitle=SUBTITLE,
        colophon=COLOPHON,
        issued_at=format_datetime(moment),
        person=display_name(user),
        organization=organization,
        question=report.question,
        answer=report.answer,
        matched="، ".join(matched) if report.mode in MATCHED_LABEL else "",
        matched_label=MATCHED_LABEL.get(report.mode, ""),
        notice=NOTICE.get(report.mode),
        details=report.details,
        related_jobs=_related(report, matched),
    )


def render_pdf(report, user, organization: str | None) -> bytes:
    moment = now()
    html = build_html(report, user, organization, moment)
    with _render_lock:
        return HTML(string=html, base_url=_ASSET_BASE).write_pdf()


def filename(moment: datetime | None = None) -> str:
    return f"job-report-{date_slug(moment or now())}.pdf"

# -*- coding: utf-8 -*-
"""HTML -> PDF for the search report.

WeasyPrint rather than a PDF-drawing library because the document is Persian: the
shaping and the right-to-left run order come from Pango/HarfBuzz for free, where a
canvas API would need every line reshaped and positioned by hand. The price is four
system libraries in the image (see the Dockerfile) — paid once, against a template
that is ordinary CSS from then on.

The report is rendered from the payload the *client* posts back, not from a fresh
search: the answer is one LLM call and asking again would both spend another and risk
printing prose the user never saw. Everything here is therefore untrusted input, which
is why the template autoescapes and `src/schemas.py:ReportIn` bounds every string.
"""

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

# Which list columns are sentences rather than short labels, and so get the full width
# instead of two columns. The client's `JobDetails.jsx:LIST_AS_LINES` makes the same
# split on screen; keep the two together.
SENTENCE_LISTS = {"responsibilities"}

# What the record under the question is, per answer mode. `single` says nothing — the
# question was about that job, so naming it again above the answer is noise.
MATCHED_LABEL = {
    "job_match": "شغل منطبق",
    "job_generated": "شغل پیشنهادی",
    "interdisciplinary": "مشاغل منطبق",
}

# A record the engine designed has not been registered anywhere. Without this the
# report reads exactly like one describing a job the corpus already holds.
NOTICE = {
    "job_generated": "این شغل هنوز در پایگاه داده ثبت نشده است؛ آنچه در ادامه می‌آید "
                     "پیشنهاد سامانه است و تا تایید مدیر سامانه، بخشی از پایگاه داده "
                     "به شمار نمی‌رود.",
    "out_of_domain": "این پرسش خارج از دامنه مشاغل سامانه تشخیص داده شد، بنابراین "
                     "گزارش شامل مشخصات هیچ شغلی نیست.",
}

# Characters kept whether or not the font draws them: the shaping controls Persian needs
# (ZWNJ is what makes «می‌شود» one word and two shapes) and ordinary whitespace. A cmap
# has no entry for these, and dropping them would rewrite the text rather than clean it.
_ALWAYS_KEEP = frozenset("\n\r\t  ‌‍‎‏")


@lru_cache(maxsize=1)
def _drawable() -> frozenset[int]:
    """Every codepoint the report font actually has a glyph for."""
    with TTFont(_ASSETS / "Vazirmatn-Regular.ttf") as font:
        return frozenset(font.getBestCmap())


def printable(value):
    """Drops characters the font cannot draw, which in practice means emoji.

    The engine decorates its template answers with 📌 and 🔗 (`job_qa_service/render.py`),
    and a model asked for prose will reach for more of them. Vazirmatn has none, so Pango
    substitutes a hex box — and worse, that box does not even stay in its own block: a 🔗
    at the head of the answer came out beside the heading two elements above it. A report
    is not the place for them regardless, so they are removed rather than fonted around.

    Wired in as the environment's `finalize`, so it covers every `{{ }}` in the template
    including ones added later, instead of a filter each one has to remember.
    """
    if not isinstance(value, str):
        return value
    kept = "".join(c for c in value if c in _ALWAYS_KEEP or ord(c) in _drawable())
    # Line by line, because a stripped 📌 leaves the line it introduced starting with a
    # space, and the answer is laid out with `white-space: pre-wrap`.
    return "\n".join(line.strip() for line in kept.split("\n"))


_environment = Environment(
    loader=FileSystemLoader(_HERE),
    autoescape=select_autoescape(["html"]),
    finalize=printable,
)
_environment.filters["fa"] = fa_digits
_environment.globals["SENTENCE_LISTS"] = SENTENCE_LISTS

# One render at a time. `@font-face` registration goes through process-global fontconfig
# state, which WeasyPrint does not document as thread-safe, and the endpoint hands its
# render to `run_in_threadpool` — so two downloads at once would otherwise overlap there.
# A render is ~0.5 s and a report is a rare action beside a search, so the queue this can
# form is cheaper than the class of bug it rules out.
_render_lock = threading.Lock()


# The document names its font and logo by bare filename and WeasyPrint resolves them
# against this. Keeping the paths out of the template is not only tidier: `finalize`
# below rewrites every value the template interpolates, and a path is the one kind of
# string that must survive byte for byte.
_ASSET_BASE = f"{_ASSETS.as_uri()}/"


def _matched_titles(report) -> list[str]:
    """The job(s) the answer is about: two for an interdisciplinary answer, and for a
    generated one the proposed title, which rides in `details` rather than in `job`."""
    if report.jobs:
        return list(report.jobs)
    if report.job:
        return [report.job]
    if report.mode == "job_generated" and report.details:
        return [report.details[0].job_title]
    return []


def _related(report, matched: list[str]) -> list[str]:
    """`related_jobs` leads with the matched record itself on the discovery path, and
    printing a job as its own neighbour reads as a bug. The client drops it the same
    way (`Search.jsx`); the two lists have to agree, since the report claims to be
    what was on screen."""
    seen = set(matched)
    return [title for title in (report.related_jobs or []) if title not in seen]


def build_html(report, user, organization: str | None, unit: str | None,
               moment: datetime | None = None) -> str:
    """The report as HTML, still needing `_ASSET_BASE` to resolve its font and logo.

    Split out from `render_pdf` so a test can assert on what the page says without
    WeasyPrint's 0.5 s in the way — and so the layout can be opened in a browser, by
    writing it into `src/reports/assets/` where the relative URLs resolve.
    """
    moment = moment or now()
    matched = _matched_titles(report)
    template = _environment.get_template("template.html")
    return template.render(
        title=TITLE,
        subtitle=SUBTITLE,
        colophon=COLOPHON,
        issued_at=format_datetime(moment),
        # The person, not the credential: a report is read away from the system, where
        # «a.karimi» names nobody. `display_name` falls back to the username for an
        # account created before the name columns existed. It is a function over the
        # account rather than a property on it, because a Prisma model is regenerated
        # from `schema.prisma` and cannot carry one.
        person=display_name(user),
        organization=organization,
        unit=unit,
        question=report.question,
        answer=report.answer,
        matched="، ".join(matched) if report.mode in MATCHED_LABEL else "",
        matched_label=MATCHED_LABEL.get(report.mode, ""),
        notice=NOTICE.get(report.mode),
        details=report.details,
        related_jobs=_related(report, matched),
    )


def render_pdf(report, user, organization: str | None, unit: str | None) -> bytes:
    moment = now()
    html = build_html(report, user, organization, unit, moment)
    with _render_lock:
        return HTML(string=html, base_url=_ASSET_BASE).write_pdf()


def filename(moment: datetime | None = None) -> str:
    """`job-report-1405-05-15.pdf`. ASCII on purpose: the Persian title of the job would
    have to be RFC 5987-encoded to survive a Content-Disposition header, and browsers
    disagree about the result. The date is the Jalali one shown inside the report, so
    the file on disk and its first page agree."""
    return f"job-report-{date_slug(moment or now())}.pdf"

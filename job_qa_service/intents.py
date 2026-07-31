# -*- coding: utf-8 -*-
"""What the user is asking for, decided before retrieval runs.

Two independent questions live here: *is this a request for a job* (`is_job_request`,
which routes to the discovery path), and if not, *which columns answer it*
(`detect_intent` -> `INTENT_TO_FIELDS`).
"""

import re

INTENT_TO_FIELDS = {
    "description": ["description"], "responsibilities": ["responsibilities"],
    # Persian «شایستگی/توانایی» covers learned skills and innate abilities alike,
    # so this intent answers from both columns rather than picking one.
    "competencies": ["skills", "abilities"], "knowledge": ["knowledge"],
    "tools": ["tools"], "career_path": ["career_path_next"],
    "work_context": ["work_context"], "aliases": ["aliases"],
    "general": ["description", "responsibilities", "skills", "knowledge", "abilities", "tools"],
}

INTENT_KEYWORDS = {
    "responsibilities": ["وظایف", "وظیفه", "مسئولیت", "روزمره", "کارها", "چه کاری", "چیکار", "چه کار"],
    "tools":            ["ابزار", "نرم‌افزار", "نرم افزار", "برنامه", "تجهیزات", "سیستم", "با چی"],
    "competencies":     ["مهارت", "شایستگی", "توانایی", "توانمندی", "ویژگی", "دقت", "استعداد", "باید بلد"],
    # Multi-word forms on purpose: a bare «دانش» also matches دانشگاه/دانشجو/دانش‌آموز.
    # Listed after competencies so «مهارت و دانش لازم» still answers from that pair.
    "knowledge":        ["چه دانشی", "دانش لازم", "دانش مورد نیاز", "دانش تخصصی",
                         "دانش فنی", "معلومات", "باید بداند", "چه بداند"],
    "career_path":      ["ارتقا", "ارتقاء", "آینده", "پیشرفت", "مسیر", "بعدش", "ترفیع", "رشد"],
    "work_context":     ["محیط", "فضا", "شرایط کاری", "کجا کار", "محل کار"],
    "aliases":          ["نام دیگر", "اسم دیگر", "معادل"],
    "description":      ["معرفی", "شرح", "چیست", "توضیح", "درباره", "چیه"],
}

EXPLICIT_COMBO_WORDS = ["بین رشته", "بین‌رشته", "ترکیب", "هر دو", "هردو", "تلفیق", "میان‌رشته"]
QUESTION_WORDS = {"چیست", "چیه", "چطور", "چگونه", "کدام", "چند", "چی", "کجا", "آیا"}

# The user is describing a job they want rather than asking about a known one.
# Both ZWNJ and plain-space spellings are listed because user input is not always
# normalized the same way hazm normalizes the corpus (it joins «بچه ها» but leaves
# «میخوام» alone, so both spellings genuinely reach here).
#
# These literal phrases are the floor, not the whole test: _JOB_REQUEST_RE below
# generalizes them. Everything listed here is an idiom that no pattern would catch.
JOB_REQUEST_KEYWORDS = [
    "شغلی می‌خواهم", "شغلی میخواهم", "شغلی می خواهم", "شغلی میخوام",
    "شغل می‌خواهم", "شغل میخواهم", "شغل میخوام",
    "کاری می‌خواهم", "کاری میخواهم", "کاری میخوام",
    "دنبال شغلی", "دنبال شغل", "دنبال کاری", "به دنبال شغل",
    "چه شغلی", "چه کاری مناسب", "کدام شغل", "کدوم شغل",
    "شغلی پیشنهاد", "شغل پیشنهاد", "کاری پیشنهاد", "شغلی معرفی", "شغل معرفی کن",
    "شغلی هست که", "شغلی وجود دارد", "شغلی وجود داره", "شغلی سراغ",
    "مناسب من", "برای من مناسب", "به من می‌آید", "به من میاد",
    "می‌خواهم کار کنم", "میخوام کار کنم", "علاقه‌مند به کاری", "علاقه مند به کاری",
]

# What actually marks a request is grammatical, not lexical: the thing being sought
# is an *indefinite* job («شغلی», «یه کاری»), while a question names the job it asks
# about («محیط کاری راننده زره‌پوش»). The phrase list above only ever caught the
# spellings someone thought to write down — «یه کاری که توش ... کار کنم» is the same
# request in plainer Persian and matched none of them, so it fell through to the
# question path and was answered instead of offered as a new record.
_ZWNJ = "‌"
_JOB = r"(?:شغل|کار|حرفه|پیشه)"
# A bare «کار» is far too common to key on ("محیط کار", "کارها"), so indefiniteness
# has to be marked — either by the ی suffix or by یه/یک in front.
_INDEF_JOB = rf"(?:(?:یه|یک)\s+{_JOB}ی?|{_JOB}ی)"
# «مناسب» is deliberately absent: it would fire on «محیط کاری مناسب برای پرستار
# چیست؟», which is a question. It stays in the phrase list as «مناسب من» instead.
_DESIRE = (rf"(?:می[{_ZWNJ}\s]?خوا|میخوا|بخوا|خواستم|دنبال|سراغ|علاقه"
           rf"|دوست\s*دارم|پیشنهاد|معرفی|بگرد|جستجو)")
# First person only: the clause has to be about what *the user* would do in the job.
# Third-person «کار می‌کند» reads the other way — it describes a job already named.
_FIRST_PERSON = r"(?:کنم|بکنم|باشم|بشوم|بشم|شوم|بتوانم|بتونم|بروم|برم)"

_JOB_REQUEST_RE = [re.compile(p) for p in (
    rf"{_INDEF_JOB}.{{0,40}}{_DESIRE}",                    # «شغلی ... می‌خواهم»
    rf"{_DESIRE}.{{0,40}}{_INDEF_JOB}",                    # «دنبال ... کاری»
    rf"{_INDEF_JOB}\s+که\b.{{0,120}}{_FIRST_PERSON}\b",    # «یه کاری که توش ... کار کنم»
    # «چه شغلی» asks which occupation; «چه کاری» asks which *task* («یک مهندس چه
    # کاری انجام می‌دهد؟») and is a duties question, so کار is excluded here. The
    # one requesting sense of it, «چه کاری مناسب», is a literal above.
    rf"(?:چه|کدام|کدوم)\s+(?:شغل|حرفه|پیشه)",              # «چه شغلی», «کدام شغل»
)]


def detect_intent(question):
    for intent, kws in INTENT_KEYWORDS.items():
        if any(k in question for k in kws):
            return intent
    return "general"


def is_job_request(question):
    """True when the user describes a job they want instead of asking about one.
    Expects normalize_text output. The phrase list catches fixed idioms; the
    patterns catch the general «an indefinite job + I want it / that I'd do» shape."""
    if any(k in question for k in JOB_REQUEST_KEYWORDS):
        return True
    return any(p.search(question) for p in _JOB_REQUEST_RE)

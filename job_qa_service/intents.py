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


# ---------------------------------------------------------------------------
# Is the input a *job name* and nothing else?
#
# Two questions again, and the second one is new. `is_bare_name` is the old inline
# test from `engine.answer`: a short input with no question in it is somebody typing
# an occupation rather than asking about one, and it is answered as a request for a
# description. `names_an_occupation` is what the *creation* route needs on top of it:
# a bare input that the corpus cannot answer is offered as a new record, and «کیک
# شکلاتی» and «تاریخ ایران» are two tokens each with no question mark and score in
# exactly the band a genuinely missing job scores in — retrieval cannot separate them,
# because neither is in the corpus and both are near something that is.
#
# What separates them is the head of the phrase. Persian names an occupation either
# with one of a small closed set of agent nouns («مدیر فصلنامه», «کارشناس امور
# ایثارگران») or with an agentive suffix on a noun («ریخته‌گر», «نگهبان», «قالی‌باف»).
# A topic, an object or a greeting has neither, and the honest answer to those is
# still «not in the database» rather than a generated record in the moderation queue.
BARE_NAME_MAX_TOKENS = 4

# Matched as a prefix, so one entry covers the plural and the -ی forms at once:
# «مدیر» reaches «مدیران» and «مدیریت», «کارشناس» reaches «کارشناسان».
OCCUPATION_HEADS = (
    "مدیر", "سرپرست", "رئیس", "معاون", "مسئول", "متصدی", "کارشناس", "کارمند", "کارگر",
    "مامور", "مأمور", "افسر", "فرمانده", "بازرس", "ناظر", "مشاور", "مربی", "معلم",
    "مدرس", "استاد", "محقق", "دانشمند", "طراح", "مهندس", "تکنسین", "اپراتور", "متخصص",
    "دستیار", "منشی", "حسابرس", "وکیل", "قاضی", "پزشک", "پرستار", "جراح", "مترجم",
    "ویراستار", "نویسنده", "سردبیر", "عکاس", "خواننده", "نوازنده", "آشپز", "نانوا",
    "قصاب", "خیاط", "راننده", "ملوان", "فروشنده", "بازاریاب", "معمار", "مکانیک",
    "نجار", "نصاب", "صیاد", "خدمه", "بهیار", "ماما", "مبلغ", "روحانی", "طلبه", "مداح",
    "خادم", "قاری", "رزمنده", "کارآموز", "کارورز", "خلبان", "نماینده", "تاجر",
    # Military ranks: the customer is a government body and the corpus carries 102
    # military occupations, so «سروان» is as likely an input here as «حسابدار».
    "سرباز", "سرهنگ", "سروان", "ستوان", "سرگرد", "سرتیپ", "ناخدا", "امیر", "تیمسار",
)

# The productive half. A stem shorter than this is not a noun being turned into an
# agent noun — «زبان» is «بان» on one letter, «دیگر» is «گر» on two, «مقدار» is «دار»
# on two — and each of those would otherwise read as an occupation.
_AGENTIVE_STEM_MIN = 3
_AGENTIVE_SUFFIXES = ("گر", "بان", "کار", "چی", "دار", "شناس", "ساز", "نویس", "فروش",
                      "نگار", "پزشک", "ورز", "باف", "کش", "کننده", "دهنده", "دان",
                      "یست", "گذار", "گزار", "پرور")

# Both the word and its singular are tried, because the plural hides the suffix: the
# corpus writes «تحلیلگران» and «برنامه‌نویسان», where the «گر» and the «نویس» are no
# longer at the end of the word. Stripping first would not do — «نگهبان» *ends* in
# «ان» without being a plural, and «نگهب» is nothing at all.
_PLURALS = (("گان", "ه"), ("های", ""), ("ها", ""), ("ان", ""))

# The ordinary words the suffix rule over-reaches on. Each is a noun that happens to
# end in an agentive suffix on a long enough stem, so no length guard separates them:
# «خیابان» is «بان» on four letters exactly as «نگهبان» is on three. Listing the few
# that are common enough to be typed is cheaper than weakening a rule that is right
# everywhere else.
_NOT_OCCUPATIONS = frozenset((
    "خیابان", "بیابان", "سازمان", "آشکار", "افکار", "انکار", "نمودار", "پدیدار",
    "بدهکار", "طلبکار", "پیشکش", "سرکش", "قارچی", "تماشاچی", "شکار", "نگار", "خاندان",
))

_PUNCT = "؟?.،,:;!\"'«»()[]"


def _tokens(question):
    return [t.strip(_PUNCT) for t in question.split() if t.strip(_PUNCT)]


def _forms(word):
    """The word, and its singular if it reads as a plural. Both are looked at because
    only one of the two ever carries the suffix — see `_PLURALS`."""
    for plural, restore in _PLURALS:
        if word.endswith(plural) and len(word) - len(plural) >= _AGENTIVE_STEM_MIN:
            return word, word[:-len(plural)] + restore
    return (word,)


def is_bare_name(question):
    """True when the input is a job name rather than a question about one.

    «معلم جغرافیا» carries no question verb and no question mark, so it is a request
    to be told about that occupation. Expects normalize_text output."""
    tokens = _tokens(question)
    if not tokens or len(set(tokens)) > BARE_NAME_MAX_TOKENS:
        return False
    if "؟" in question or "?" in question:
        return False
    if set(tokens) & QUESTION_WORDS:
        return False
    return detect_intent(question) == "general"


def names_an_occupation(question):
    """True when some word in the input is the head of an occupation title.

    Deliberately a vocabulary test and not a score: it is asked only of inputs the
    corpus could not answer, where the score has already said «nothing here» and the
    remaining question is whether that silence is a gap in the dataset or an input
    that was never about work."""
    for token in _tokens(question):
        if any(token.startswith(head) for head in OCCUPATION_HEADS):
            return True
        for word in _forms(token.replace("\u200c", "")):
            if word in _NOT_OCCUPATIONS:
                continue
            if any(word.endswith(s) and len(word) - len(s) >= _AGENTIVE_STEM_MIN
                   for s in _AGENTIVE_SUFFIXES):
                return True
    return False

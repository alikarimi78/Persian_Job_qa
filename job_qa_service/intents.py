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


# ---------------------------------------------------------------------------
# Is the input a question about *this assistant*?
#
# «کار تو چیه؟» and «هدف شما چیست؟» are not questions about an occupation, and no
# record answers them — but retrieval always ranks something first, so a three-word
# question lands wherever its two content words happen to point. Measured before this
# test existed: «کار شما چیست؟» scored 0.536 against «متخصصان روابط کار» and was
# answered as a description of that occupation, «چه کاری انجام می‌دهی؟» 0.535 against
# «نمایش‌دهندگان و تبلیغ‌کنندگان محصول», «چطور کار می‌کنی؟» 0.522 against «جوشکاران».
# The ones that stayed under the gate answered OOD_MESSAGE, which is not wrong but
# reads as the system failing at the first thing many people type into it.
#
# A vocabulary test rather than a score, for the same reason `names_an_occupation` is
# one: the question is not about the corpus at all, so no threshold over the corpus can
# separate it. **Two halves have to be present** — who is being asked («تو», «شما», a
# second-person verb, «این سامانه») and what is being asked about them («کار», «هدف»,
# «اسم», «چیست») — because each half on its own is ordinary Persian: «هدف شغل مشاور
# چیست؟» carries the second and is a question about an occupation.
BARE_SYSTEM_MAX_TOKENS = 8

# Who is being addressed. Whole tokens, never substrings: «تو» sits inside «توانایی»,
# «تولید» and «توپخانه», and the corpus is full of all three. Both spellings of the
# verb prefix are listed for the same reason JOB_REQUEST_KEYWORDS lists both.
SYSTEM_ADDRESSEES = frozenset((
    "تو", "شما", "خودت", "خودتان", "خودتو", "خودتون", "تورا", "تورو", "شمارا",
    "هستی", "هستید", "هستین", "کیستی", "بلدی", "بلدید", "داری", "دارید",
    "می‌کنی", "میکنی", "می‌کنید", "میکنید", "می‌توانی", "می‌تونی", "میتونی",
    "می‌توانید", "می‌تونید", "میتونید", "می‌دهی", "میدهی", "میدی", "می‌دهید", "میدید",
))

# The same half, written as a phrase instead of a pronoun. «این» is required: the corpus
# holds «خدمه سامانه‌های موشکی پدافند هوایی», and a bare «سامانه» would take every
# question about one with it.
SYSTEM_SELF_PHRASES = ("این سامانه", "این سایت", "این برنامه", "این سیستم",
                       "این ربات", "این دستیار", "این هوش مصنوعی")

# What is being asked about it. Enumerated rather than prefix-matched, which is what
# OCCUPATION_HEADS can afford and this cannot: «کی» as a prefix reaches «کیفیت» and
# «نام» reaches «نامه», and either one plus a «شما» would swallow a real question.
SYSTEM_TOPICS = frozenset((
    "کار", "کاری", "کارها", "کارهایی", "چیکار", "چیکاره",
    "وظیفه", "وظایف", "هدف", "هدفی", "کاربرد", "فایده", "معرفی",
    "اسم", "نام", "امکانات", "امکاناتی", "قابلیت", "قابلیت‌ها", "قابلیتی",
    "وظیفه‌ای", "کمک", "کمکی",
    "مدل", "مدلی", "سازنده", "ساخته",
    "کی", "کیست", "کیه", "چی", "چیست", "چیه", "چیستی",
))

# Both halves in one word — the second-person enclitic is the addressee. «کارت» is also
# the Persian for a card, which is the one wrong reading here and costs a person asking
# about «کارت ملی» a paragraph explaining what this service does.
SYSTEM_TOPICS_SELF = frozenset((
    "کارت", "کارتان", "کارتون", "کارات", "وظیفت", "وظیفه‌ات", "وظایفت", "وظایفتان",
    "هدفت", "هدفتان", "هدفتون", "اسمت", "اسمتان", "اسمتون", "نامت", "نامتان",
    "کاربردت", "سازندت", "سازنده‌ات",
))

# What the two halves miss. Each carries its own second person, so none of them widens
# the test to inputs that are not addressed to the assistant.
SYSTEM_QUESTION_KEYWORDS = (
    "هوش مصنوعی هستی", "هوش مصنوعی هستید", "ربات هستی", "چت‌بات هستی", "چت بات هستی",
    "انسان هستی", "آدم هستی", "چیکاره‌ای", "چیکاره ای",
    "خودت را معرفی", "خودتان را معرفی", "خودت رو معرفی", "خودتو معرفی",
    "چه کمکی می‌توانی", "چه کمکی میتونی", "چه کمکی می‌کنی",
    "چه کاری بلدی", "چه کارهایی بلدی",
)


def is_about_system(question):
    """True when the input asks about this assistant rather than about an occupation.

    Expects normalize_text output. Asked **after** `is_job_request`, never before: a
    sentence like «یک شغل خوب معرفی کنید» addresses the assistant too, and the offer it
    asks for is a better answer than a description of what the assistant does.

    A question that names an occupation is never one of these, whatever else it says.
    That veto is the whole of what keeps «هدف شغل مشاور چیست؟» and «کار یک حسابدار
    چیست؟» — both of which carry a topic word — on the path that answers them."""
    tokens = _tokens(question)
    if not tokens or len(tokens) > BARE_SYSTEM_MAX_TOKENS:
        return False
    if names_an_occupation(question):
        return False
    if any(k in question for k in SYSTEM_QUESTION_KEYWORDS):
        return True
    words = set(tokens)
    if words & SYSTEM_TOPICS_SELF:
        return True
    addressed = (bool(words & SYSTEM_ADDRESSEES)
                 or any(p in question for p in SYSTEM_SELF_PHRASES))
    return addressed and bool(words & SYSTEM_TOPICS)


# ---------------------------------------------------------------------------
# Is the input a greeting and nothing else?
#
# «سلام» reaches the corpus like any other text and lands on whatever it happens to be
# nearest — measured 0.478 against «غربالگران امنیتی حمل و نقل» — and answers
# OOD_MESSAGE, which tells someone who has just said hello that the database holds no
# information about it. The reply is the same one `is_about_system` earns: a person who
# opens with a greeting and nothing else is looking for the way in.
#
# **The greeting has to be the whole input**, which is what separates «سلام» from
# «سلام، وظایف پرستار چیست؟» — a question that merely opens with one, and that must go
# on being answered. So the phrases are removed from the text and what is left has to be
# nothing but the words a greeting is padded with. That subtraction is also what makes
# the substring match safe: «سلام» is inside «سلامت» and «اسلام», «درود» is inside
# «درودگر», and in each case what remains after the cut is not a filler.
GREETING_MAX_TOKENS = 5

# **Sorted longest first**, because the phrases nest and each one is cut out of the text
# in turn: «سلام» taken out of «السلام علیکم» leaves «ال», and «خوبی» out of «خوبید»
# leaves «د» — neither is a filler, so both greetings would be refused by the very rule
# that makes the match safe. Longest first, each is consumed whole.
GREETING_PHRASES = tuple(sorted((
    "سلام", "السلام", "علیکم", "درود", "وقت بخیر", "وقت به خیر", "روز بخیر",
    "صبح بخیر", "عصر بخیر", "شب بخیر", "خسته نباشید", "چه خبر",
    "خوبی", "خوبید", "خوبین", "چطوری", "چطورید", "چطورین", "چطوره",
    "حال شما چطور", "حالت چطوره", "حالتون چطوره", "حالتان چطور",
    "hi", "hey", "hello",
), key=len, reverse=True))

# What a greeting is padded with, and nothing else. Anything outside this list means the
# input carries a question as well, and the question is what gets answered.
GREETING_FILLERS = frozenset((
    "با", "و", "عرض", "ادب", "احترام", "خدمت", "بر", "شما", "دوست", "من", "عزیز",
    "آقا", "خانم", "ببخشید", "لطفا", "لطفاً", "سپاس", "است", "هستم",
))


def is_greeting(question):
    """True when the input is a greeting and carries no question with it.

    Expects normalize_text output."""
    tokens = _tokens(question)
    if not tokens or len(tokens) > GREETING_MAX_TOKENS:
        return False
    rest, greeted = question, False
    for phrase in GREETING_PHRASES:
        if phrase in rest:
            greeted = True
            rest = rest.replace(phrase, " ")
    return greeted and not set(_tokens(rest)) - GREETING_FILLERS

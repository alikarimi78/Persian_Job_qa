import re

INTENT_TO_FIELDS = {
    "description": ["description"], "responsibilities": ["responsibilities"],
    "competencies": ["skills", "abilities"], "knowledge": ["knowledge"],
    "tools": ["tools"], "career_path": ["career_path_next"],
    "work_context": ["work_context"], "aliases": ["aliases"],
    "general": ["description", "responsibilities", "skills", "knowledge", "abilities", "tools"],
}

INTENT_KEYWORDS = {
    "responsibilities": ["وظایف", "وظیفه", "مسئولیت", "روزمره", "کارها", "چه کاری", "چیکار", "چه کار"],
    "tools":            ["ابزار", "نرم‌افزار", "نرم افزار", "برنامه", "تجهیزات", "سیستم", "با چی"],
    "competencies":     ["مهارت", "شایستگی", "توانایی", "توانمندی", "ویژگی", "دقت", "استعداد", "باید بلد"],
    "knowledge":        ["چه دانشی", "دانش لازم", "دانش مورد نیاز", "دانش تخصصی",
                         "دانش فنی", "معلومات", "باید بداند", "چه بداند"],
    "career_path":      ["ارتقا", "ارتقاء", "آینده", "پیشرفت", "مسیر", "بعدش", "ترفیع", "رشد"],
    "work_context":     ["محیط", "فضا", "شرایط کاری", "کجا کار", "محل کار"],
    "aliases":          ["نام دیگر", "اسم دیگر", "معادل"],
    "description":      ["معرفی", "شرح", "چیست", "توضیح", "درباره", "چیه"],
}

EXPLICIT_COMBO_WORDS = ["بین رشته", "بین‌رشته", "ترکیب", "هر دو", "هردو", "تلفیق", "میان‌رشته"]
QUESTION_WORDS = {"چیست", "چیه", "چطور", "چگونه", "کدام", "چند", "چی", "کجا", "آیا"}

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

_ZWNJ = "‌"
_JOB = r"(?:شغل|کار|حرفه|پیشه)"
_INDEF_JOB = rf"(?:(?:یه|یک)\s+{_JOB}ی?|{_JOB}ی)"
_DESIRE = (rf"(?:می[{_ZWNJ}\s]?خوا|میخوا|بخوا|خواستم|دنبال|سراغ|علاقه"
           rf"|دوست\s*دارم|پیشنهاد|معرفی|بگرد|جستجو)")
_FIRST_PERSON = r"(?:کنم|بکنم|باشم|بشوم|بشم|شوم|بتوانم|بتونم|بروم|برم)"

_JOB_REQUEST_RE = [re.compile(p) for p in (
    rf"{_INDEF_JOB}.{{0,40}}{_DESIRE}",
    rf"{_DESIRE}.{{0,40}}{_INDEF_JOB}",
    rf"{_INDEF_JOB}\s+که\b.{{0,120}}{_FIRST_PERSON}\b",
    rf"(?:چه|کدام|کدوم)\s+(?:شغل|حرفه|پیشه)",
)]


def detect_intent(question):
    for intent, kws in INTENT_KEYWORDS.items():
        if any(k in question for k in kws):
            return intent
    return "general"


def is_job_request(question):
    if any(k in question for k in JOB_REQUEST_KEYWORDS):
        return True
    return any(p.search(question) for p in _JOB_REQUEST_RE)


BARE_NAME_MAX_TOKENS = 4

OCCUPATION_HEADS = (
    "مدیر", "سرپرست", "رئیس", "معاون", "مسئول", "متصدی", "کارشناس", "کارمند", "کارگر",
    "مامور", "مأمور", "افسر", "فرمانده", "بازرس", "ناظر", "مشاور", "مربی", "معلم",
    "مدرس", "استاد", "محقق", "دانشمند", "طراح", "مهندس", "تکنسین", "اپراتور", "متخصص",
    "دستیار", "منشی", "حسابرس", "وکیل", "قاضی", "پزشک", "پرستار", "جراح", "مترجم",
    "ویراستار", "نویسنده", "سردبیر", "عکاس", "خواننده", "نوازنده", "آشپز", "نانوا",
    "قصاب", "خیاط", "راننده", "ملوان", "فروشنده", "بازاریاب", "معمار", "مکانیک",
    "نجار", "نصاب", "صیاد", "خدمه", "بهیار", "ماما", "مبلغ", "روحانی", "طلبه", "مداح",
    "خادم", "قاری", "رزمنده", "کارآموز", "کارورز", "خلبان", "نماینده", "تاجر",
    "سرباز", "سرهنگ", "سروان", "ستوان", "سرگرد", "سرتیپ", "ناخدا", "امیر", "تیمسار",
)

_AGENTIVE_STEM_MIN = 3
_AGENTIVE_SUFFIXES = ("گر", "بان", "کار", "چی", "دار", "شناس", "ساز", "نویس", "فروش",
                      "نگار", "پزشک", "ورز", "باف", "کش", "کننده", "دهنده", "دان",
                      "یست", "گذار", "گزار", "پرور")

_PLURALS = (("گان", "ه"), ("های", ""), ("ها", ""), ("ان", ""))

_NOT_OCCUPATIONS = frozenset((
    "خیابان", "بیابان", "سازمان", "آشکار", "افکار", "انکار", "نمودار", "پدیدار",
    "بدهکار", "طلبکار", "پیشکش", "سرکش", "قارچی", "تماشاچی", "شکار", "نگار", "خاندان",
))

_PUNCT = "؟?.،,:;!\"'«»()[]"


def _tokens(question):
    return [t.strip(_PUNCT) for t in question.split() if t.strip(_PUNCT)]


def _forms(word):
    for plural, restore in _PLURALS:
        if word.endswith(plural) and len(word) - len(plural) >= _AGENTIVE_STEM_MIN:
            return word, word[:-len(plural)] + restore
    return (word,)


def is_bare_name(question):
    tokens = _tokens(question)
    if not tokens or len(set(tokens)) > BARE_NAME_MAX_TOKENS:
        return False
    if "؟" in question or "?" in question:
        return False
    if set(tokens) & QUESTION_WORDS:
        return False
    return detect_intent(question) == "general"


def names_an_occupation(question):
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


BARE_SYSTEM_MAX_TOKENS = 8

SYSTEM_ADDRESSEES = frozenset((
    "تو", "شما", "خودت", "خودتان", "خودتو", "خودتون", "تورا", "تورو", "شمارا",
    "هستی", "هستید", "هستین", "کیستی", "بلدی", "بلدید", "داری", "دارید",
    "می‌کنی", "میکنی", "می‌کنید", "میکنید", "می‌توانی", "می‌تونی", "میتونی",
    "می‌توانید", "می‌تونید", "میتونید", "می‌دهی", "میدهی", "میدی", "می‌دهید", "میدید",
))

SYSTEM_SELF_PHRASES = ("این سامانه", "این سایت", "این برنامه", "این سیستم",
                       "این ربات", "این دستیار", "این هوش مصنوعی")

SYSTEM_TOPICS = frozenset((
    "کار", "کاری", "کارها", "کارهایی", "چیکار", "چیکاره",
    "وظیفه", "وظایف", "هدف", "هدفی", "کاربرد", "فایده", "معرفی",
    "اسم", "نام", "امکانات", "امکاناتی", "قابلیت", "قابلیت‌ها", "قابلیتی",
    "وظیفه‌ای", "کمک", "کمکی",
    "مدل", "مدلی", "سازنده", "ساخته",
    "کی", "کیست", "کیه", "چی", "چیست", "چیه", "چیستی",
))

SYSTEM_TOPICS_SELF = frozenset((
    "کارت", "کارتان", "کارتون", "کارات", "وظیفت", "وظیفه‌ات", "وظایفت", "وظایفتان",
    "هدفت", "هدفتان", "هدفتون", "اسمت", "اسمتان", "اسمتون", "نامت", "نامتان",
    "کاربردت", "سازندت", "سازنده‌ات",
))

SYSTEM_QUESTION_KEYWORDS = (
    "هوش مصنوعی هستی", "هوش مصنوعی هستید", "ربات هستی", "چت‌بات هستی", "چت بات هستی",
    "انسان هستی", "آدم هستی", "چیکاره‌ای", "چیکاره ای",
    "خودت را معرفی", "خودتان را معرفی", "خودت رو معرفی", "خودتو معرفی",
    "چه کمکی می‌توانی", "چه کمکی میتونی", "چه کمکی می‌کنی",
    "چه کاری بلدی", "چه کارهایی بلدی",
)


def is_about_system(question):
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


GREETING_MAX_TOKENS = 5

GREETING_PHRASES = tuple(sorted((
    "سلام", "السلام", "علیکم", "درود", "وقت بخیر", "وقت به خیر", "روز بخیر",
    "صبح بخیر", "عصر بخیر", "شب بخیر", "خسته نباشید", "چه خبر",
    "خوبی", "خوبید", "خوبین", "چطوری", "چطورید", "چطورین", "چطوره",
    "حال شما چطور", "حالت چطوره", "حالتون چطوره", "حالتان چطور",
    "hi", "hey", "hello",
), key=len, reverse=True))

GREETING_FILLERS = frozenset((
    "با", "و", "عرض", "ادب", "احترام", "خدمت", "بر", "شما", "دوست", "من", "عزیز",
    "آقا", "خانم", "ببخشید", "لطفا", "لطفاً", "سپاس", "است", "هستم",
))


def is_greeting(question):
    tokens = _tokens(question)
    if not tokens or len(tokens) > GREETING_MAX_TOKENS:
        return False
    rest, greeted = question, False
    for phrase in GREETING_PHRASES:
        if phrase in rest:
            greeted = True
            rest = rest.replace(phrase, " ")
    return greeted and not set(_tokens(rest)) - GREETING_FILLERS

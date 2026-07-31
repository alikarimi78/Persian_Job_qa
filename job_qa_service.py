# -*- coding: utf-8 -*-
"""
Persian Occupation Q&A engine (RAG) — production backend module.

Pipeline: hybrid dense retrieval (full-text + title/alias embeddings, BAAI/bge-m3)
fused with BM25 via Reciprocal Rank Fusion -> dual-gate out-of-domain check ->
single or interdisciplinary answer generated through an OpenAI-compatible API,
with automatic template fallback on any API failure.

A second entry path handles job *requests* ("شغلی می‌خواهم با این ویژگی‌ها..."):
the described spec is retrieved against the corpus and either an existing close
match is returned, or a brand-new occupation record is generated in the dataset's
own 10-column shape. A generated record is an *offer*, not a decision: the answer
names it and asks the user whether to register it, and the record itself is handed
back in `job_draft` so the client can prefill its suggestion form with it. Storing
it stays the user's call, and approving it stays the admin's.

Backend usage:
    from job_qa_service import JobQAEngine
    engine = JobQAEngine("Merged_Occupations.xlsx")   # load once at startup
    result = engine.answer("وظایف افسر توپخانه چیست؟")  # thread-safe for reads

Env vars (.env supported): OPENAI_API_KEY, OPENAI_BASE_URL, LLM_MODEL, EMBED_MODEL_NAME
"""

import os
import re
import time
import math
import json
import hashlib
from collections import Counter, defaultdict
import sys

import numpy as np
import pandas as pd
from sentence_transformers import SentenceTransformer


# Force UTF-8 on stdio; malformed terminal bytes become � instead of crashing
for _stream in (sys.stdin, sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

try:
    import torch
    _HAS_CUDA = torch.cuda.is_available()
except Exception:
    _HAS_CUDA = False

try:
    from hazm import Normalizer
    _normalizer = Normalizer()
except Exception:
    _normalizer = None

from openai import OpenAI, RateLimitError, APIConnectionError, APITimeoutError


# =========================================================
# Configuration
# =========================================================
EMBED_MODEL_NAME = os.getenv("EMBED_MODEL_NAME", "BAAI/bge-m3")
EMB_CACHE_DIR = os.getenv("EMB_CACHE_DIR", "emb_cache")

W_FULL, W_TITLE = 0.6, 0.4      # dense hybrid weights (sum = 1)
RRF_K = 60
MAX_CANDIDATES = 15
SCAN_DEPTH = 5                  # depth searched for a distinct secondary job

# Calibrated for bge-m3 on this dataset (correct ≈ 0.58–0.66, OOD ceiling ≈ 0.36)
THRESHOLD_MATCH  = 0.49         # dense out-of-domain gate
THRESHOLD_SPARSE = 0.15         # sparse out-of-domain gate
SECONDARY_MIN    = 0.50         # min dense score of the 2nd job (interdisciplinary)
SECONDARY_MARGIN = 0.01         # max gap between 1st and 2nd job
PAIR_SIM_MAX     = 0.85         # above this, two jobs are near-duplicates -> single mode

# Job-request mode. A spec description must match an existing record more closely
# than a plain question does before we call it "the same job", hence the higher
# bar than THRESHOLD_MATCH; below DISCOVERY_FLOOR the text is not about work at all.
DISCOVERY_MATCH   = 0.60        # >= this: an existing job already covers the request
DISCOVERY_FLOOR   = 0.35        # < this (and sparse weak too): out of domain, do not invent
DISCOVERY_RELATED = 3           # neighbouring jobs shown to the user / fed to the generator

LLM_MODEL    = os.getenv("LLM_MODEL", "gpt-4o-mini")
LLM_BASE_URL = os.getenv("OPENAI_BASE_URL")
LLM_API_KEY  = os.getenv("OPENAI_API_KEY")
OCCUPATIONS_PATH = os.getenv("OCCUPATIONS_PATH")
LLM_MAX_RETRIES, LLM_BASE_DELAY = 3, 2.0

SYSTEM_SINGLE = (
    "تو موتور پاسخ‌گویی یک اپلیکیشن رسمی معرفی مشاغل هستی و خروجی تو مستقیماً و بدون ویرایش "
    "به کاربر نهایی نمایش داده می‌شود. قوانین را دقیقاً رعایت کن:\n"
    "1) پاسخ را مستقیم با خودِ جواب شروع کن؛ هیچ مقدمه، سلام، یا جمله‌ای مانند «سوال شما درباره...» ننویس.\n"
    "2) فقط بر اساس «اطلاعات شغل» داده‌شده پاسخ بده و از دانش خودت چیزی اضافه نکن.\n"
    "3) لحن رسمی و کتابی فارسی؛ از لحن محاوره‌ای و تعارف پرهیز کن.\n"
    "4) خروجی متن ساده باشد؛ به هیچ وجه از Markdown (ستاره، #، بک‌تیک) استفاده نکن. "
    "اگر فهرست لازم بود، هر مورد را در یک خط با خط تیره (-) بنویس.\n"
    "5) کوتاه و دقیق: حداکثر پنج جمله یا چند مورد فهرستی کوتاه. جمع‌بندی و توضیح اضافه ممنوع.\n"
    "6) اگر شغلِ داده‌شده دقیقاً همان شغلِ مورد پرسش نبود اما مرتبط و هم‌حوزه بود، پاسخ را بر اساس "
    "همان شغل داده‌شده بده و در ابتدای پاسخ در یک عبارت کوتاه نام شغل را ذکر کن "
    "(مثلاً: «نزدیک‌ترین شغل موجود، ... است.»). فقط اگر داده‌ها هیچ ارتباطی با پرسش نداشتند بنویس: "
    "«اطلاعات کافی در این مورد موجود نیست.»\n"
    "7) اگر ورودی کاربر فقط نام یک شغل بود و پرسش مشخصی نداشت، آن را درخواست معرفی تلقی کن "
    "و معرفی کوتاهی از همان شغل بر اساس داده‌ها ارائه بده؛ در این حالت از عبارت "
    "«اطلاعات کافی موجود نیست» استفاده نکن."
)

SYSTEM_INTERDISCIPLINARY = (
    "تو موتور پاسخ‌گویی یک اپلیکیشن رسمی معرفی مشاغل هستی و خروجی تو مستقیماً و بدون ویرایش "
    "به کاربر نهایی نمایش داده می‌شود. سوال کاربر به نقطه تلاقی دو شغل مرتبط مربوط است. قوانین:\n"
    "1) اطلاعات دو شغل را در یک پاسخ واحد و منسجم ترکیب کن؛ دو فهرست جداگانه ارائه نکن.\n"
    "2) پاسخ را مستقیم شروع کن؛ بدون مقدمه، سلام، یا اشاره به خود سوال.\n"
    "3) در یک جمله کوتاه اشاره کن که پاسخ ترکیبی از دو حوزه است و نام دو شغل را بیاور، سپس اصل پاسخ.\n"
    "4) فقط بر اساس داده‌های داده‌شده پاسخ بده؛ لحن رسمی و کتابی فارسی.\n"
    "5) متن ساده بدون Markdown (ستاره، #، بک‌تیک)؛ فهرست فقط با خط تیره (-).\n"
    "6) حداکثر شش جمله یا چند مورد فهرستی کوتاه؛ توضیح اضافه و جمع‌بندی ممنوع."
)

SYSTEM_JOB_MATCH = (
    "تو موتور پاسخ‌گویی یک اپلیکیشن رسمی معرفی مشاغل هستی. کاربر ویژگی‌های شغل مورد نظرش را "
    "توصیف کرده و یک شغل موجود در پایگاه داده با آن توصیف هم‌خوانی دارد. قوانین:\n"
    "1) با یک جمله کوتاه اعلام کن که چنین شغلی وجود دارد و نام آن را بیاور.\n"
    "2) سپس توضیح بده کدام بخش از توصیف کاربر با کدام ویژگی این شغل مطابقت دارد.\n"
    "3) فقط بر اساس «اطلاعات شغل» داده‌شده بنویس و از دانش خودت چیزی اضافه نکن.\n"
    "4) لحن رسمی و کتابی فارسی؛ متن ساده بدون Markdown (ستاره، #، بک‌تیک)؛ فهرست فقط با خط تیره (-).\n"
    "5) حداکثر شش جمله. مقدمه، سلام و جمع‌بندی ممنوع."
)

SYSTEM_JOB_GENERATE = (
    "تو طراح مشاغل یک اپلیکیشن رسمی معرفی مشاغل هستی. کاربر ویژگی‌های شغلی را توصیف کرده که در "
    "پایگاه داده وجود ندارد. وظیفه تو ساختن یک رکورد شغلی جدید و واقع‌گرایانه بر اساس همان توصیف است.\n"
    "خروجی تو باید فقط و فقط یک شیء JSON معتبر باشد؛ هیچ متن، توضیح یا بلوک کد قبل و بعد از آن ننویس.\n"
    "کلیدهای JSON دقیقاً اینها هستند و مقادیر همه فارسی‌اند:\n"
    'job_title, aliases, tools, skills, knowledge, abilities, work_context, career_path_next, '
    'description, responsibilities\n'
    "قوانین محتوا:\n"
    "1) job_title: یک عنوان شغلی رسمی، کوتاه و باورپذیر فارسی که توصیف کاربر را پوشش دهد.\n"
    "2) aliases (۱ تا ۳ مورد)، tools (۲ تا ۵ مورد)، skills (۳ تا ۵ مورد)، knowledge (۲ تا ۴ مورد)، "
    "abilities (۲ تا ۴ مورد)، responsibilities (۳ تا ۵ مورد) و career_path_next (۱ تا ۳ مورد) را "
    "به‌صورت رشته‌ای بنویس که موارد آن با کاراکتر | از هم جدا شده‌اند. تفکیک این سه را رعایت کن: "
    "skills مهارت‌های آموختنی و عملی، knowledge حوزه‌های دانش نظری و تخصصی، و abilities توانایی‌های "
    "ذاتی و شخصی؛ یک مورد را بین آنها تکرار نکن.\n"
    "3) description و work_context هرکدام یک یا دو جمله کامل فارسی باشند.\n"
    "4) همه محتوا باید مستقیماً از خواسته‌های کاربر ریشه بگیرد؛ ویژگی‌هایی که کاربر گفته را نادیده نگیر.\n"
    "5) شغل باید واقع‌گرایانه و قابل تحقق باشد؛ اگر درخواست کاربر خیالی یا اغراق‌آمیز بود، نزدیک‌ترین "
    "معادل واقعی و حرفه‌ای آن را طراحی کن.\n"
    "6) «مشاغل مشابه موجود» فقط برای الگوگرفتن از سبک نگارش و پرهیز از تکرارند؛ شغل جدید باید با آنها "
    "متفاوت باشد و رونویسی از آنها نباشد.\n"
    "7) از Markdown استفاده نکن و هیچ فیلدی را خالی نگذار."
)

EXPECTED_COLUMNS = ["job_title", "aliases", "tools", "skills", "knowledge", "abilities",
                    "work_context", "career_path_next", "description", "responsibilities"]

# The three columns where a comma is punctuation rather than a list separator;
# everything else is a "|"-joined list. A generated draft is checked against this
# before it leaves the engine, so a model reaching for "|" in prose cannot
# reintroduce the corruption the dataset was repaired of.
PROSE_COLUMNS = ["job_title", "description", "work_context"]

FIELD_LABELS = {
    "job_title": "عنوان شغل", "aliases": "نام‌های دیگر", "tools": "ابزارها",
    "skills": "مهارت‌ها و شایستگی‌ها", "knowledge": "دانش تخصصی",
    "abilities": "توانایی‌ها", "work_context": "محیط کاری",
    "career_path_next": "مسیر شغلی بعدی", "description": "شرح شغل",
    "responsibilities": "وظایف و مسئولیت‌ها",
}

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

OOD_MESSAGE = "متاسفانه در دیتابیس من اطلاعاتی درباره این موضوع پیدا نشد."

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

MATCH_HEADER = "بر اساس ویژگی‌هایی که توصیف کردی، این شغل در پایگاه داده موجود است:"
DRAFT_HEADER = ("شغلی که دقیقاً منطبق بر توصیف تو باشد در پایگاه داده موجود نیست، "
                "اما بر اساس ویژگی‌هایی که گفتی یک شغل پیشنهادی طراحی شد:")
DRAFT_QUESTION = ("می‌خواهی این شغل را به‌عنوان شغل جدید ثبت کنی؟ در صورت تایید، فرم ثبت شغل با "
                  "همین مشخصات باز می‌شود و می‌توانی پیش از ارسال آن‌ها را ویرایش کنی. "
                  "ثبت نهایی پس از تایید ادمین انجام می‌شود.")
RELATED_LABEL = "مشاغل مرتبط موجود در پایگاه داده"
DISCOVERY_UNAVAILABLE = ("امکان ساخت شغل پیشنهادی در این لحظه فراهم نیست؛ "
                         "نزدیک‌ترین مشاغل موجود در پایگاه داده اینها هستند:")

# Shown when a job is presented as a whole profile rather than as an answer to one intent
DISCOVERY_FIELDS = ["description", "responsibilities", "skills", "knowledge", "abilities",
                    "tools", "work_context", "career_path_next"]

_MD_PATTERNS = [(re.compile(r"\*\*(.*?)\*\*"), r"\1"), (re.compile(r"\*(.*?)\*"), r"\1"),
                (re.compile(r"`(.*?)`"), r"\1"), (re.compile(r"#{1,6}\s?"), "")]


# =========================================================
# Text utilities
# =========================================================
def normalize_text(text):
    if text is None or (isinstance(text, float) and pd.isna(text)):
        return ""
    text = str(text).replace("ي", "ی").replace("ك", "ک")
    text = " ".join(text.split())
    if _normalizer:
        text = _normalizer.normalize(text)
    return text.strip()


def _clean_markdown(text):
    for pattern, repl in _MD_PATTERNS:
        text = pattern.sub(repl, text)
    return text.strip()


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


def _parse_json_object(text):
    """Extracts the first JSON object from an LLM reply; None if there is none.
    Tolerates the code fences and stray prose some models add around JSON."""
    if not text:
        return None
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        return None
    try:
        obj = json.loads(match.group(0))
    except (json.JSONDecodeError, ValueError):
        return None
    return obj if isinstance(obj, dict) else None


def _corpus_fingerprint(*text_groups):
    """Short digest of the embedding model plus every text that gets encoded. The
    embedding cache is keyed on it, so editing a record or changing how combined_text
    is assembled misses the cache instead of silently reusing the old vectors."""
    digest = hashlib.sha256(EMBED_MODEL_NAME.encode("utf-8"))
    for texts in text_groups:
        for text in texts:
            digest.update(text.encode("utf-8"))
            digest.update(b"\x1f")
        digest.update(b"\x1e")
    return digest.hexdigest()[:12]


def build_context(row, fields, include_title=True):
    lines = []
    if include_title:
        lines.append(f"{FIELD_LABELS['job_title']}: {row['job_title']}")
        if row.get("aliases"):
            lines.append(f"{FIELD_LABELS['aliases']}: {row['aliases']}")
    lines += [f"{FIELD_LABELS.get(f, f)}: {row.get(f, '')}" for f in fields if row.get(f, "")]
    return "\n".join(lines)


# =========================================================
# Sparse channel: BM25
# =========================================================
class BM25:
    """BM25 with query-max normalization: scores are divided by the maximum score
    a document matching ALL query tokens could reach, so weak partial matches
    (or queries whose content words are absent from the corpus) stay low."""
    K1, B = 1.5, 0.75

    def __init__(self, texts):
        corpus_tokens = [t.lower().split() for t in texts]
        self.doc_count = len(corpus_tokens)
        self.doc_lengths = np.array([len(d) for d in corpus_tokens], dtype=np.float32)
        self.avg_len = float(np.mean(self.doc_lengths)) if self.doc_count else 1.0

        self.inverted = defaultdict(dict)
        for doc_id, tokens in enumerate(corpus_tokens):
            for tok, cnt in Counter(tokens).items():
                self.inverted[tok][doc_id] = cnt
        self.idf = {tok: math.log((self.doc_count - len(dd) + 0.5) / (len(dd) + 0.5) + 1.0)
                    for tok, dd in self.inverted.items()}
        self.oov_idf = math.log((self.doc_count + 0.5) / 0.5 + 1.0)

    def score(self, query):
        scores = np.zeros(self.doc_count, dtype=np.float32)
        max_possible = 0.0
        for tok in set(query.lower().split()):
            idf = self.idf.get(tok, self.oov_idf)
            max_possible += idf * (self.K1 + 1.0)
            dd = self.inverted.get(tok)
            if not dd:
                continue
            idxs = np.fromiter(dd.keys(), dtype=np.int64)
            tfs = np.fromiter(dd.values(), dtype=np.float32)
            lens = self.doc_lengths[idxs]
            denom = tfs + self.K1 * (1.0 - self.B + self.B * lens / self.avg_len)
            scores[idxs] += idf * tfs * (self.K1 + 1.0) / denom
        return scores / max_possible if max_possible > 0 else scores


# =========================================================
# Engine
# =========================================================
class JobQAEngine:
    def __init__(self, data, rebuild_embeddings=False):
        self.df = self._load_data(data)

        device = "cuda" if _HAS_CUDA else "cpu"
        self.model = SentenceTransformer(EMBED_MODEL_NAME, device=device)
        self.emb_full, self.emb_title = self._load_or_build_embeddings(rebuild_embeddings)
        self.bm25 = BM25(self.df["combined_text"].tolist())
        self._client = None

    # ---------- data ----------
    @staticmethod
    def _combined_text(row):
        # Important fields first: encoder-side truncation drops the least critical tail
        parts = [
            f"{FIELD_LABELS['job_title']}: {row['job_title']}",
            f"{FIELD_LABELS['aliases']}: {row['aliases'].replace('|', '،')}",
            f"{FIELD_LABELS['description']}: {row['description']}",
            f"{FIELD_LABELS['responsibilities']}: {row['responsibilities']}",
            f"{FIELD_LABELS['skills']}: {row['skills']}",
            f"{FIELD_LABELS['knowledge']}: {row['knowledge']}",
            f"{FIELD_LABELS['abilities']}: {row['abilities']}",
            f"{FIELD_LABELS['tools']}: {row['tools']}",
            f"{FIELD_LABELS['work_context']}: {row['work_context']}",
            f"{FIELD_LABELS['career_path_next']}: {row['career_path_next']}",
        ]
        return " . ".join(p for p in parts if p.split(": ", 1)[-1].strip())

    @staticmethod
    def _title_alias_text(row):
        return f"{row['job_title']} ، {row['aliases'].replace('|', '،')}".strip(" ،")

    def _load_data(self, data):
        df = data.copy() if isinstance(data, pd.DataFrame) else pd.read_excel(data)
        df.columns = [str(c).strip().lower() for c in df.columns]
        for col in EXPECTED_COLUMNS:
            if col not in df.columns:
                df[col] = ""
            df[col] = df[col].map(normalize_text)
        df = df[df["job_title"].str.len() > 0].reset_index(drop=True)
        df["combined_text"] = df.apply(self._combined_text, axis=1)
        return df

    # ---------- embeddings ----------
    def _encode(self, texts, prefix):
        if "e5" in EMBED_MODEL_NAME.lower():          # E5 models need query:/passage: prefixes
            texts = [f"{prefix}: {t}" for t in texts]
        return self.model.encode(texts, normalize_embeddings=True, show_progress_bar=False)

    def _load_or_build_embeddings(self, rebuild):
        os.makedirs(EMB_CACHE_DIR, exist_ok=True)
        full_texts = self.df["combined_text"].tolist()
        title_texts = self.df.apply(self._title_alias_text, axis=1).tolist()

        tag = EMBED_MODEL_NAME.replace("/", "_")
        fingerprint = _corpus_fingerprint(full_texts, title_texts)
        path = os.path.join(EMB_CACHE_DIR, f"corpus_{tag}_{len(self.df)}_{fingerprint}.npz")

        if os.path.exists(path) and not rebuild:
            data = np.load(path)
            if {"full", "title"} <= set(data.files) and len(data["full"]) == len(self.df):
                return data["full"], data["title"]

        emb_full = self._encode(full_texts, "passage")
        emb_title = self._encode(title_texts, "passage")
        np.savez(path, full=emb_full, title=emb_title)
        return emb_full, emb_title

    # ---------- retrieval ----------
    def _retrieve(self, q_norm):
        """Dense hybrid + BM25 rankings fused with Reciprocal Rank Fusion."""
        q_emb = self._encode([q_norm], "query")[0]
        dense = W_FULL * (self.emb_full @ q_emb) + W_TITLE * (self.emb_title @ q_emb)
        sparse = self.bm25.score(q_norm)

        k = min(MAX_CANDIDATES, len(dense))
        rrf = defaultdict(float)
        for rank, idx in enumerate(np.argsort(dense)[::-1][:k]):
            rrf[int(idx)] += 1.0 / (RRF_K + rank + 1)
        for rank, idx in enumerate(np.argsort(sparse)[::-1][:k]):
            rrf[int(idx)] += 1.0 / (RRF_K + rank + 1)
        order = [i for i, _ in sorted(rrf.items(), key=lambda x: x[1], reverse=True)]
        return order, dense, sparse

    # ---------- generation ----------
    def _llm(self, messages, temperature=0.3, max_tokens=700, clean=True):
        """API call with exponential backoff; returns '' on failure (caller falls
        back to the template answer). `clean=False` keeps the reply verbatim, which
        JSON replies need since markdown stripping would corrupt them."""
        if not LLM_API_KEY:
            return ""
        if self._client is None:
            self._client = OpenAI(api_key=LLM_API_KEY, base_url=LLM_BASE_URL)

        for attempt in range(1, LLM_MAX_RETRIES + 1):
            try:
                resp = self._client.chat.completions.create(
                    model=LLM_MODEL, messages=messages,
                    temperature=temperature, max_tokens=max_tokens)
                text = (resp.choices[0].message.content or "").strip()
                return _clean_markdown(text) if clean else text
            except (RateLimitError, APIConnectionError, APITimeoutError) as e:
                if attempt == LLM_MAX_RETRIES:
                    return ""
                delay = LLM_BASE_DELAY * (2 ** (attempt - 1))
                time.sleep(delay)
            except Exception as e:
                return ""
        return ""

    @staticmethod
    def _template_one(row, fields):
        return f"📌 {row['job_title']}\n\n" + build_context(row, fields, include_title=False)

    @staticmethod
    def _template_two(row1, row2, fields):
        return (f"🔗 نقش تلفیقی: {row1['job_title']} + {row2['job_title']}\n\n"
                f"— {row1['job_title']}:\n{build_context(row1, fields, include_title=False)}\n\n"
                f"— {row2['job_title']}:\n{build_context(row2, fields, include_title=False)}")

    # ---------- job generation ----------
    def _generate_job(self, question, neighbour_idxs):
        """Designs a new occupation record from the user's spec. Returns a dict with
        the dataset's own columns (so it can be stored as a suggestion), or None if
        the API is unavailable or its reply is unusable."""
        reference = "\n\n".join(
            f"نمونه {n + 1}:\n{build_context(self.df.iloc[i], DISCOVERY_FIELDS)}"
            for n, i in enumerate(neighbour_idxs))

        raw = self._llm([
            {"role": "system", "content": SYSTEM_JOB_GENERATE},
            {"role": "user", "content":
                f"درخواست کاربر:\n{question}\n\n"
                f"مشاغل مشابه موجود (فقط برای الگوی سبک نگارش و پرهیز از تکرار):\n{reference}"},
        ], temperature=0.5, max_tokens=900, clean=False)

        obj = _parse_json_object(raw)
        if obj is None:
            return None
        draft = {c: normalize_text(obj.get(c, "")) for c in EXPECTED_COLUMNS}
        for col in PROSE_COLUMNS:
            draft[col] = re.sub(r"\s*\|\s*", "، ", draft[col]).strip("، ")
        return draft if draft["job_title"] else None

    @staticmethod
    def _render_draft(draft, related):
        """Formats the *offer*: the proposal is summarized to its title and one-line
        description, then the user is asked whether to register it. The full record
        travels in `job_draft`, so a client fills its suggestion form from there
        rather than parsing this text back apart."""
        lines = [DRAFT_HEADER, "", f"📌 {FIELD_LABELS['job_title']}: {draft['job_title']}"]
        if draft.get("description"):
            lines.append(f"{FIELD_LABELS['description']}: {draft['description']}")
        if related:
            lines += ["", f"{RELATED_LABEL}: " + "، ".join(related)]
        lines += ["", DRAFT_QUESTION]
        return "\n".join(lines)

    def _discover(self, question, q_norm, use_llm=True):
        """Job-request path: return a close existing job, or design a new one."""
        order, dense, sparse = self._retrieve(q_norm)
        i1 = order[0]
        s1_dense, s1_sparse = float(dense[i1]), float(sparse[i1])
        related = [self.df.iloc[i]["job_title"] for i in order[:DISCOVERY_RELATED]]

        # Nothing in the request relates to work at all -> do not invent a job
        if s1_dense < DISCOVERY_FLOOR and s1_sparse < THRESHOLD_SPARSE:
            return {"mode": "out_of_domain", "intent": "job_request",
                    "score": s1_dense, "answer": OOD_MESSAGE}

        if s1_dense >= DISCOVERY_MATCH:
            row = self.df.iloc[i1]
            ans = self._llm([
                {"role": "system", "content": SYSTEM_JOB_MATCH},
                {"role": "user", "content":
                    f"اطلاعات شغل:\n{build_context(row, DISCOVERY_FIELDS)}\n\n"
                    f"توصیف کاربر: {question}"},
            ]) if use_llm else ""
            if not ans:
                ans = f"{MATCH_HEADER}\n\n{self._template_one(row, DISCOVERY_FIELDS)}"
            return {"mode": "job_match", "intent": "job_request",
                    "job": row["job_title"], "score": s1_dense,
                    "related_jobs": related, "answer": ans}

        draft = self._generate_job(question, order[:DISCOVERY_RELATED]) if use_llm else None
        if draft is None:
            # Generation needs the API; without it the nearest records are the best we have
            return {"mode": "out_of_domain", "intent": "job_request",
                    "score": s1_dense, "related_jobs": related,
                    "answer": DISCOVERY_UNAVAILABLE + "\n" + "\n".join(f"- {t}" for t in related)}

        return {"mode": "job_generated", "intent": "job_request",
                "job": draft["job_title"], "score": s1_dense,
                "job_draft": draft, "related_jobs": related,
                "answer": self._render_draft(draft, related)}

    # ---------- public API ----------
    def answer(self, question, use_llm=True):
        """Answers one question. Returns a dict with keys:
        mode ('single'|'interdisciplinary'|'job_match'|'job_generated'|'out_of_domain'),
        intent, answer, plus job/score fields depending on mode. 'job_generated' is an
        offer the user still has to accept; it carries 'job_draft', the proposed record
        in the dataset's own columns, for the client to prefill its form with."""
        q = normalize_text(question)

        # A described spec is a different task from a question about a known job
        if is_job_request(q):
            return self._discover(question, q, use_llm)

        intent = detect_intent(q)

        # Bare job names ("معلم جغرافیا") carry no question verb -> description request
        tokens = set(q.split())
        is_question = ("؟" in q) or ("?" in q) or bool(tokens & QUESTION_WORDS)
        if intent == "general" and len(tokens) <= 4 and not is_question:
            intent = "description"
        fields = INTENT_TO_FIELDS.get(intent, INTENT_TO_FIELDS["general"])

        order, dense, sparse = self._retrieve(q)
        i1 = order[0]
        s1_dense, s1_sparse = float(dense[i1]), float(sparse[i1])

        # Out-of-domain only if BOTH channels are weak
        if s1_dense < THRESHOLD_MATCH and s1_sparse < THRESHOLD_SPARSE:
            return {"mode": "out_of_domain", "intent": intent,
                    "score": s1_dense, "answer": OOD_MESSAGE}

        # First candidate that is NOT a near-duplicate of the leader
        i2 = next((c for c in order[1:SCAN_DEPTH + 1]
                   if float(self.emb_full[i1] @ self.emb_full[c]) < PAIR_SIM_MAX), None)

        explicit = any(k in q for k in EXPLICIT_COMBO_WORDS)
        interdisciplinary, s2_dense = False, None
        if i2 is not None:
            s2_dense = float(dense[i2])
            interdisciplinary = (
                (s2_dense >= SECONDARY_MIN and (s1_dense - s2_dense) <= SECONDARY_MARGIN)
                or (explicit and s2_dense >= THRESHOLD_MATCH - 0.05)
            )

        row1 = self.df.iloc[i1]

        if interdisciplinary:
            row2 = self.df.iloc[i2]
            ans = self._llm([
                {"role": "system", "content": SYSTEM_INTERDISCIPLINARY},
                {"role": "user", "content":
                    f"شغل اول:\n{build_context(row1, fields)}\n\n"
                    f"شغل دوم:\n{build_context(row2, fields)}\n\nسوال کاربر: {question}"},
            ]) if use_llm else ""
            if not ans:
                ans = self._template_two(row1, row2, fields)
            return {"mode": "interdisciplinary", "intent": intent,
                    "jobs": [row1["job_title"], row2["job_title"]],
                    "scores": [s1_dense, s2_dense], "answer": ans}

        ans = self._llm([
            {"role": "system", "content": SYSTEM_SINGLE},
            {"role": "user", "content":
                f"اطلاعات شغل:\n{build_context(row1, fields)}\n\nسوال کاربر: {question}"},
        ]) if use_llm else ""
        if not ans:
            ans = self._template_one(row1, fields)
        return {"mode": "single", "intent": intent, "job": row1["job_title"],
                "score": s1_dense, "answer": ans}


# =========================================================
# Standalone demo
# =========================================================
if __name__ == "__main__":
    engine = JobQAEngine(OCCUPATIONS_PATH)
    print("✅ Ready. Ask your question (or 'خروج').")
    while True:
        try:
            question = input("\n❓ سوال: ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if question.lower() in ["exit", "quit", "خروج"]:
            break
        if not question:
            continue
        try:
            res = engine.answer(question)
        except Exception as e:
            continue
        print(f"\nmode: {res['mode']} | intent: {res['intent']}")
        if res["mode"] in ("single", "job_match", "job_generated"):
            print(f"job: {res['job']} (score={res['score']:.3f})")
        elif res["mode"] == "interdisciplinary":
            print(f"jobs: {res['jobs'][0]} + {res['jobs'][1]}")
        if res.get("related_jobs"):
            print(f"related: {'، '.join(res['related_jobs'])}")
        print(f"\n🤖 {res['answer']}")

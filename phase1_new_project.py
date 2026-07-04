# -*- coding: utf-8 -*-
"""
Occupation Q&A system (RAG) — hybrid dense + BM25 edition

Core logic (kept from the original design):
- Dense hybrid retrieval: W_FULL * full-text + W_TITLE * (title+aliases) embeddings
- Keyword-based intent detection (selects display fields, never retrieval)
- Interdisciplinary detection guarded by SECONDARY_MIN / SECONDARY_MARGIN / PAIR_SIM_MAX

Engineering layer (new):
- Sparse BM25 channel fused with the dense ranking via Reciprocal Rank Fusion (RRF)
- Dual-gate out-of-domain check (dense OR sparse must clear its threshold)
- API error handling: retry with exponential backoff + automatic fallback to templates
- Markdown stripping so raw ** or # never reaches the end user
- Quantitative evaluation (--eval): MRR / Hit@1 / Hit@3 measured on the SAME
  retrieval function used in production, plus automatic threshold suggestion
  from labeled in-domain + out-of-domain questions

Run:
    python job_qa_hybrid.py                       # interactive, uses the API
    python job_qa_hybrid.py --local               # local LLM instead of the API
    python job_qa_hybrid.py --no-llm              # templates only, no generation
    python job_qa_hybrid.py --rebuild             # force-rebuild embeddings
    python job_qa_hybrid.py --calibrate           # print top-5 scores for probe queries
    python job_qa_hybrid.py --eval eval_questions.csv   # metrics + threshold suggestion

Eval file format (CSV, UTF-8): columns `question,expected`
    - in-domain rows: expected = (part of) the correct job_title
    - out-of-domain rows: leave expected EMPTY -> used for threshold suggestion

Env vars:
    OPENAI_API_KEY, OPENAI_BASE_URL, LLM_MODEL, HF_TOKEN (all optional except the
    API key when API generation is used)
"""

import os
import re
import time
import math
import logging
import argparse
from collections import Counter, defaultdict

import numpy as np
import pandas as pd
from sentence_transformers import SentenceTransformer

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

try:
    from openai import OpenAI, RateLimitError, APIConnectionError, APITimeoutError
    _RETRY_EXCEPTIONS = (RateLimitError, APIConnectionError, APITimeoutError)
    _HAS_OPENAI = True
except Exception:
    _RETRY_EXCEPTIONS = tuple()
    _HAS_OPENAI = False

logging.basicConfig(level=logging.INFO,
                    format="[%(asctime)s] %(levelname)s: %(message)s",
                    datefmt="%H:%M:%S")
log = logging.getLogger("job_qa")

# Optional HF login (speeds up downloads). Never hardcode the token here.
_HF_TOKEN = os.getenv("HF_TOKEN")
if _HF_TOKEN:
    try:
        from huggingface_hub import login
        login(token=_HF_TOKEN)
    except Exception as e:
        log.warning(f"HF login failed (cached models still work): {e}")


# =========================================================
# 0) Config
# =========================================================
EMBED_MODEL_NAME = os.getenv("EMBED_MODEL_NAME", "BAAI/bge-m3")
DATA_PATH = "Merged_Occupations.xlsx"
EMB_CACHE_DIR = "emb_cache"

# Dense hybrid weights: score = W_FULL * full-text + W_TITLE * (title+aliases)
W_FULL  = 0.6
W_TITLE = 0.4

# Retrieval fusion
RRF_K = 60            # standard RRF constant
MAX_CANDIDATES = 15   # candidates taken from each channel before fusion
SCAN_DEPTH = 5        # how deep to look for a *distinct* secondary job

# Thresholds — refine them with `--eval` (it prints a data-driven suggestion).
THRESHOLD_MATCH  = 0.78   # dense gate: best hybrid score below this AND ...
THRESHOLD_SPARSE = 0.45   # ... sparse gate: best normalized BM25 below this -> out of domain
SECONDARY_MIN    = 0.76   # 2nd job must be at least this relevant (dense) for interdisciplinary
SECONDARY_MARGIN = 0.03   # ... AND within this margin of the 1st job
PAIR_SIM_MAX     = 0.92   # if the two jobs are MORE similar than this to each other,
                          # they are near-duplicates (same field) -> stay single

# Text-generation API (OpenAI-compatible)
LLM_MODEL    = os.getenv("LLM_MODEL", "gpt-4o-mini")
LLM_BASE_URL = os.getenv("OPENAI_BASE_URL")          # e.g. a relay/proxy service
LLM_API_KEY  = os.getenv("OPENAI_API_KEY")
LLM_MAX_RETRIES = 3
LLM_BASE_DELAY  = 2.0   # seconds; doubles per attempt

SYSTEM_SINGLE = (
    "تو موتور پاسخ‌گویی یک اپلیکیشن رسمی معرفی مشاغل هستی و خروجی تو مستقیماً و بدون ویرایش "
    "به کاربر نهایی نمایش داده می‌شود. قوانین را دقیقاً رعایت کن:\n"
    "1) پاسخ را مستقیم با خودِ جواب شروع کن؛ هیچ مقدمه، سلام، یا جمله‌ای مانند «سوال شما درباره...» ننویس.\n"
    "2) فقط بر اساس «اطلاعات شغل» داده‌شده پاسخ بده و از دانش خودت چیزی اضافه نکن.\n"
    "3) لحن رسمی و کتابی فارسی؛ از لحن محاوره‌ای و تعارف پرهیز کن.\n"
    "4) خروجی متن ساده باشد؛ به هیچ وجه از Markdown (ستاره، #، بک‌تیک) استفاده نکن. "
    "اگر فهرست لازم بود، هر مورد را در یک خط با خط تیره (-) بنویس.\n"
    "5) کوتاه و دقیق: حداکثر پنج جمله یا چند مورد فهرستی کوتاه. جمع‌بندی و توضیح اضافه ممنوع.\n"
    "6) اگر پاسخ در داده‌ها نبود فقط بنویس: «اطلاعات کافی در این مورد موجود نیست.»"
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


# =========================================================
# 1) Schema, labels and intents
# =========================================================
EXPECTED_COLUMNS = [
    "job_title", "aliases", "tools", "skills",
    "work_context", "career_path_next", "description", "responsibilities",
]

FIELD_LABELS = {
    "job_title":        "عنوان شغل",
    "aliases":          "نام‌های دیگر",
    "tools":            "ابزارها",
    "skills":           "مهارت‌ها و شایستگی‌ها",
    "work_context":     "محیط کاری",
    "career_path_next": "مسیر شغلی بعدی",
    "description":      "شرح شغل",
    "responsibilities": "وظایف و مسئولیت‌ها",
}

INTENT_TO_FIELDS = {
    "description":      ["description"],
    "responsibilities": ["responsibilities"],
    "competencies":     ["skills"],
    "tools":            ["tools"],
    "career_path":      ["career_path_next"],
    "work_context":     ["work_context"],
    "aliases":          ["aliases"],
    "general":          ["description", "responsibilities", "skills", "tools"],
}

# Keyword-based intent detection (add words here as needed)
INTENT_KEYWORDS = {
    "responsibilities": ["وظایف", "وظیفه", "مسئولیت", "روزمره", "کارها", "چه کاری", "چیکار", "چه کار"],
    "tools":            ["ابزار", "نرم‌افزار", "نرم افزار", "برنامه", "تجهیزات", "سیستم", "با چی"],
    "competencies":     ["مهارت", "شایستگی", "توانایی", "توانمندی", "ویژگی", "دقت", "استعداد", "باید بلد"],
    "career_path":      ["ارتقا", "ارتقاء", "آینده", "پیشرفت", "مسیر", "بعدش", "ترفیع", "رشد"],
    "work_context":     ["محیط", "فضا", "شرایط کاری", "کجا کار", "محل کار"],
    "aliases":          ["نام دیگر", "اسم دیگر", "معادل"],
    "description":      ["معرفی", "شرح", "چیست", "توضیح", "درباره", "چیه"],
}

EXPLICIT_COMBO_WORDS = ["بین رشته", "بین‌رشته", "ترکیب", "هر دو", "هردو", "تلفیق", "میان‌رشته"]


# =========================================================
# 2) Text normalization (identical for query and corpus)
# =========================================================
def normalize_text(text):
    if text is None or (isinstance(text, float) and pd.isna(text)):
        return ""
    text = str(text).replace("ي", "ی").replace("ك", "ک")   # Arabic -> Persian chars
    text = " ".join(text.split())                            # collapse whitespace
    if _normalizer:
        text = _normalizer.normalize(text)
    return text.strip()


# =========================================================
# 3) Load and prepare data
# =========================================================
def build_combined_text(row):
    """Full text for the dense 'full' embedding. Important fields come first, so if
    the encoder truncates at its max sequence length, the tail (least critical
    fields) is what gets cut."""
    parts = [
        f"{FIELD_LABELS['job_title']}: {row['job_title']}",
        f"{FIELD_LABELS['aliases']}: {row['aliases'].replace('|', '،')}",
        f"{FIELD_LABELS['description']}: {row['description']}",
        f"{FIELD_LABELS['responsibilities']}: {row['responsibilities']}",
        f"{FIELD_LABELS['skills']}: {row['skills']}",
        f"{FIELD_LABELS['tools']}: {row['tools']}",
        f"{FIELD_LABELS['work_context']}: {row['work_context']}",
        f"{FIELD_LABELS['career_path_next']}: {row['career_path_next']}",
    ]
    return " . ".join(p for p in parts if p.split(": ", 1)[-1].strip())


def build_title_alias_text(row):
    """Short text (title + aliases only) for the name-focused embedding."""
    aliases = row["aliases"].replace("|", "،")
    return f"{row['job_title']} ، {aliases}".strip(" ،")


def load_jobs_data(file_path=DATA_PATH):
    df = pd.read_excel(file_path)
    df.columns = [str(c).strip().lower() for c in df.columns]

    # Schema check: warn loudly on missing columns instead of failing silently
    missing = [c for c in EXPECTED_COLUMNS if c not in df.columns]
    if missing:
        log.warning(f"Dataset is missing expected columns (filled as empty): {missing}")

    for col in EXPECTED_COLUMNS:
        if col not in df.columns:
            df[col] = ""
        df[col] = df[col].map(normalize_text)

    df = df[df["job_title"].str.len() > 0].reset_index(drop=True)
    df["combined_text"] = df.apply(build_combined_text, axis=1)
    return df


# =========================================================
# 4) Intent detection (keyword-based; selects display fields only)
# =========================================================
def detect_intent(question):
    q = question.strip()
    for intent, kws in INTENT_KEYWORDS.items():
        if any(k in q for k in kws):
            return intent
    return "general"


# =========================================================
# 5) Dense embeddings (auto E5 prefix handling)
# =========================================================
def _is_e5(name):
    return "e5" in name.lower()


def encode_passages(model, texts):
    if _is_e5(EMBED_MODEL_NAME):
        texts = [f"passage: {t}" for t in texts]
    return model.encode(texts, normalize_embeddings=True, show_progress_bar=False)


def encode_queries(model, texts):
    if _is_e5(EMBED_MODEL_NAME):
        texts = [f"query: {t}" for t in texts]
    return model.encode(texts, normalize_embeddings=True, show_progress_bar=False)


def get_corpus_embeddings(df, model, rebuild=False):
    """Caches TWO matrices (full / title), keyed by model name + row count."""
    os.makedirs(EMB_CACHE_DIR, exist_ok=True)
    tag = EMBED_MODEL_NAME.replace("/", "_")
    path = os.path.join(EMB_CACHE_DIR, f"corpus_{tag}_{len(df)}.npz")

    if os.path.exists(path) and not rebuild:
        data = np.load(path)
        if {"full", "title"} <= set(data.files) and len(data["full"]) == len(df):
            log.info("Loaded embeddings from cache.")
            return data["full"], data["title"]
        log.warning("Cache is stale or has an old format. Rebuilding...")

    log.info("Building embeddings (this may take a minute)...")
    emb_full = encode_passages(model, df["combined_text"].tolist())
    emb_title = encode_passages(model, df.apply(build_title_alias_text, axis=1).tolist())
    np.savez(path, full=emb_full, title=emb_title)
    log.info("Embeddings saved.")
    return emb_full, emb_title


# =========================================================
# 6) Sparse channel: BM25 (lexical matching for exact names/terms)
# =========================================================
class BM25:
    K1 = 1.5
    B = 0.75

    def __init__(self, texts):
        corpus_tokens = [t.lower().split() for t in texts]
        self.doc_count = len(corpus_tokens)
        self.doc_lengths = np.array([len(d) for d in corpus_tokens], dtype=np.float32)
        self.avg_len = float(np.mean(self.doc_lengths)) if self.doc_count else 1.0

        self.inverted = defaultdict(dict)
        for doc_id, tokens in enumerate(corpus_tokens):
            for tok, cnt in Counter(tokens).items():
                self.inverted[tok][doc_id] = cnt

        self.idf = {
            tok: math.log((self.doc_count - len(dd) + 0.5) / (len(dd) + 0.5) + 1.0)
            for tok, dd in self.inverted.items()
        }

    def score(self, query):
        """Returns max-normalized scores in [0, 1] for the whole corpus."""
        scores = np.zeros(self.doc_count, dtype=np.float32)
        for tok in set(query.lower().split()):
            dd = self.inverted.get(tok)
            if not dd:
                continue
            idf = self.idf[tok]
            idxs = np.fromiter(dd.keys(), dtype=np.int64)
            tfs = np.fromiter(dd.values(), dtype=np.float32)
            lens = self.doc_lengths[idxs]
            denom = tfs + self.K1 * (1.0 - self.B + self.B * lens / self.avg_len)
            scores[idxs] += idf * tfs * (self.K1 + 1.0) / denom
        m = scores.max()
        return scores / m if m > 0 else scores


# =========================================================
# 7) Production retrieval (single source of truth — eval uses THIS)
# =========================================================
def retrieve(q_norm, model, emb_full, emb_title, bm25):
    """Fuses the dense hybrid ranking and the BM25 ranking with RRF.
    Returns (rrf_order, dense_scores, sparse_scores)."""
    q_emb = encode_queries(model, [q_norm])[0]              # embeddings are L2-normalized
    dense = W_FULL * (emb_full @ q_emb) + W_TITLE * (emb_title @ q_emb)
    sparse = bm25.score(q_norm)

    k = min(MAX_CANDIDATES, len(dense))
    dense_rank = np.argsort(dense)[::-1][:k]
    sparse_rank = np.argsort(sparse)[::-1][:k]

    rrf = defaultdict(float)
    for rank, idx in enumerate(dense_rank):
        rrf[int(idx)] += 1.0 / (RRF_K + rank + 1)
    for rank, idx in enumerate(sparse_rank):
        rrf[int(idx)] += 1.0 / (RRF_K + rank + 1)

    order = [idx for idx, _ in sorted(rrf.items(), key=lambda x: x[1], reverse=True)]
    return order, dense, sparse


# =========================================================
# 8) Generation: retry with backoff, markdown stripping, template fallback
# =========================================================
def _clean_markdown(text):
    text = re.sub(r"\*\*(.*?)\*\*", r"\1", text)
    text = re.sub(r"\*(.*?)\*", r"\1", text)
    text = re.sub(r"`(.*?)`", r"\1", text)
    text = re.sub(r"#{1,6}\s?", "", text)
    return text.strip()


_openai_client = None
def llm_generate(messages, temperature=0.3, max_tokens=700, **_):
    """API generation with exponential backoff. Returns '' on total failure,
    which callers treat as 'fall back to the template answer'."""
    global _openai_client
    if not _HAS_OPENAI:
        log.error("openai package is not installed; falling back to templates.")
        return ""
    if not LLM_API_KEY:
        log.error("OPENAI_API_KEY is not set; falling back to templates.")
        return ""
    if _openai_client is None:
        _openai_client = OpenAI(api_key=LLM_API_KEY, base_url=LLM_BASE_URL)

    for attempt in range(1, LLM_MAX_RETRIES + 1):
        try:
            resp = _openai_client.chat.completions.create(
                model=LLM_MODEL, messages=messages,
                temperature=temperature, max_tokens=max_tokens,
            )
            return _clean_markdown((resp.choices[0].message.content or "").strip())
        except _RETRY_EXCEPTIONS as e:
            if attempt == LLM_MAX_RETRIES:
                log.error(f"API retries exhausted: {e}")
                return ""
            delay = LLM_BASE_DELAY * (2 ** (attempt - 1))
            log.warning(f"API transient error ({e.__class__.__name__}); retrying in {delay:.0f}s...")
            time.sleep(delay)
        except Exception as e:
            log.error(f"API unexpected failure: {e}")
            return ""
    return ""


_local_pipe = None
def llm_generate_local(messages, temperature=0.3, max_tokens=400, **_):
    """Local fallback generator. Returns '' on failure."""
    global _local_pipe
    try:
        if _local_pipe is None:
            from transformers import pipeline
            _local_pipe = pipeline("text-generation",
                                   model="Qwen/Qwen2.5-3B-Instruct",
                                   device_map="auto")
        out = _local_pipe(messages, max_new_tokens=max_tokens, temperature=temperature,
                          do_sample=True, return_full_text=False,
                          pad_token_id=_local_pipe.tokenizer.eos_token_id)
        g = out[0]["generated_text"]
        text = g[-1]["content"] if isinstance(g, list) else g
        return _clean_markdown(str(text).strip())
    except Exception as e:
        log.error(f"Local LLM failure: {e}")
        return ""


def build_context(row, fields, include_title=True):
    lines = []
    if include_title:
        lines.append(f"{FIELD_LABELS['job_title']}: {row['job_title']}")
        if row.get("aliases"):
            lines.append(f"{FIELD_LABELS['aliases']}: {row['aliases']}")
    for f in fields:
        v = row.get(f, "")
        if v:
            lines.append(f"{FIELD_LABELS.get(f, f)}: {v}")
    return "\n".join(lines)


def simple_answer_one(row, intent):
    fields = INTENT_TO_FIELDS.get(intent, INTENT_TO_FIELDS["general"])
    return f"📌 {row['job_title']}\n\n" + build_context(row, fields, include_title=False)


def simple_answer_two(row1, row2, intent):
    fields = INTENT_TO_FIELDS.get(intent, INTENT_TO_FIELDS["general"])
    return (
        f"🔗 نقش تلفیقی: {row1['job_title']} + {row2['job_title']}\n\n"
        f"— {row1['job_title']}:\n{build_context(row1, fields, include_title=False)}\n\n"
        f"— {row2['job_title']}:\n{build_context(row2, fields, include_title=False)}"
    )


# =========================================================
# 9) Answer engine
# =========================================================
def answer_question(question, df, emb_full, emb_title, bm25, model, gen_fn, use_llm=True):
    q = normalize_text(question)
    intent = detect_intent(q)
    fields = INTENT_TO_FIELDS.get(intent, INTENT_TO_FIELDS["general"])

    order, dense, sparse = retrieve(q, model, emb_full, emb_title, bm25)
    i1 = order[0]
    s1_dense, s1_sparse = float(dense[i1]), float(sparse[i1])

    # Dual-gate out-of-domain: reject only if BOTH channels are weak
    if s1_dense < THRESHOLD_MATCH and s1_sparse < THRESHOLD_SPARSE:
        return {"mode": "out_of_domain", "intent": intent,
                "score": s1_dense, "sparse": s1_sparse,
                "answer": "متاسفانه در دیتابیس من اطلاعاتی درباره این موضوع پیدا نشد."}

    # Find the first DISTINCT secondary candidate (skip near-duplicates of the leader)
    i2 = None
    for cand in order[1:SCAN_DEPTH + 1]:
        if float(emb_full[i1] @ emb_full[cand]) < PAIR_SIM_MAX:
            i2 = cand
            break

    explicit = any(k in q for k in EXPLICIT_COMBO_WORDS)
    interdisciplinary = False
    s2_dense = None
    if i2 is not None:
        s2_dense = float(dense[i2])
        interdisciplinary = (
            (s2_dense >= SECONDARY_MIN and (s1_dense - s2_dense) <= SECONDARY_MARGIN)
            or (explicit and s2_dense >= THRESHOLD_MATCH - 0.05)
        )

    row1 = df.iloc[i1]

    if interdisciplinary:
        row2 = df.iloc[i2]
        ans = ""
        if use_llm:
            messages = [
                {"role": "system", "content": SYSTEM_INTERDISCIPLINARY},
                {"role": "user", "content":
                    f"شغل اول:\n{build_context(row1, fields)}\n\n"
                    f"شغل دوم:\n{build_context(row2, fields)}\n\nسوال کاربر: {question}"},
            ]
            ans = gen_fn(messages)
            if not ans:
                log.warning("Generation failed; using the template answer instead.")
        if not ans:
            ans = simple_answer_two(row1, row2, intent)
        return {"mode": "interdisciplinary", "intent": intent,
                "jobs": [row1["job_title"], row2["job_title"]],
                "scores": [s1_dense, s2_dense], "answer": ans}

    ans = ""
    if use_llm:
        messages = [
            {"role": "system", "content": SYSTEM_SINGLE},
            {"role": "user", "content":
                f"اطلاعات شغل:\n{build_context(row1, fields)}\n\nسوال کاربر: {question}"},
        ]
        ans = gen_fn(messages)
        if not ans:
            log.warning("Generation failed; using the template answer instead.")
    if not ans:
        ans = simple_answer_one(row1, intent)
    return {"mode": "single", "intent": intent, "job": row1["job_title"],
            "score": s1_dense, "sparse": s1_sparse, "answer": ans}


# =========================================================
# 10) Quantitative evaluation + data-driven threshold suggestion
# =========================================================
def _title_match(expected, got):
    e, g = normalize_text(expected).lower(), normalize_text(got).lower()
    return bool(e) and (e in g or g in e)


def evaluate(eval_path, df, emb_full, emb_title, bm25, model):
    """Reads a CSV (question,expected). Empty `expected` = out-of-domain probe.
    Metrics are computed on the exact production `retrieve()` function."""
    ev = pd.read_csv(eval_path).fillna("")
    if not {"question", "expected"} <= set(ev.columns):
        raise ValueError("Eval file must have columns: question,expected")

    in_domain = ev[ev["expected"].str.strip() != ""]
    ood = ev[ev["expected"].str.strip() == ""]

    rr_sum, hit1, hit3 = 0.0, 0, 0
    correct_top1_scores, misses = [], []
    interdisc_fires = 0

    for _, r in in_domain.iterrows():
        q = normalize_text(r["question"])
        order, dense, _ = retrieve(q, model, emb_full, emb_title, bm25)
        titles = [df.iloc[i]["job_title"] for i in order]

        rank = next((p + 1 for p, t in enumerate(titles) if _title_match(r["expected"], t)), None)
        if rank == 1:
            hit1 += 1
            correct_top1_scores.append(float(dense[order[0]]))
        if rank is not None and rank <= 3:
            hit3 += 1
        rr_sum += (1.0 / rank) if rank else 0.0
        if rank is None or rank > 3:
            misses.append((r["question"], r["expected"], titles[:3]))

        # Would this single-job question wrongly trigger interdisciplinary mode?
        i1 = order[0]
        i2 = next((c for c in order[1:SCAN_DEPTH + 1]
                   if float(emb_full[i1] @ emb_full[c]) < PAIR_SIM_MAX), None)
        if i2 is not None:
            s1d, s2d = float(dense[i1]), float(dense[i2])
            if s2d >= SECONDARY_MIN and (s1d - s2d) <= SECONDARY_MARGIN:
                interdisc_fires += 1

    ood_top_scores = []
    for _, r in ood.iterrows():
        q = normalize_text(r["question"])
        order, dense, sparse = retrieve(q, model, emb_full, emb_title, bm25)
        ood_top_scores.append((float(dense[order[0]]), float(sparse[order[0]])))

    n = len(in_domain)
    print("\n" + "=" * 56)
    print("EVALUATION REPORT (production retrieval path)")
    print("=" * 56)
    if n:
        print(f"In-domain questions : {n}")
        print(f"Hit@1               : {hit1 / n * 100:.1f}%")
        print(f"Hit@3               : {hit3 / n * 100:.1f}%")
        print(f"MRR                 : {rr_sum / n:.4f}")
        print(f"Interdisciplinary fired on single-job questions: {interdisc_fires}/{n}"
              f"  (should be ~0; if high, lower SECONDARY_MARGIN)")
    if misses:
        print("\nMisses (expected not in top-3):")
        for q, e, top3 in misses[:10]:
            print(f"  Q: {q}\n     expected: {e} | got: {top3}")

    # --- Data-driven threshold suggestion ---
    if correct_top1_scores and ood_top_scores:
        min_correct = min(correct_top1_scores)
        max_ood_dense = max(s for s, _ in ood_top_scores)
        max_ood_sparse = max(s for _, s in ood_top_scores)
        print("\nThreshold suggestion (dense gate):")
        print(f"  lowest correct top-1 dense score : {min_correct:.4f}")
        print(f"  highest out-of-domain dense score: {max_ood_dense:.4f}")
        if max_ood_dense < min_correct:
            mid = (max_ood_dense + min_correct) / 2
            print(f"  -> clean separation; set THRESHOLD_MATCH ≈ {mid:.3f}")
        else:
            print("  -> ranges overlap; add more eval rows or raise THRESHOLD_MATCH "
                  "toward the correct-score side and accept a few rejections.")
        print(f"  highest out-of-domain sparse score: {max_ood_sparse:.4f}"
              f"  -> keep THRESHOLD_SPARSE above this (current: {THRESHOLD_SPARSE})")
    elif not ood_top_scores:
        print("\nNo out-of-domain rows in the eval file -> cannot suggest thresholds. "
              "Add a few rows with an empty `expected` column.")
    print("=" * 56 + "\n")


def calibrate(df, emb_full, emb_title, bm25, model, queries):
    """Quick ad-hoc score inspection for probe queries (incl. out-of-domain ones)."""
    print("\n=== Score calibration (production retrieval path) ===")
    for qq in queries:
        q = normalize_text(qq)
        order, dense, sparse = retrieve(q, model, emb_full, emb_title, bm25)
        print(f"\n❓ {qq}")
        for i in order[:5]:
            print(f"   dense={dense[i]:.4f} sparse={sparse[i]:.4f}  {df.iloc[i]['job_title']}")


# =========================================================
# 11) Main
# =========================================================
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", default=DATA_PATH)
    parser.add_argument("--rebuild", action="store_true", help="force-rebuild embeddings")
    parser.add_argument("--local", action="store_true", help="use a local model instead of the API")
    parser.add_argument("--no-llm", action="store_true", help="plain templates only, no generation")
    parser.add_argument("--calibrate", action="store_true", help="print scores for probe queries")
    parser.add_argument("--eval", metavar="CSV", help="run MRR/Hit@k evaluation + threshold suggestion")
    args = parser.parse_args()

    df = load_jobs_data(args.data)
    log.info(f"Loaded {len(df)} occupations.")

    device = "cuda" if _HAS_CUDA else "cpu"
    log.info(f"Loading embedding model on {device.upper()} ...")
    model = SentenceTransformer(EMBED_MODEL_NAME, device=device)
    emb_full, emb_title = get_corpus_embeddings(df, model, rebuild=args.rebuild)

    log.info("Building BM25 index...")
    bm25 = BM25(df["combined_text"].tolist())

    if args.eval:
        evaluate(args.eval, df, emb_full, emb_title, bm25, model)
        return

    if args.calibrate:
        calibrate(df, emb_full, emb_title, bm25, model, [
            "وظایف افسر توپخانه چیست؟",
            "ابزارهای فرمانده تانک چیست؟",
            "شغلی که هم با رادار و هم با موشک کار کند چیست؟",
            "مسیر ارتقای افسر مرکز فرماندهی",
            # Out-of-domain probes — thresholds must sit ABOVE these scores
            "طرز تهیه قرمه‌سبزی چیست؟",
            "بهترین گوشی سال کدام است؟",
        ])
        return

    use_llm = not args.no_llm
    gen_fn = llm_generate_local if args.local else llm_generate

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
            res = answer_question(question, df, emb_full, emb_title, bm25,
                                  model, gen_fn, use_llm=use_llm)
        except Exception as e:
            log.error(f"Unexpected error while answering: {e}")
            print("خطایی رخ داد؛ لطفاً دوباره تلاش کنید.")
            continue

        print("\n" + "-" * 40)
        print(f"mode: {res['mode']} | intent: {res['intent']}")
        if res["mode"] == "single":
            print(f"job: {res['job']}  (dense={res['score']:.3f}, sparse={res.get('sparse', 0):.3f})")
        elif res["mode"] == "interdisciplinary":
            print(f"jobs: {res['jobs'][0]} + {res['jobs'][1]}  "
                  f"(dense={res['scores'][0]:.3f}, {res['scores'][1]:.3f})")
        print("\n🤖 پاسخ:")
        print(res["answer"])
        print("-" * 40)


if __name__ == "__main__":
    main()
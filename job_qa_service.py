# -*- coding: utf-8 -*-
"""
Persian Occupation Q&A engine (RAG) — production backend module.

Pipeline: hybrid dense retrieval (full-text + title/alias embeddings, BAAI/bge-m3)
fused with BM25 via Reciprocal Rank Fusion -> dual-gate out-of-domain check ->
single or interdisciplinary answer generated through an OpenAI-compatible API,
with automatic template fallback on any API failure.

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

EXPECTED_COLUMNS = ["job_title", "aliases", "tools", "skills",
                    "work_context", "career_path_next", "description", "responsibilities"]

FIELD_LABELS = {
    "job_title": "عنوان شغل", "aliases": "نام‌های دیگر", "tools": "ابزارها",
    "skills": "مهارت‌ها و شایستگی‌ها", "work_context": "محیط کاری",
    "career_path_next": "مسیر شغلی بعدی", "description": "شرح شغل",
    "responsibilities": "وظایف و مسئولیت‌ها",
}

INTENT_TO_FIELDS = {
    "description": ["description"], "responsibilities": ["responsibilities"],
    "competencies": ["skills"], "tools": ["tools"], "career_path": ["career_path_next"],
    "work_context": ["work_context"], "aliases": ["aliases"],
    "general": ["description", "responsibilities", "skills", "tools"],
}

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
QUESTION_WORDS = {"چیست", "چیه", "چطور", "چگونه", "کدام", "چند", "چی", "کجا", "آیا"}

OOD_MESSAGE = "متاسفانه در دیتابیس من اطلاعاتی درباره این موضوع پیدا نشد."

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
        tag = EMBED_MODEL_NAME.replace("/", "_")
        path = os.path.join(EMB_CACHE_DIR, f"corpus_{tag}_{len(self.df)}.npz")

        if os.path.exists(path) and not rebuild:
            data = np.load(path)
            if {"full", "title"} <= set(data.files) and len(data["full"]) == len(self.df):
                return data["full"], data["title"]

        emb_full = self._encode(self.df["combined_text"].tolist(), "passage")
        emb_title = self._encode(self.df.apply(self._title_alias_text, axis=1).tolist(), "passage")
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
    def _llm(self, messages, temperature=0.3, max_tokens=700):
        """API call with exponential backoff; returns '' on failure (caller falls
        back to the template answer)."""
        if not LLM_API_KEY:
            return ""
        if self._client is None:
            self._client = OpenAI(api_key=LLM_API_KEY, base_url=LLM_BASE_URL)

        for attempt in range(1, LLM_MAX_RETRIES + 1):
            try:
                resp = self._client.chat.completions.create(
                    model=LLM_MODEL, messages=messages,
                    temperature=temperature, max_tokens=max_tokens)
                return _clean_markdown((resp.choices[0].message.content or "").strip())
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

    # ---------- public API ----------
    def answer(self, question, use_llm=True):
        """Answers one question. Returns a dict with keys:
        mode ('single'|'interdisciplinary'|'out_of_domain'), intent, answer,
        plus job/score fields depending on mode."""
        q = normalize_text(question)
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
        if res["mode"] == "single":
            print(f"job: {res['job']} (score={res['score']:.3f})")
        elif res["mode"] == "interdisciplinary":
            print(f"jobs: {res['jobs'][0]} + {res['jobs'][1]}")
        print(f"\n🤖 {res['answer']}")

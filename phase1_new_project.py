# -*- coding: utf-8 -*-
"""
Occupation Q&A system (RAG) — rewritten

- Column names are simply lowercased (so `Job_title` -> `job_title`); the schema
  matches the current dataset exactly. No legacy column mapping.
- Retrieval with a strong multilingual model (E5; query:/passage: prefixes auto-applied)
- Identical normalization for both queries and corpus text
- Interdisciplinary detection + answer (combining two jobs)
- Text generation via an external OpenAI-compatible API + local fallback
- Automatic GPU usage when CUDA is available

Run:
    python job_nlp.py                 # interactive, uses the API
    python job_nlp.py --local         # use a local model instead of the API
    python job_nlp.py --no-llm        # plain templates only, no generation
    python job_nlp.py --calibrate     # print scores to tune thresholds
    python job_nlp.py --rebuild       # force-rebuild embeddings

Env vars for the API:
    OPENAI_API_KEY, OPENAI_BASE_URL, LLM_MODEL
"""

import os
import argparse
import numpy as np
import pandas as pd
from sklearn.metrics.pairwise import cosine_similarity
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


# =========================================================
# 0) Config
# =========================================================
EMBED_MODEL_NAME = "intfloat/multilingual-e5-base"   # strong multilingual retriever
# Simpler alternative (no prefix needed, thresholds close to your old model):
#   "sentence-transformers/paraphrase-multilingual-mpnet-base-v2"
# Most accurate (heavier): "BAAI/bge-m3"

DATA_PATH = "Merged_Occupations.xlsx"
EMB_CACHE_DIR = "emb_cache"

# Thresholds — tune them with --calibrate.
# With E5 similarities run high (a good match is ~0.80+), not ~0.25 like the old model.
THRESHOLD_MATCH  = 0.80    # if the best similarity is below this -> out of domain
SECONDARY_MIN    = 0.78    # the 2nd job must be at least this relevant for interdisciplinary
SECONDARY_MARGIN = 0.03    # if (top1 - top2) <= this -> treat as interdisciplinary

# Text-generation API (OpenAI-compatible: OpenAI / OpenRouter / AvalAI / vLLM ...)
LLM_MODEL    = os.getenv("LLM_MODEL", "gpt-4o-mini")
LLM_BASE_URL = os.getenv("OPENAI_BASE_URL")   # set this for a proxy/relay service
LLM_API_KEY  = os.getenv("OPENAI_API_KEY")


# =========================================================
# 1) Schema, labels and intents
# =========================================================
# Dataset columns (already lowercased on load). `Job_title` -> `job_title`.
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

# Maps each intent to the field(s) used for display / generation
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


# =========================================================
# 2) Text normalization (identical for query and corpus)
# =========================================================
def normalize_text(text):
    if text is None or (isinstance(text, float) and pd.isna(text)):
        return ""
    text = " ".join(str(text).split())          # collapse whitespace
    if _normalizer:
        text = _normalizer.normalize(text)
    return text.strip()


# =========================================================
# 3) Load and prepare data
# =========================================================
def build_combined_text(row):
    """Full text used for retrieval embeddings. Title and aliases are emphasized first."""
    parts = [
        f"{FIELD_LABELS['job_title']}: {row['job_title']}",
        f"{FIELD_LABELS['aliases']}: {row['aliases']}",
        f"{FIELD_LABELS['description']}: {row['description']}",
        f"{FIELD_LABELS['responsibilities']}: {row['responsibilities']}",
        f"{FIELD_LABELS['skills']}: {row['skills']}",
        f"{FIELD_LABELS['tools']}: {row['tools']}",
        f"{FIELD_LABELS['work_context']}: {row['work_context']}",
        f"{FIELD_LABELS['career_path_next']}: {row['career_path_next']}",
    ]
    return " . ".join(p for p in parts if p.split(": ", 1)[-1].strip())


def load_jobs_data(file_path=DATA_PATH):
    df = pd.read_excel(file_path)
    # Lowercase + strip column names so `Job_title` becomes `job_title`
    df.columns = [str(c).strip().lower() for c in df.columns]
    # Ensure all expected columns exist, then normalize text
    for col in EXPECTED_COLUMNS:
        if col not in df.columns:
            df[col] = ""
        df[col] = df[col].map(normalize_text)
    df = df[df["job_title"].str.len() > 0].reset_index(drop=True)  # drop title-less rows
    df["combined_text"] = df.apply(build_combined_text, axis=1)
    return df


# =========================================================
# 4) Intent detection (selects display field, NOT retrieval)
# =========================================================
def detect_intent(question):
    q = question.strip()
    for intent, kws in INTENT_KEYWORDS.items():
        if any(k in q for k in kws):
            return intent
    return "general"


# =========================================================
# 5) Embeddings (auto E5 prefix handling)
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
    """Cache keyed by model name + row count; prevents loading stale embeddings."""
    os.makedirs(EMB_CACHE_DIR, exist_ok=True)
    tag = EMBED_MODEL_NAME.replace("/", "_")
    path = os.path.join(EMB_CACHE_DIR, f"corpus_{tag}_{len(df)}.npy")

    if os.path.exists(path) and not rebuild:
        emb = np.load(path)
        if len(emb) == len(df):
            print("✅ Loaded embeddings from cache.")
            return emb
        print("⚠️ Embedding count mismatch. Rebuilding...")

    print("⏳ Building embeddings (this may take a minute)...")
    emb = encode_passages(model, df["combined_text"].tolist())
    np.save(path, emb)
    print("✅ Embeddings saved.")
    return emb


# =========================================================
# 6) Context building and generation
# =========================================================
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


def llm_generate(messages, temperature=0.3, max_tokens=700, **_):
    """Generate text via an OpenAI-compatible API."""
    from openai import OpenAI
    client = OpenAI(api_key=LLM_API_KEY, base_url=LLM_BASE_URL)
    resp = client.chat.completions.create(
        model=LLM_MODEL,
        messages=messages,
        temperature=temperature,
        max_tokens=max_tokens,
    )
    return resp.choices[0].message.content.strip()


_local_pipe = None
def llm_generate_local(messages, temperature=0.3, max_tokens=400, **_):
    """Local fallback (when no API is available). A bigger model => better quality."""
    global _local_pipe
    if _local_pipe is None:
        from transformers import pipeline
        _local_pipe = pipeline(
            "text-generation",
            model="Qwen/Qwen2.5-3B-Instruct",
            device_map="auto",          # uses GPU automatically if available
        )
    out = _local_pipe(
        messages,
        max_new_tokens=max_tokens,
        temperature=temperature,
        do_sample=True,
        return_full_text=False,
        pad_token_id=_local_pipe.tokenizer.eos_token_id,
    )
    g = out[0]["generated_text"]
    return (g[-1]["content"] if isinstance(g, list) else g).strip()


def answer_single(question, row, intent, gen_fn):
    fields = INTENT_TO_FIELDS.get(intent, INTENT_TO_FIELDS["general"])
    context = build_context(row, fields)
    messages = [
        {"role": "system", "content": (
            "تو یک دستیار متخصص تحلیل مشاغل هستی. فقط و فقط بر اساس «اطلاعات شغل» که در "
            "اختیارت قرار می‌گیرد پاسخ بده و چیزی از خودت اضافه نکن. اگر داده کافی نبود، "
            "صادقانه بگو اطلاعات کافی موجود نیست. پاسخ را روان، فارسی و خلاصه بنویس."
        )},
        {"role": "user", "content": f"اطلاعات شغل:\n{context}\n\nسوال کاربر: {question}"},
    ]
    return gen_fn(messages)


def answer_interdisciplinary(question, row1, row2, intent, gen_fn):
    fields = INTENT_TO_FIELDS.get(intent, INTENT_TO_FIELDS["general"])
    c1 = build_context(row1, fields)
    c2 = build_context(row2, fields)
    messages = [
        {"role": "system", "content": (
            "تو یک مشاور شغلی خبره هستی. سوال کاربر به نقطه تلاقی دو حرفه مرتبط مربوط می‌شود. "
            "وظیفه‌ات این است که اطلاعات هر دو شغل را با هم «ترکیب» کنی و یک پاسخ منسجم و یکپارچه "
            "بدهی، نه اینکه دو فهرست جدا ارائه کنی. به کاربر توضیح بده که این یک نقش تلفیقی/"
            "بین‌رشته‌ای است و وجوه مشترک و مکمل دو شغل را برجسته کن. فقط بر اساس داده‌های "
            "داده‌شده پاسخ بده."
        )},
        {"role": "user", "content": (
            f"شغل اول:\n{c1}\n\nشغل دوم:\n{c2}\n\nسوال کاربر: {question}"
        )},
    ]
    return gen_fn(messages, temperature=0.4)


# Plain templates without an LLM (fallback or --no-llm mode)
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
# 7) Answer engine (unified retrieval + interdisciplinary check)
# =========================================================
def answer_question(question, df, corpus_emb, model, gen_fn, use_llm=True):
    q = normalize_text(question)
    intent = detect_intent(q)

    # Retrieval always runs on the full text (job identity); intent only picks display fields
    q_emb = encode_queries(model, [q])
    sims = cosine_similarity(q_emb, corpus_emb)[0]
    order = np.argsort(sims)[::-1]
    i1, s1 = int(order[0]), float(sims[order[0]])
    i2, s2 = int(order[1]), float(sims[order[1]])

    if s1 < THRESHOLD_MATCH:
        return {
            "mode": "out_of_domain", "intent": intent, "score": s1,
            "answer": "متاسفانه در دیتابیس من اطلاعاتی درباره این موضوع پیدا نشد.",
        }

    explicit = any(k in q for k in
                   ["بین رشته", "بین‌رشته", "ترکیب", "هر دو", "هردو", "تلفیق", "میان‌رشته"])
    interdisciplinary = (
        (s2 >= SECONDARY_MIN and (s1 - s2) <= SECONDARY_MARGIN)
        or (explicit and s2 >= THRESHOLD_MATCH - 0.05)
    )

    row1 = df.iloc[i1]
    if interdisciplinary:
        row2 = df.iloc[i2]
        ans = (answer_interdisciplinary(question, row1, row2, intent, gen_fn)
               if use_llm else simple_answer_two(row1, row2, intent))
        return {
            "mode": "interdisciplinary", "intent": intent,
            "jobs": [row1["job_title"], row2["job_title"]],
            "scores": [s1, s2], "answer": ans,
        }

    ans = (answer_single(question, row1, intent, gen_fn)
           if use_llm else simple_answer_one(row1, intent))
    return {"mode": "single", "intent": intent,
            "job": row1["job_title"], "score": s1, "answer": ans}


# =========================================================
# 8) Threshold calibration
# =========================================================
def calibrate(df, corpus_emb, model, queries):
    print("\n=== Score calibration (use this to set THRESHOLD_*) ===")
    for qq in queries:
        sims = cosine_similarity(encode_queries(model, [normalize_text(qq)]), corpus_emb)[0]
        top = np.argsort(sims)[::-1][:5]
        print(f"\n❓ {qq}")
        for r in top:
            print(f"   {sims[r]:.4f}  {df.iloc[int(r)]['job_title']}")


# =========================================================
# 9) Main
# =========================================================
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", default=DATA_PATH)
    parser.add_argument("--rebuild", action="store_true", help="force-rebuild embeddings")
    parser.add_argument("--local", action="store_true", help="use a local model instead of the API")
    parser.add_argument("--no-llm", action="store_true", help="plain templates only, no generation")
    parser.add_argument("--calibrate", action="store_true", help="print scores to tune thresholds")
    args = parser.parse_args()

    df = load_jobs_data(args.data)
    print(f"✅ Loaded {len(df)} occupations.")

    device = "cuda" if _HAS_CUDA else "cpu"
    print(f"⏳ Loading embedding model on {device.upper()} ...")
    model = SentenceTransformer(EMBED_MODEL_NAME, device=device)
    corpus_emb = get_corpus_embeddings(df, model, rebuild=args.rebuild)

    if args.calibrate:
        calibrate(df, corpus_emb, model, [
            "وظایف افسر توپخانه چیست؟",
            "ابزارهای فرمانده تانک چیست؟",
            "شغلی که هم با رادار و هم با موشک کار کند چیست؟",
            "مسیر ارتقای افسر مرکز فرماندهی",
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

        res = answer_question(question, df, corpus_emb, model, gen_fn, use_llm=use_llm)

        print("\n" + "-" * 40)
        print(f"mode: {res['mode']} | intent: {res['intent']}")
        if res["mode"] == "single":
            print(f"job: {res['job']}  (score={res['score']:.3f})")
        elif res["mode"] == "interdisciplinary":
            print(f"jobs: {res['jobs'][0]} + {res['jobs'][1]}  "
                  f"(scores={res['scores'][0]:.3f}, {res['scores'][1]:.3f})")
        print("\n🤖 پاسخ:")
        print(res["answer"])
        print("-" * 40)


if __name__ == "__main__":
    main()
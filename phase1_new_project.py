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
from huggingface_hub import login
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
HUGGING_FACE_ACCESS_TOKE = os.getenv("HUGGING_FACE_ACCESS_TOKE")
login(token=HUGGING_FACE_ACCESS_TOKE)
EMBED_MODEL_NAME = os.getenv("EMBED_MODEL_NAME", "intfloat/multilingual-e5-base")   # strong multilingual retriever
# Simpler alternative (no prefix needed, thresholds close to your old model):
#   "sentence-transformers/paraphrase-multilingual-mpnet-base-v2"
# Most accurate (heavier): "BAAI/bge-m3"

# Hybrid retrieval weights: final score = W_FULL * full-text + W_TITLE * (title+aliases)
W_FULL  = 0.6
W_TITLE = 0.4

DATA_PATH = "Merged_Occupations.xlsx"
EMB_CACHE_DIR = "emb_cache"

# Thresholds — tune them with --calibrate.
# With E5 similarities run high (a good match is ~0.80+), not ~0.25 like the old model.
THRESHOLD_MATCH  = 0.80    # if the best similarity is below this -> out of domain
SECONDARY_MIN    = 0.78    # the 2nd job must be at least this relevant for interdisciplinary
SECONDARY_MARGIN = 0.03    # if (top1 - top2) <= this -> treat as interdisciplinary
PAIR_SIM_MAX = 0.92

# Text-generation API (OpenAI-compatible: OpenAI / OpenRouter / AvalAI / vLLM ...)
LLM_MODEL    = os.getenv("LLM_MODEL", "gpt-4o-mini")
LLM_BASE_URL = os.getenv("OPENAI_BASE_URL", "https://api.gapgpt.app/v1")   # set this for a proxy/relay service
LLM_API_KEY  = os.getenv("OPENAI_API_KEY")


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

def build_title_alias_text(row):
    """Short text (title + aliases only) for the name-focused embedding.
    The '|' separator is replaced so the model reads aliases more naturally."""
    aliases = row["aliases"].replace("|", "،")
    return f"{row['job_title']} ، {aliases}".strip(" ،")

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
    """Builds/caches TWO embedding matrices:
       - full   : combined_text (job identity + all details)
       - title  : job_title + aliases only (name-focused matching)
    Cache keyed by model name + row count; prevents loading stale embeddings."""
    os.makedirs(EMB_CACHE_DIR, exist_ok=True)
    tag = EMBED_MODEL_NAME.replace("/", "_")
    path = os.path.join(EMB_CACHE_DIR, f"corpus_{tag}_{len(df)}.npz")

    if os.path.exists(path) and not rebuild:
        data = np.load(path)
        if "full" in data.files and "title" in data.files and len(data["full"]) == len(df):
            print("✅ Loaded embeddings from cache.")
            return data["full"], data["title"]
        print("⚠️ Cache is stale or has the old format. Rebuilding...")

    print("⏳ Building embeddings (this may take a minute)...")
    emb_full = encode_passages(model, df["combined_text"].tolist())
    title_texts = df.apply(build_title_alias_text, axis=1).tolist()
    emb_title = encode_passages(model, title_texts)
    np.savez(path, full=emb_full, title=emb_title)
    print("✅ Embeddings saved.")
    return emb_full, emb_title


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
        {"role": "system", "content": SYSTEM_SINGLE},
        {"role": "user", "content": f"اطلاعات شغل:\n{context}\n\nسوال کاربر: {question}"},
    ]
    return gen_fn(messages)


def answer_interdisciplinary(question, row1, row2, intent, gen_fn):
    fields = INTENT_TO_FIELDS.get(intent, INTENT_TO_FIELDS["general"])
    c1 = build_context(row1, fields)
    c2 = build_context(row2, fields)
    messages = [
        {"role": "system", "content": SYSTEM_INTERDISCIPLINARY},
        {"role": "user", "content": (
            f"شغل اول:\n{c1}\n\nشغل دوم:\n{c2}\n\nسوال کاربر: {question}"
        )},
    ]
    return gen_fn(messages)


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
def answer_question(question, df, emb_full, emb_title, model, gen_fn, use_llm=True):
    q = normalize_text(question)
    intent = detect_intent(q)

    # Hybrid retrieval: weighted mix of full-text and title/alias similarity
    q_emb = encode_queries(model, [q])
    sims_full  = cosine_similarity(q_emb, emb_full)[0]
    sims_title = cosine_similarity(q_emb, emb_title)[0]
    sims = W_FULL * sims_full + W_TITLE * sims_title

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
    pair_sim = float(np.dot(emb_full[i1], emb_full[i2]))
    jobs_are_distinct = pair_sim < PAIR_SIM_MAX
    interdisciplinary = jobs_are_distinct and (
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
def calibrate(df, emb_full, emb_title, model, queries):
    print("\n=== Score calibration (use this to set THRESHOLD_*) ===")
    for qq in queries:
        q_emb = encode_queries(model, [normalize_text(qq)])
        sims = (W_FULL * cosine_similarity(q_emb, emb_full)[0]
                + W_TITLE * cosine_similarity(q_emb, emb_title)[0])
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
    emb_full, emb_title = get_corpus_embeddings(df, model, rebuild=args.rebuild)

    if args.calibrate:
        calibrate(df, emb_full, emb_title, model, [
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

        res = answer_question(question, df, emb_full, emb_title, model, gen_fn, use_llm=use_llm)

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
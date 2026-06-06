import os
import re
import numpy as np
import pandas as pd
from sentence_transformers import SentenceTransformer
from sklearn.metrics.pairwise import cosine_similarity
from transformers import pipeline

try:
    from hazm import Normalizer

    normalizer = Normalizer()
except ImportError:
    normalizer = None

# =========================
# 0) Environment Variables
# =========================
threshold_value = 0.40


# =========================
# 1) Text Normalization
# =========================
def normalize_text(text):
    if pd.isna(text):
        return ""
    text = str(text).strip()

    # Replace multiple spaces with a single space
    text = re.sub(r"\s+", " ", text)

    if normalizer:
        text = normalizer.normalize(text)

    return text


# =========================
# 2) Intent Detection (Smart version using HuggingFace)
# =========================
def detect_intent(question, classifier):
    q = question.strip()

    # Mapping dictionary: Persian labels to system keys
    intent_mapping = {
        "معرفی و شرح کلی شغل": "description",
        "وظایف و مسئولیت‌های روزمره": "responsibilities",
        "شایستگی‌ها، مهارت‌ها و توانمندی‌ها": "competencies",
        "ابزارها و نرم‌افزارهای مورد نیاز": "tools",
        "مسیر ارتقا شغلی و آینده": "career_path",
        "محیط کاری و فضای کار": "work_context",
        "سطح شغلی و جایگاه سازمانی": "level",
        "دپارتمان و بخش سازمانی": "department"
    }

    candidate_labels = list(intent_mapping.keys())

    # Zero-Shot model predicts the most relevant label for the user's query
    result = classifier(q, candidate_labels)

    # Extract the best label (index 0 always holds the highest score)
    best_label = result['labels'][0]

    # Return the equivalent English key for the system
    return intent_mapping[best_label]


# =========================
# 3) Load & Prepare Data
# =========================
def build_combined_text(row):
    parts = [
        f"عنوان شغل: {row.get('job_title', '')}",
        f"نام‌های دیگر: {row.get('aliases', '')}",
        f"صنعت: {row.get('industry', '')}",
        f"حوزه‌ها: {row.get('domains', '')}",
        f"سطح شغلی: {row.get('level', '')}",
        f"دپارتمان: {row.get('department', '')}",
        f"ابزارها: {row.get('tools', '')}",
        f"مهارت‌های نرم: {row.get('soft_skills', '')}",
        f"مهارت‌های سخت: {row.get('hard_skills', '')}",
        f"محیط کاری: {row.get('work_context', '')}",
        f"مسیر شغلی بعدی: {row.get('career_path_next', '')}",
        f"شرح شغل: {row.get('description', '')}",
        f"مسئولیت‌ها: {row.get('responsibilities', '')}",
        f"شایستگی‌ها: {row.get('competencies', '')}",
    ]
    return " | ".join([p for p in parts if p.strip()])


def load_jobs_data(file_path="sample_jobs.xlsx"):
    df = pd.read_excel(file_path)

    # Fill NaN values with empty strings for text processing
    text_columns = [
        "job_title", "aliases", "industry", "domains", "level", "department",
        "tools", "soft_skills", "hard_skills", "work_context",
        "career_path_next", "description", "responsibilities", "competencies"
    ]

    for col in text_columns:
        if col not in df.columns:
            df[col] = ""
        df[col] = df[col].fillna("").astype(str)

    df["combined_text"] = df.apply(build_combined_text, axis=1)
    return df


# =========================
# 5) Build Embeddings
# =========================
def get_job_embeddings(df, model, embeddings_path="job_embeddings.npz", force_rebuild=False):
    if os.path.exists(embeddings_path) and not force_rebuild:
        data = np.load(embeddings_path)

        # Convert .npz file content to a dictionary of numpy arrays
        embeddings_dict = {key: data[key] for key in data.files}

        # Validate if the number of embedded records matches the dataframe
        if len(embeddings_dict["general"]) == len(df):
            print("✅ Loaded existing separated embeddings from file.")
            return embeddings_dict
        else:
            print("⚠️ Embeddings count mismatch. Rebuilding embeddings...")

    print("⏳ Building separated embeddings (This might take a minute)...")

    # Combine related skill fields for a richer competency embedding
    df["combined_skills"] = df["hard_skills"] + " " + df["soft_skills"] + " " + df["competencies"]

    embeddings_dict = {
        "general": model.encode(df["combined_text"].tolist(), show_progress_bar=False),
        "description": model.encode(df["description"].tolist(), show_progress_bar=False),
        "responsibilities": model.encode(df["responsibilities"].tolist(), show_progress_bar=False),
        "competencies": model.encode(df["combined_skills"].tolist(), show_progress_bar=False),
        "tools": model.encode(df["tools"].tolist(), show_progress_bar=False),
    }

    # Save all arrays into a single compressed .npz file
    np.savez(embeddings_path, **embeddings_dict)
    print("✅ Separated embeddings saved.")
    return embeddings_dict


# =========================
# 6) Search Jobs (Optional Utility)
# =========================
def search_jobs(query, df, model, embeddings, top_k=3):
    query = normalize_text(query)
    query_embedding = model.encode([query], convert_to_tensor=False)

    sims = cosine_similarity(query_embedding, embeddings)[0]
    top_indices = sims.argsort()[::-1][:top_k]

    results = []
    for idx in top_indices:
        row = df.iloc[idx]
        results.append({
            "score": float(sims[idx]),
            "job_title": row["job_title"],
            "aliases": row["aliases"],
            "industry": row["industry"],
            "domains": row["domains"],
            "description": row["description"],
            "responsibilities": row["responsibilities"],
            "competencies": row["competencies"],
        })
    return results


# =========================
# 7) Format Answer & Generation
# =========================
def generate_smart_response(user_question, job_row, llm_generator):
    # Build a rich context string from the matched database row
    context = f"""
    عنوان شغل: {job_row.get('job_title', '')}
    شرح شغل: {job_row.get('description', '')}
    وظایف اصلی: {job_row.get('responsibilities', '')}
    مهارت‌های سخت: {job_row.get('hard_skills', '')}
    مهارت‌های نرم: {job_row.get('soft_skills', '')}
    ابزارهای مورد نیاز: {job_row.get('tools', '')}
    محیط کاری: {job_row.get('work_context', '')}
    مسیر ارتقا شغلی: {job_row.get('career_path_next', '')}
    """

    # Prompt Engineering for the LLM
    messages = [
        {
            "role": "system",
            "content": (
                "شما یک مشاور شغلی بی‌نقص و منطقی هستید. "
                "وظیفه شما مقایسه دقیق ویژگی‌های کاربر با نیازمندی‌های شغل است. "
                "قانون مهم: ابتدا بررسی کنید که آیا ویژگی یا نقطه ضعف کاربر با 'مهارت‌های نرم' یا 'شایستگی‌های' شغل در تضاد است؟ "
                "اگر در تضاد بود، به هیچ وجه شغل را پیشنهاد ندهید و صراحتاً توضیح دهید که چرا این شغل با توجه به ویژگی کاربر مناسب او نیست. "
                "فقط بر اساس اطلاعات داده شده پاسخ دهید."
            )
        },
        {
            "role": "user",
            "content": f"اطلاعات شغل:\n{context}\n\nسوال کاربر: {user_question}"
        }
    ]

    # Generate response using the local LLM pipeline
    outputs = llm_generator(
        messages,
        max_new_tokens=300,
        temperature=0.3,
        do_sample=True,
        pad_token_id=llm_generator.tokenizer.eos_token_id,
        return_full_text=False
    )

    # Extract the generated output string safely
    generated_data = outputs[0]["generated_text"]

    # Handle variations in HuggingFace pipeline output structures (list vs string)
    if isinstance(generated_data, list):
        generated_text = generated_data[-1]["content"]
    else:
        generated_text = generated_data

    return str(generated_text).strip()


def generate_simple_answer(job_row, intent):
    title = job_row.get("job_title", "")

    if intent == "description":
        return f"📌 شغل: {title}\n\n{job_row.get('description', 'اطلاعاتی موجود نیست.')}"
    elif intent == "responsibilities":
        return f"📌 وظایف شغل {title}:\n\n{job_row.get('responsibilities', 'اطلاعاتی موجود نیست.')}"
    elif intent == "competencies":
        hard_skills = job_row.get("hard_skills", "")
        soft_skills = job_row.get("soft_skills", "")
        competencies = job_row.get("competencies", "")
        return (
            f"📌 شایستگی‌ها و مهارت‌های موردنیاز برای {title}:\n\n"
            f"مهارت‌های سخت: {hard_skills}\n"
            f"مهارت‌های نرم: {soft_skills}\n\n"
            f"توضیح تکمیلی: {competencies}"
        )
    elif intent == "tools":
        return f"📌 ابزارهای مورد استفاده در شغل {title}:\n\n{job_row.get('tools', 'اطلاعاتی موجود نیست.')}"
    elif intent == "career_path":
        return f"📌 مسیر شغلی بعدی برای {title}:\n\n{job_row.get('career_path_next', 'اطلاعاتی موجود نیست.')}"
    elif intent == "work_context":
        return f"📌 محیط کاری شغل {title}:\n\n{job_row.get('work_context', 'اطلاعاتی موجود نیست.')}"
    elif intent == "level":
        return f"📌 سطح شغلی {title}:\n\n{job_row.get('level', 'اطلاعاتی موجود نیست.')}"
    elif intent == "department":
        return f"📌 دپارتمان/واحد شغل {title}:\n\n{job_row.get('department', 'اطلاعاتی موجود نیست.')}"

    return f"📌 شغل: {title}\n\n{job_row.get('description', 'اطلاعاتی موجود نیست.')}"


def answer_question(question, df, embeddings_dict, model, classifier, llm_generator, threshold=threshold_value):
    clean_question = normalize_text(question)

    # 1. Detect Intent
    intent = detect_intent(clean_question, classifier)

    # 2. Search & Match
    question_embedding = model.encode([clean_question])
    target_embeddings = embeddings_dict.get(intent, embeddings_dict["general"])
    similarities = cosine_similarity(question_embedding, target_embeddings)[0]

    best_idx = similarities.argmax()
    score = similarities[best_idx]

    # Handle Out of Domain Queries
    if score < threshold:
        return {
            "intent": "out_of_domain",
            "matched_job": "نامشخص",
            "score": float(score),
            "response_smart": "متاسفانه اطلاعاتی در دیتابیس من درباره این موضوع وجود ندارد.",
            "response_simple": "متاسفانه اطلاعاتی در دیتابیس من درباره این موضوع وجود ندارد."
        }

    best_job = df.iloc[best_idx]

    # 3. Generate Responses (RAG vs Simple Template)
    smart_response = generate_smart_response(question, best_job, llm_generator)
    simple_response = generate_simple_answer(best_job, intent)

    return {
        "intent": intent,
        "matched_job": best_job["job_title"],
        "score": float(score),
        "response_smart": smart_response,
        "response_simple": simple_response
    }


# =========================
# 8) Main Execution
# =========================
def main():
    file_path = "sample_jobs.xlsx"
    df = load_jobs_data(file_path)

    print("⏳ Loading Embedding Model...")
    model = SentenceTransformer("sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2")

    print("⏳ Loading Zero-Shot Classifier...")
    classifier = pipeline("zero-shot-classification", model="MoritzLaurer/mDeBERTa-v3-base-mnli-xnli")

    print("⏳ Loading Text Generation LLM (Qwen-1.5B)...")
    # Automatically maps the model to GPU (T4 in Colab) if available
    llm_generator = pipeline(
        "text-generation",
        model="Qwen/Qwen2.5-1.5B-Instruct",
        device_map="auto"
    )

    embeddings_dict = get_job_embeddings(df, model, embeddings_path="job_embeddings.npz")

    print("✅ System is ready. Type your question (or 'exit').")

    while True:
        question = input("\n❓ سوال: ").strip()
        if question.lower() in ["exit", "quit", "خروج"]:
            break

        result = answer_question(question, df, embeddings_dict, model, classifier, llm_generator)

        print("\n------------------------------")
        print(f"🎯 Intent: {result['intent']}")
        print(f"🔎 Matched Job: {result['matched_job']}")
        print(f"📊 Score: {result['score']:.4f}")
        print("\n🤖 پاسخ هوشمند:")
        print(result["response_smart"])
        print("\n📄 پاسخ ساده:")
        print(result["response_simple"])
        print("------------------------------")


if __name__ == "__main__":
    main()
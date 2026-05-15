import os
import re
import pandas as pd
from sentence_transformers import SentenceTransformer
from sklearn.metrics.pairwise import cosine_similarity

try:
    from hazm import Normalizer
    normalizer = Normalizer()
except:
    normalizer = None


# =========================
# 1) Normalization
# =========================
def normalize_text(text):
    if pd.isna(text):
        return ""
    text = str(text).strip()

    # basic cleanup
    text = text.replace("\u200c", " ")  # نیم‌فاصله
    text = re.sub(r"\s+", " ", text)

    if normalizer:
        text = normalizer.normalize(text)

    return text


# =========================
# 2) Intent Detection
# =========================
def detect_intent(query):
    q = normalize_text(query)

    competency_keywords = [
        "ارتقا", "پیشرفت", "رشد", "شایستگی", "مهارت لازم",
        "چه چیزهایی یاد بگیرم", "برای بهتر شدن", "برای پیشرفت",
        "برای ارتقا", "چطور ارتقا", "چگونه ارتقا"
    ]

    responsibility_keywords = [
        "وظایف", "مسئولیت", "چه کارهایی انجام", "کارش چیست",
        "چه می کند", "چه میکنه", "شرح وظایف"
    ]

    description_keywords = [
        "چیست", "یعنی چه", "معرفی", "تعریف", "این شغل چیست"
    ]

    for kw in competency_keywords:
        if kw in q:
            return "competencies"

    for kw in responsibility_keywords:
        if kw in q:
            return "responsibilities"

    for kw in description_keywords:
        if kw in q:
            return "description"

    return "general"


# =========================
# 3) Generate Sample Excel
# =========================
def generate_sample_excel(file_path="sample_jobs.xlsx"):
    data = [
        {
            "job_title": "کارشناس منابع انسانی",
            "aliases": "کارشناس HR، کارشناس سرمایه انسانی، HR Specialist",
            "industry": "عمومی",
            "domains": "منابع انسانی، جذب و استخدام، آموزش، ارزیابی عملکرد",
            "description": "این شغل مسئول اجرای فرآیندهای منابع انسانی مانند جذب، استخدام، آموزش، ارزیابی عملکرد و پشتیبانی از کارکنان است.",
            "responsibilities": "انجام فرآیند جذب و استخدام، تنظیم پرونده پرسنلی، همکاری در آموزش کارکنان، اجرای ارزیابی عملکرد، پاسخگویی به مسائل منابع انسانی.",
            "competencies": "برای ارتقا در این شغل باید مهارت ارتباطی، تسلط بر قوانین کار، توانایی تحلیل داده‌های منابع انسانی، آشنایی با سیستم‌های HR و مهارت حل مسئله را تقویت کند."
        },
        {
            "job_title": "کارشناس منابع انسانی در شرکت عمرانی",
            "aliases": "کارشناس HR شرکت عمرانی، کارشناس منابع انسانی پروژه ساختمانی، HR عمرانی",
            "industry": "عمران و ساخت‌وساز",
            "domains": "منابع انسانی، عمران، پروژه، ساخت‌وساز",
            "description": "این شغل مسئول اجرای امور منابع انسانی در شرکت‌های عمرانی و پروژه‌های ساختمانی است و باید با شرایط نیروی انسانی پروژه‌ای و محیط کارگاهی آشنا باشد.",
            "responsibilities": "جذب نیروی پروژه، هماهنگی امور پرسنلی کارکنان کارگاه، همکاری در برنامه‌ریزی نیروی انسانی پروژه، پیگیری حضور و غیاب و قراردادهای کارکنان پروژه.",
            "competencies": "برای ارتقا در این شغل باید بر قوانین کار و قراردادهای پروژه‌ای مسلط شود، با فضای پروژه‌های عمرانی آشنا باشد، مهارت ارتباط با مدیران فنی و سرپرستان کارگاه را تقویت کند و تحلیل نیاز نیروی انسانی پروژه را یاد بگیرد."
        },
        {
            "job_title": "مهندس عمران",
            "aliases": "کارشناس عمران، Civil Engineer، مهندس پروژه عمرانی",
            "industry": "عمران و ساخت‌وساز",
            "domains": "عمران، سازه، اجرا، پروژه",
            "description": "این شغل مسئول طراحی، نظارت و اجرای پروژه‌های عمرانی و ساختمانی است.",
            "responsibilities": "بررسی نقشه‌ها، نظارت بر اجرای پروژه، کنترل کیفیت عملیات اجرایی، هماهنگی با پیمانکاران و تهیه گزارش فنی.",
            "competencies": "برای ارتقا در این شغل باید دانش فنی عمران را عمیق‌تر کند، نرم‌افزارهای تخصصی را یاد بگیرد، مهارت مدیریت پروژه و توانایی حل مسئله در شرایط اجرایی را افزایش دهد."
        },
        {
            "job_title": "کارشناس آموزش و توسعه",
            "aliases": "کارشناس Learning and Development، کارشناس L&D، کارشناس آموزش سازمانی",
            "industry": "عمومی",
            "domains": "منابع انسانی، آموزش، توسعه کارکنان",
            "description": "این شغل مسئول نیازسنجی آموزشی، طراحی برنامه‌های توسعه کارکنان و ارزیابی اثربخشی آموزش است.",
            "responsibilities": "انجام نیازسنجی آموزشی، تدوین برنامه آموزشی، برگزاری یا هماهنگی دوره‌ها، ارزیابی اثربخشی آموزش و تهیه گزارش‌های آموزشی.",
            "competencies": "برای ارتقا در این شغل باید مهارت طراحی آموزشی، تحلیل نیاز آموزشی، ارزیابی اثربخشی، ارتباط موثر و آشنایی با مسیرهای توسعه شغلی را تقویت کند."
        },
        {
            "job_title": "کارشناس حقوق و دستمزد",
            "aliases": "Payroll Specialist، کارشناس جبران خدمات، کارشناس حقوق",
            "industry": "عمومی",
            "domains": "منابع انسانی، جبران خدمات، حقوق و دستمزد",
            "description": "این شغل مسئول محاسبه حقوق و مزایا، کنترل اطلاعات پرسنلی مرتبط با پرداخت و همکاری در امور جبران خدمات است.",
            "responsibilities": "محاسبه حقوق و مزایا، بررسی کارکرد، کنترل کسورات، تهیه گزارش‌های پرداخت و همکاری با امور مالی.",
            "competencies": "برای ارتقا در این شغل باید دقت تحلیلی، تسلط بر قوانین بیمه و مالیات، مهارت کار با نرم‌افزارهای حقوق و دستمزد و توانایی تهیه گزارش‌های دقیق را تقویت کند."
        }
    ]

    df = pd.DataFrame(data)
    df.to_excel(file_path, index=False)
    print(f"✅ Sample Excel created: {file_path}")


# =========================
# 4) Load Data
# =========================
def load_jobs(file_path="sample_jobs.xlsx"):
    df = pd.read_excel(file_path)

    for col in ["job_title", "aliases", "industry", "domains", "description", "responsibilities", "competencies"]:
        if col not in df.columns:
            df[col] = ""
        df[col] = df[col].fillna("").apply(normalize_text)

    # combined text for semantic search
    df["combined_text"] = df.apply(
        lambda row: f"""
        عنوان شغل: {row['job_title']}
        نام های مشابه: {row['aliases']}
        صنعت: {row['industry']}
        حوزه ها: {row['domains']}
        توضیحات: {row['description']}
        وظایف: {row['responsibilities']}
        شایستگی ها: {row['competencies']}
        """,
        axis=1
    )

    return df


# =========================
# 5) Build Embeddings
# =========================
def build_embeddings(df, model_name="sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"):
    model = SentenceTransformer(model_name)
    embeddings = model.encode(df["combined_text"].tolist(), convert_to_tensor=False)
    return model, embeddings


# =========================
# 6) Search
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
# 7) Format Answer
# =========================
def format_answer(query, best_result):
    intent = detect_intent(query)

    if intent == "competencies":
        return f"""🎯 شغل پیشنهادی: {best_result['job_title']}

📌 شایستگی‌های لازم برای ارتقا:
{best_result['competencies']}
"""

    elif intent == "responsibilities":
        return f"""🎯 شغل پیشنهادی: {best_result['job_title']}

📌 وظایف شغل:
{best_result['responsibilities']}
"""

    elif intent == "description":
        return f"""🎯 شغل پیشنهادی: {best_result['job_title']}

📌 معرفی شغل:
{best_result['description']}
"""

    else:
        return f"""🎯 شغل پیشنهادی: {best_result['job_title']}

📌 معرفی:
{best_result['description']}

📌 وظایف:
{best_result['responsibilities']}

📌 شایستگی‌ها:
{best_result['competencies']}
"""


# =========================
# 8) Main
# =========================
def main():
    excel_file = "sample_jobs.xlsx"

    # اگر فایل نبود، بساز
    if not os.path.exists(excel_file):
        generate_sample_excel(excel_file)

    print("📂 Loading job data...")
    df = load_jobs(excel_file)

    print("🤖 Loading embedding model...")
    model, embeddings = build_embeddings(df)

    print("✅ System is ready.")
    print("برای خروج، عبارت 'exit' را وارد کنید.\n")

    while True:
        query = input("🔍 سوال یا عنوان شغل را وارد کنید: ").strip()
        if query.lower() in ["exit", "quit", "خروج"]:
            print("👋 برنامه بسته شد.")
            break

        results = search_jobs(query, df, model, embeddings, top_k=3)

        if not results:
            print("❌ نتیجه‌ای پیدا نشد.\n")
            continue

        best_result = results[0]
        answer = format_answer(query, best_result)
        print("\n" + answer)

        print("----- نتایج نزدیک -----")
        for i, r in enumerate(results, 1):
            print(f"{i}. {r['job_title']} | score={r['score']:.4f}")
        print("\n" + "=" * 50 + "\n")


if __name__ == "__main__":
    main()

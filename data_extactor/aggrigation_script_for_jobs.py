import os
import pandas as pd

# مسیر فولدر فایل‌ها (اگر اسکریپت بیرون پوشه است، نام پوشه را بنویسید)
FOLDER_PATH = "./dataset_jobs"

# فایل‌های امتیازدار O*NET برای هر شغل یک سطر به ازای *تمام* عناصر تاکسونومی دارند
# (۳۳ مورد دانش، ۵۲ مورد توانایی، ...) و اهمیت واقعی در ستون Data Value است.
# بدون این فیلتر، همه مشاغل عیناً یک لیست یکسان می‌گیرند.
IMPORTANCE_SCALE = "IM"   # مقیاس اهمیت (۱ تا ۵)
CONTEXT_SCALE = "CX"      # مقیاس کانتکست محیط کار (۱ تا ۵)
MIN_IMPORTANCE = 3.0      # ۳ = «مهم» در مقیاس O*NET
MIN_CONTEXT = 3.5         # کانتکست عناصر بیشتری دارد، پس آستانه بالاتر

# جداکننده ستون‌های چندمقداری. حتماً «|» و نه کاما: خودِ آیتم‌ها ممکن است کاما
# داشته باشند («Education Administrators, Postsecondary» یک عنوان است نه دو تا)
# و aggregation_translated_jobs.py هر کاما را در این ستون‌ها به | تبدیل می‌کند،
# که یک آیتم را به دو آیتم بی‌معنا می‌شکست. هیچ آیتمی در منبع «|» ندارد.
LIST_SEP = " | "


def dedup_keep_order(items, sort_items=False):
    """حذف مقادیر تکراری با حفظ ترتیب.

    مقایسه بدون حساسیت به بزرگی/کوچکی حروف و فاصله‌های اضافه انجام می‌شود،
    ولی همان شکل اولِ دیده‌شده نگه داشته می‌شود.
    """
    seen = set()
    unique = []
    for item in items:
        key = " ".join(item.lower().split())
        if key and key not in seen:
            seen.add(key)
            unique.append(item)
    return sorted(unique) if sort_items else unique


def load_and_aggregate(file_name, groupby_col, target_col, join_str=", ",
                       scale_id=None, min_value=None):
    """توابع کمکی برای لود کردن و تجمیع متون چندخطی مربوط به یک شغل

    اگر scale_id داده شود، فقط سطرهای آن مقیاس با Data Value >= min_value
    نگه داشته می‌شوند و نتیجه بر اساس اهمیت (نزولی) مرتب می‌شود.
    """
    file_path = os.path.join(FOLDER_PATH, file_name)
    if not os.path.exists(file_path):
        print(f"⚠️ فایل {file_name} پیدا نشد. این ستون خالی می‌ماند.")
        return pd.DataFrame(columns=[groupby_col, target_col])

    print(f"⏳ در حال پردازش: {file_name}...")
    df = pd.read_excel(file_path)

    # حذف دیتای خالی و تبدیل به رشته
    df[target_col] = df[target_col].fillna("").astype(str)

    # فیلتر اهمیت: فقط عناصری که واقعاً به این شغل مربوط‌اند
    sort_items = True
    if scale_id is not None and "Scale ID" in df.columns:
        before = len(df)
        df = df[df["Scale ID"] == scale_id]
        if "Recommend Suppress" in df.columns:
            df = df[df["Recommend Suppress"] != "Y"]
        if "Not Relevant" in df.columns:
            df = df[df["Not Relevant"] != "Y"]
        if min_value is not None:
            df = df[df["Data Value"] >= min_value]
        # مهم‌ترین موارد اول بیایند (مرتب‌سازی الفبایی اینجا معنایی ندارد)
        df = df.sort_values("Data Value", ascending=False)
        sort_items = False
        print(f"   ↳ فیلتر {scale_id} ≥ {min_value}: {before:,} → {len(df):,} سطر")

    # تجمیع سطرها بر اساس کد شغل و حذف مقادیر تکراری
    aggregated = df.groupby(groupby_col)[target_col].apply(
        lambda x: join_str.join(
            dedup_keep_order([item.strip() for item in x if item.strip()], sort_items)
        )
    ).reset_index()

    return aggregated

def main():
    print("🚀 فرآیند ساخت دیتابیس جامع O*NET 30.3 شروع شد...\n")

    # ۱. بارگذاری بیس اصلی مشاغل (عنوان و شرح شغل)
    base_file = os.path.join(FOLDER_PATH, "Occupation Data.xlsx")
    if not os.path.exists(base_file):
        raise FileNotFoundError("خطا: فایل اصلی 'Occupation Data.xlsx' یافت نشد!")

    print("⏳ در حال لود سورس اصلی مشاغل...")
    final_df = pd.read_excel(base_file)
    # تغییر نام ستون‌ها به ساختار پروژه شما
    final_df = final_df.rename(columns={
        "Title": "job_title",
        "Description": "description"
    })

    # ۲. استخراج نام‌های مستعار (Aliases)
    aliases_df = load_and_aggregate("Sample of Reported Titles.xlsx", "O*NET-SOC Code", "Reported Job Title",
                                    join_str=LIST_SEP)
    final_df = final_df.merge(aliases_df, on="O*NET-SOC Code", how="left").rename(columns={"Reported Job Title": "aliases"})

    # ۳. استخراج وظایف و مسئولیت‌ها (Responsibilities)
    tasks_df = load_and_aggregate("Task Statements.xlsx", "O*NET-SOC Code", "Task", join_str=LIST_SEP)
    final_df = final_df.merge(tasks_df, on="O*NET-SOC Code", how="left").rename(columns={"Task": "responsibilities"})

    # ۴. استخراج لول شغلی (Level)
    zones_file = os.path.join(FOLDER_PATH, "Job Zones.xlsx")
    if os.path.exists(zones_file):
        zones_df = pd.read_excel(zones_file)[["O*NET-SOC Code", "Job Zone"]]
        final_df = final_df.merge(zones_df, on="O*NET-SOC Code", how="left").rename(columns={"Job Zone": "level"})
    else:
        final_df["level"] = ""

    # ۵. استخراج ابزارهای نرم‌افزاری (Tools)
    # در O*NET 30.3 ستون ابزار معمولا Example یا Commodity Title نام دارد
    tools_df = load_and_aggregate("Software Skills.xlsx", "O*NET-SOC Code", "Workplace Example",
                                  join_str=LIST_SEP)
    final_df = final_df.merge(tools_df, on="O*NET-SOC Code", how="left").rename(columns={"Workplace Example": "tools"})

    # ۶. استخراج مهارت‌ها (Skills)
    skills_df = load_and_aggregate("Essential Skills.xlsx", "O*NET-SOC Code", "Element Name",
                                   join_str=LIST_SEP,
                                   scale_id=IMPORTANCE_SCALE, min_value=MIN_IMPORTANCE)
    final_df = final_df.merge(skills_df, on="O*NET-SOC Code", how="left").rename(columns={"Element Name": "hard_skills"})
    # نکته: چون هارد اسکیل و سافت اسکیل تفکیک‌نشده در این فایل هستند، می‌توانید فعلاً هردو را پر کنید
    final_df["soft_skills"] = final_df["hard_skills"]

    # ۷. استخراج کانتکست محیط کار (Work Context)
    context_df = load_and_aggregate("Work Context.xlsx", "O*NET-SOC Code", "Element Name",
                                    scale_id=CONTEXT_SCALE, min_value=MIN_CONTEXT)
    final_df = final_df.merge(context_df, on="O*NET-SOC Code", how="left").rename(columns={"Element Name": "work_context"})

    # ۷.۱. استخراج دانش مورد نیاز (Knowledge)
    knowledge_df = load_and_aggregate("Knowledge.xlsx", "O*NET-SOC Code", "Element Name",
                                      join_str=LIST_SEP,
                                      scale_id=IMPORTANCE_SCALE, min_value=MIN_IMPORTANCE)
    final_df = final_df.merge(knowledge_df, on="O*NET-SOC Code", how="left").rename(columns={"Element Name": "knowledge"})

    # ۷.۲. استخراج قابلیت‌ها و توانایی‌ها (Abilities)
    abilities_df = load_and_aggregate("Abilities.xlsx", "O*NET-SOC Code", "Element Name",
                                      join_str=LIST_SEP,
                                      scale_id=IMPORTANCE_SCALE, min_value=MIN_IMPORTANCE)
    final_df = final_df.merge(abilities_df, on="O*NET-SOC Code", how="left").rename(columns={"Element Name": "abilities"})

    # ۸. استخراج مسیر شغلی آینده (Career Path Next)
    path_df = load_and_aggregate("Related Occupations.xlsx", "O*NET-SOC Code", "Related Title",
                                 join_str=LIST_SEP)
    final_df = final_df.merge(path_df, on="O*NET-SOC Code", how="left").rename(columns={"Related Title": "career_path_next"})

    # ۹. افزودن ستون‌های کمکی صنعت و دپارتمان به صورت پیش‌فرض (چون ساختار درختی دارند)
    final_df["industry"] = final_df["O*NET-SOC Code"].apply(lambda x: f"صنعت کد {str(x)[:2]}")
    final_df["domains"] = "عمومی"
    final_df["department"] = "نامشخص"

    final_df = final_df.rename(columns={"O*NET-SOC Code": "job_code"})

    # تمیزکاری نهایی دیتابیس و پر کردن مقادیر خالی
    columns_order = [
        "job_code", "job_title", "aliases", "industry", "domains", "level", "department",
        "tools", "soft_skills", "hard_skills", "knowledge", "abilities", "work_context",
        "career_path_next", "description", "responsibilities"
    ]

    # اضافه کردن ستون‌هایی که ممکن است به هر دلیل لود نشده باشند
    for col in columns_order:
        if col not in final_df.columns:
            final_df[col] = ""

    final_df = final_df[columns_order].fillna("")

    # ۱۰. ذخیره فایل نهایی اکسل
    output_filename = "onet_master_database_en.xlsx"
    final_df.to_excel(output_filename, index=False)

    print("\n" + "="*60)
    print(f"✅ موفقیت‌آمیز! دیتابیس جامع با موفقیت ساخته شد.")
    print(f"📊 تعداد کل مشاغل استخراج شده: {len(final_df)} شغل")
    print(f"💾 فایل در این مسیر ذخیره شد: {output_filename}")
    print("-"*60)
    print(f"{'ستون':<20} {'پرشده':>8} {'مقدار یکتا':>12} {'میانگین آیتم':>14}")
    for col in columns_order:
        filled = (final_df[col].astype(str).str.strip() != "").sum()
        uniq = final_df[col].nunique()
        counts = [len([p for p in str(v).replace(" | ", ", ").split(", ") if p.strip()])
                  for v in final_df[col] if str(v).strip()]
        avg = sum(counts) / len(counts) if counts else 0
        print(f"{col:<20} {filled:>8} {uniq:>12} {avg:>14.1f}")
    print("="*60)

if __name__ == "__main__":
    main()
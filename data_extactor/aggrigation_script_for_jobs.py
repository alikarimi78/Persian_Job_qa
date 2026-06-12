import os
import pandas as pd

# مسیر فولدر فایل‌ها (اگر اسکریپت بیرون پوشه است، نام پوشه را بنویسید)
FOLDER_PATH = "./dataset_jobs"

def load_and_aggregate(file_name, groupby_col, target_col, join_str="، "):
    """توابع کمکی برای لود کردن و تجمیع متون چندخطی مربوط به یک شغل"""
    file_path = os.path.join(FOLDER_PATH, file_name)
    if not os.path.exists(file_path):
        print(f"⚠️ فایل {file_name} پیدا نشد. این ستون خالی می‌ماند.")
        return pd.DataFrame(columns=[groupby_col, target_col])

    print(f"⏳ در حال پردازش: {file_name}...")
    df = pd.read_excel(file_path)

    # حذف دیتای خالی و تبدیل به رشته
    df[target_col] = df[target_col].fillna("").astype(str)

    # تجمیع سطرها بر اساس کد شغل و حذف مقادیر تکراری
    aggregated = df.groupby(groupby_col)[target_col].apply(
        lambda x: join_str.join(sorted(list(set([item.strip() for item in x if item.strip()]))))
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
    aliases_df = load_and_aggregate("Sample of Reported Titles.xlsx", "O*NET-SOC Code", "Reported Title")
    final_df = final_df.merge(aliases_df, on="O*NET-SOC Code", how="left").rename(columns={"Reported Title": "aliases"})

    # ۳. استخراج وظایف و مسئولیت‌ها (Responsibilities)
    tasks_df = load_and_aggregate("Task Statements.xlsx", "O*NET-SOC Code", "Task", join_str=" | ")
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
    tools_df = load_and_aggregate("Software Skills.xlsx", "O*NET-SOC Code", "Example")
    final_df = final_df.merge(tools_df, on="O*NET-SOC Code", how="left").rename(columns={"Example": "tools"})

    # ۶. استخراج مهارت‌ها (Skills)
    skills_df = load_and_aggregate("Essential Skills.xlsx", "O*NET-SOC Code", "Element Name")
    final_df = final_df.merge(skills_df, on="O*NET-SOC Code", how="left").rename(columns={"Element Name": "hard_skills"})
    # نکته: چون هارد اسکیل و سافت اسکیل تفکیک‌نشده در این فایل هستند، می‌توانید فعلاً هردو را پر کنید
    final_df["soft_skills"] = final_df["hard_skills"]

    # ۷. استخراج کانتکست محیط کار (Work Context)
    context_df = load_and_aggregate("Work Context.xlsx", "O*NET-SOC Code", "Element Name")
    final_df = final_df.merge(context_df, on="O*NET-SOC Code", how="left").rename(columns={"Element Name": "work_context"})

    # ۸. استخراج مسیر شغلی آینده (Career Path Next)
    path_df = load_and_aggregate("Related Occupations.xlsx", "O*NET-SOC Code", "Related Title")
    final_df = final_df.merge(path_df, on="O*NET-SOC Code", how="left").rename(columns={"Related Title": "career_path_next"})

    # ۹. افزودن ستون‌های کمکی صنعت و دپارتمان به صورت پیش‌فرض (چون ساختار درختی دارند)
    final_df["industry"] = final_df["O*NET-SOC Code"].apply(lambda x: f"صنعت کد {str(x)[:2]}")
    final_df["domains"] = "عمومی"
    final_df["department"] = "نامشخص"

    # تمیزکاری نهایی دیتابیس و پر کردن مقادیر خالی
    columns_order = [
        "job_title", "aliases", "industry", "domains", "level", "department",
        "tools", "soft_skills", "hard_skills", "work_context",
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

    print("\n" + "="*40)
    print(f"✅ موفقیت‌آمیز! دیتابیس جامع با موفقیت ساخته شد.")
    print(f"📊 تعداد کل مشاغل استخراج شده: {len(final_df)} شغل")
    print(f"💾 فایل در این مسیر ذخیره شد: {output_filename}")
    print("="*40)

if __name__ == "__main__":
    main()
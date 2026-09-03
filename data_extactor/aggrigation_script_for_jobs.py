import os
import pandas as pd

FOLDER_PATH = "./dataset_jobs"

IMPORTANCE_SCALE = "IM"
CONTEXT_SCALE = "CX"
MIN_IMPORTANCE = 3.0
MIN_CONTEXT = 3.5

LIST_SEP = " | "


def clean_item(text):
    return " ".join(text.replace("|", "/").split())


def dedup_keep_order(items, sort_items=False):
    seen = set()
    unique = []
    for item in items:
        key = " ".join(item.lower().split())
        if key and key not in seen:
            seen.add(key)
            unique.append(item)
    return sorted(unique) if sort_items else unique


def load_and_aggregate(file_name, groupby_col, target_col,
                       scale_id=None, min_value=None):
    file_path = os.path.join(FOLDER_PATH, file_name)
    if not os.path.exists(file_path):
        print(f"⚠️ فایل {file_name} پیدا نشد. این ستون خالی می‌ماند.")
        return pd.DataFrame(columns=[groupby_col, target_col])

    print(f"⏳ در حال پردازش: {file_name}...")
    df = pd.read_excel(file_path)

    df[target_col] = df[target_col].fillna("").astype(str)

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
        df = df.sort_values("Data Value", ascending=False)
        sort_items = False
        print(f"   ↳ فیلتر {scale_id} ≥ {min_value}: {before:,} → {len(df):,} سطر")

    aggregated = df.groupby(groupby_col)[target_col].apply(
        lambda x: LIST_SEP.join(
            dedup_keep_order([clean_item(item) for item in x if item.strip()], sort_items)
        )
    ).reset_index()

    return aggregated

def main():
    print("🚀 فرآیند ساخت دیتابیس جامع O*NET 30.3 شروع شد...\n")

    base_file = os.path.join(FOLDER_PATH, "Occupation Data.xlsx")
    if not os.path.exists(base_file):
        raise FileNotFoundError("خطا: فایل اصلی 'Occupation Data.xlsx' یافت نشد!")

    print("⏳ در حال لود سورس اصلی مشاغل...")
    final_df = pd.read_excel(base_file)
    final_df = final_df.rename(columns={
        "Title": "job_title",
        "Description": "description"
    })

    aliases_df = load_and_aggregate("Sample of Reported Titles.xlsx", "O*NET-SOC Code", "Reported Job Title")
    final_df = final_df.merge(aliases_df, on="O*NET-SOC Code", how="left").rename(columns={"Reported Job Title": "aliases"})

    tasks_df = load_and_aggregate("Task Statements.xlsx", "O*NET-SOC Code", "Task")
    final_df = final_df.merge(tasks_df, on="O*NET-SOC Code", how="left").rename(columns={"Task": "responsibilities"})

    zones_file = os.path.join(FOLDER_PATH, "Job Zones.xlsx")
    if os.path.exists(zones_file):
        zones_df = pd.read_excel(zones_file)[["O*NET-SOC Code", "Job Zone"]]
        final_df = final_df.merge(zones_df, on="O*NET-SOC Code", how="left").rename(columns={"Job Zone": "level"})
    else:
        final_df["level"] = ""

    tools_df = load_and_aggregate("Software Skills.xlsx", "O*NET-SOC Code", "Workplace Example")
    final_df = final_df.merge(tools_df, on="O*NET-SOC Code", how="left").rename(columns={"Workplace Example": "tools"})

    skills_df = load_and_aggregate("Essential Skills.xlsx", "O*NET-SOC Code", "Element Name",
                                   scale_id=IMPORTANCE_SCALE, min_value=MIN_IMPORTANCE)
    final_df = final_df.merge(skills_df, on="O*NET-SOC Code", how="left").rename(columns={"Element Name": "hard_skills"})
    final_df["soft_skills"] = final_df["hard_skills"]

    context_df = load_and_aggregate("Work Context.xlsx", "O*NET-SOC Code", "Element Name",
                                    scale_id=CONTEXT_SCALE, min_value=MIN_CONTEXT)
    final_df = final_df.merge(context_df, on="O*NET-SOC Code", how="left").rename(columns={"Element Name": "work_context"})

    knowledge_df = load_and_aggregate("Knowledge.xlsx", "O*NET-SOC Code", "Element Name",
                                      scale_id=IMPORTANCE_SCALE, min_value=MIN_IMPORTANCE)
    final_df = final_df.merge(knowledge_df, on="O*NET-SOC Code", how="left").rename(columns={"Element Name": "knowledge"})

    abilities_df = load_and_aggregate("Abilities.xlsx", "O*NET-SOC Code", "Element Name",
                                      scale_id=IMPORTANCE_SCALE, min_value=MIN_IMPORTANCE)
    final_df = final_df.merge(abilities_df, on="O*NET-SOC Code", how="left").rename(columns={"Element Name": "abilities"})

    path_df = load_and_aggregate("Related Occupations.xlsx", "O*NET-SOC Code", "Related Title")
    final_df = final_df.merge(path_df, on="O*NET-SOC Code", how="left").rename(columns={"Related Title": "career_path_next"})

    final_df["industry"] = final_df["O*NET-SOC Code"].apply(lambda x: f"صنعت کد {str(x)[:2]}")
    final_df["domains"] = "عمومی"
    final_df["department"] = "نامشخص"

    final_df = final_df.rename(columns={"O*NET-SOC Code": "job_code"})

    columns_order = [
        "job_code", "job_title", "aliases", "industry", "domains", "level", "department",
        "tools", "soft_skills", "hard_skills", "knowledge", "abilities", "work_context",
        "career_path_next", "description", "responsibilities"
    ]

    for col in columns_order:
        if col not in final_df.columns:
            final_df[col] = ""

    final_df = final_df[columns_order].fillna("")

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
        counts = [len([p for p in str(v).split("|") if p.strip()])
                  for v in final_df[col] if str(v).strip()]
        avg = sum(counts) / len(counts) if counts else 0
        print(f"{col:<20} {filled:>8} {uniq:>12} {avg:>14.1f}")
    print("="*60)

if __name__ == "__main__":
    main()

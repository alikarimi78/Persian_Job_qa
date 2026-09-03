import argparse
import random
import sys
import time

from job_qa_service import JobQAEngine, normalize_text
from job_qa_service.config import OCCUPATIONS_PATH

OOD_PROBES = [
    "طرز تهیه قورمه‌سبزی چیست؟", "قیمت دلار امروز چند است؟", "بهترین گوشی سال کدام است؟",
    "قیمت بیت‌کوین امروز چند است؟", "پایتخت فرانسه کجاست؟", "نتیجه بازی پرسپولیس چه شد؟",
    "آب و هوای فردا چطور است؟", "درمان سرماخوردگی چیست؟", "بهترین فیلم سال کدام است؟",
    "تاریخ ایران باستان", "کیک شکلاتی", "تعمیر یخچال در منزل",
    "چگونه وزن کم کنم؟", "بهترین رستوران شهر کجاست؟", "قانون جدید سربازی چیست؟",
    "معنی خواب مار چیست؟", "طرز کاشت گوجه در گلدان", "بلیط قطار مشهد چند است؟",
    "فرمول محیط دایره چیست؟", "بهترین دانشگاه ایران کدام است؟",
]

ABOUT_PROBES = [
    "کار تو چیه؟", "هدف تو چیه؟", "تو کی هستی؟", "شما کی هستید؟", "کار شما چیست؟",
    "اسمت چیه؟", "چه کاری انجام می‌دهی؟", "چه کمکی می‌توانی به من بکنی؟",
    "این سامانه چیست؟", "هدف این سامانه چیست؟", "خودت را معرفی کن", "چیکار می‌کنی؟",
    "سلام", "سلام خسته نباشید", "با سلام و عرض ادب", "وقت بخیر", "خوبی؟", "درود بر شما",
]


class Category:
    def __init__(self, name):
        self.name, self.passed, self.failures = name, 0, []

    def add(self, ok, detail):
        if ok:
            self.passed += 1
        else:
            self.failures.append(detail)

    @property
    def total(self):
        return self.passed + len(self.failures)


def answered_titles(res):
    if res.get("jobs"):
        return [normalize_text(t) for t in res["jobs"]]
    return [normalize_text(res["job"])] if res.get("job") else []


def brief(res):
    got = "، ".join(answered_titles(res)) or "—"
    score = res.get("score")
    return f"mode={res['mode']} score={'—' if score is None else f'{score:.3f}'} -> {got}"


def run(engine, sample_idx, fixture_rows):
    cats = {name: Category(name) for name in
            ("fixture", "title", "bare", "alias", "ood", "about")}

    for question, expected in fixture_rows:
        res = engine.answer(question, use_llm=False)
        if not expected:
            cats["fixture"].add(res["mode"] == "out_of_domain", f"{question} | {brief(res)}")
            continue
        accepted = [normalize_text(t.strip()) for t in expected.split("|") if t.strip()]
        stale = [t for t in accepted if t not in engine.titles_normalized]
        if stale:
            cats["fixture"].add(False, f"{question} | STALE: «{'، '.join(stale)}» is not a corpus title")
        else:
            cats["fixture"].add(bool(set(accepted) & set(answered_titles(res))),
                                f"{question} | expected «{expected}» | {brief(res)}")

    for i in sample_idx:
        row = engine.df.iloc[i]
        title = row["job_title"]

        res = engine.answer(f"وظایف {title} چیست؟", use_llm=False)
        cats["title"].add(normalize_text(title) in answered_titles(res),
                          f"وظایف {title} چیست؟ | {brief(res)}")

        res = engine.answer(title, use_llm=False)
        cats["bare"].add(normalize_text(title) in answered_titles(res),
                         f"{title} | {brief(res)}")

        alias = next((a.strip() for a in str(row["aliases"]).split("|")
                      if a.strip() and normalize_text(a) != normalize_text(title)), None)
        if alias:
            res = engine.answer(f"وظایف {alias} چیست؟", use_llm=False)
            carried = any(
                normalize_text(alias) in engine.record_names[engine.titles_normalized[t]]
                for t in answered_titles(res) if t in engine.titles_normalized)
            cats["alias"].add(carried, f"وظایف {alias} چیست؟ | from «{title}» | {brief(res)}")

    for question in OOD_PROBES:
        res = engine.answer(question, use_llm=False)
        cats["ood"].add(res["mode"] == "out_of_domain", f"{question} | {brief(res)}")

    for question in ABOUT_PROBES:
        res = engine.answer(question, use_llm=False)
        cats["about"].add(res["mode"] == "about", f"{question} | {brief(res)}")

    return cats


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--sample", type=int, default=150,
                        help="records the generated probes are built from (default 150)")
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--all", action="store_true", help="probe every record")
    parser.add_argument("--csv", default="eval_questions.csv")
    parser.add_argument("--show", type=int, default=15,
                        help="failures printed per category (default 15)")
    args = parser.parse_args()

    if not OCCUPATIONS_PATH:
        sys.exit("Set OCCUPATIONS_PATH (e.g. OCCUPATIONS_PATH=Merged_Occupations.xlsx)")

    fixture_rows = []
    try:
        import csv
        with open(args.csv, encoding="utf-8") as handle:
            for row in csv.DictReader(handle):
                if (row.get("question") or "").strip():
                    fixture_rows.append((row["question"].strip(),
                                         (row.get("expected") or "").strip()))
    except FileNotFoundError:
        print(f"(no {args.csv}; fixture category skipped)")

    print("Building engine…", flush=True)
    engine = JobQAEngine(OCCUPATIONS_PATH)
    engine.titles_normalized = {normalize_text(t): i for i, t in enumerate(engine.titles)}
    engine.record_names = [
        " | ".join([normalize_text(r["job_title"]), normalize_text(str(r["aliases"]))])
        for _, r in engine.df.iterrows()]

    n = len(engine.df)
    sample_idx = (range(n) if args.all
                  else sorted(random.Random(args.seed).sample(range(n), min(args.sample, n))))
    print(f"{n} records; probing {len(sample_idx)} of them "
          f"(seed={args.seed}) + {len(OOD_PROBES)} ood + {len(ABOUT_PROBES)} about "
          f"+ {len(fixture_rows)} fixture\n", flush=True)

    started = time.time()
    cats = run(engine, sample_idx, fixture_rows)
    elapsed = time.time() - started

    print(f"{'category':10s} {'pass':>6s} {'total':>6s} {'pct':>7s}")
    grand_pass = grand_total = 0
    for cat in cats.values():
        if not cat.total:
            continue
        grand_pass, grand_total = grand_pass + cat.passed, grand_total + cat.total
        print(f"{cat.name:10s} {cat.passed:6d} {cat.total:6d} {cat.passed / cat.total:6.1%}")
    print(f"{'all':10s} {grand_pass:6d} {grand_total:6d} {grand_pass / grand_total:6.1%}"
          f"   ({elapsed:.0f}s)")

    for cat in cats.values():
        if cat.failures:
            print(f"\n--- {cat.name}: {len(cat.failures)} failed "
                  f"(showing {min(len(cat.failures), args.show)}) ---")
            for line in cat.failures[:args.show]:
                print(f"  {line}")


if __name__ == "__main__":
    main()

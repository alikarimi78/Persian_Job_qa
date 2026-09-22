import argparse
import random
import sys
import time

from src.ai_engine import JobQAEngine, normalize_text
from src.ai_engine import profile as profile_match
from src.ai_engine.columns import PROFILE_REQUIRED
from src.ai_engine.config import OCCUPATIONS_PATH

# What a record-derived probe takes from the record it is built from, in two shapes. The taxonomy
# one is the hard measurement and the reason this script exists: those four columns are closed
# vocabularies whose commonest items sit in nine records out of ten, so a profile drawn from them
# describes hundreds of records equally well and only the weighting tells them apart. Adding one
# duty gives the probe a sentence almost no other record holds, which is the easy case.
PROBE_SHAPES = {
    "taxonomy only": {"skills": 2, "knowledge": 1, "abilities": 1, "work_context": 1},
    "with one duty": {"skills": 2, "knowledge": 1, "abilities": 1, "work_context": 1,
                      "responsibilities": 1},
}

# Profiles written the way a person writes them, against the title their answer must contain. These
# are the case the coverage channel exists for and the one it used to score 0% on: none of these
# phrases is in any taxonomy column, and every one of them is somewhere in the right record.
NATURAL_PROBES = [
    ({"skills": ["برنامه‌نویسی", "حل مسئله"], "knowledge": ["پایگاه داده"],
      "abilities": ["تفکر منطقی"], "work_context": ["کار تیمی"],
      "tools": ["پایتون", "گیت"]}, "برنامه‌نویس"),
    ({"skills": ["مراقبت از بیمار", "ارتباط مؤثر"], "knowledge": ["پرستاری", "دارو"],
      "abilities": ["دقت"], "work_context": ["شیفت شب"]}, "پرستار"),
    ({"skills": ["حسابداری", "تهیه صورت‌های مالی"], "knowledge": ["مالیات"],
      "abilities": ["استدلال ریاضی"], "work_context": ["کار پشت میز"],
      "tools": ["اکسل"]}, "حساب"),
    ({"skills": ["جوشکاری", "برش فلز"], "knowledge": ["ایمنی کار"],
      "abilities": ["ثبات دست و بازو"], "work_context": ["کار در ارتفاع"]}, "جوشکار"),
    ({"skills": ["تدریس", "ارزشیابی دانش‌آموزان"], "knowledge": ["روانشناسی تربیتی"],
      "abilities": ["بیان شفاهی"], "work_context": ["کلاس درس"]}, "معلم|آموزگار|مدرس|استاد"),
    ({"skills": ["رانندگی", "بارگیری"], "knowledge": ["مقررات راهنمایی و رانندگی"],
      "abilities": ["بینایی نزدیک"], "work_context": ["سفرهای طولانی"]}, "رانندگ"),
    ({"skills": ["پخت‌وپز", "تهیه غذا"], "knowledge": ["بهداشت مواد غذایی"],
      "abilities": ["سرعت عمل"], "work_context": ["ایستادن طولانی"]}, "آشپز"),
    ({"skills": ["طراحی گرافیک", "خلاقیت بصری"], "knowledge": ["اصول طراحی"],
      "abilities": ["دقت بصری"], "work_context": ["کار پروژه‌ای"],
      "tools": ["فتوشاپ", "ایلوستریتور"]}, "گرافیک"),
    ({"skills": ["عکاسی", "نورپردازی"], "knowledge": ["ترکیب‌بندی تصویر"],
      "abilities": ["دقت بصری"], "work_context": ["کار در فضای باز"]}, "عکاس"),
    ({"skills": ["جذب و استخدام نیرو", "مصاحبه شغلی"], "knowledge": ["قوانین کار"],
      "abilities": ["ارتباط کلامی"], "work_context": ["کار اداری"]}, "منابع انسانی"),
    ({"skills": ["تعمیر موتور خودرو", "عیب‌یابی"], "knowledge": ["مکانیک خودرو"],
      "abilities": ["مهارت دست"], "work_context": ["کار در تعمیرگاه"]}, "مکانیک"),
    ({"skills": ["سیم‌کشی ساختمان", "نصب تابلو برق"], "knowledge": ["ایمنی برق"],
      "abilities": ["مهارت دست"], "work_context": ["کار در ارتفاع"]}, "برق‌کار"),
]

# A profile of nothing real: the ranking has nothing to stand on and must refuse rather than hand
# back five jobs at 0%. Fortune-telling is deliberately not one of these — «رمال» and «فالگیر» are
# corpus records, and a profile of divination is answered, not refused.
FANTASY_PROBES = [
    {"skills": ["تربیت اژدها", "پرواز با جارو"], "knowledge": ["جادوی سیاه"],
     "abilities": ["نامرئی شدن"], "work_context": ["قلعه جادویی"]},
    {"skills": ["سفر در زمان", "تسخیر سیارات"], "knowledge": ["زبان موجودات فضایی"],
     "abilities": ["گذشتن از دیوار"], "work_context": ["دنیای موازی"]},
]


def build_probe(row, rng, shape):
    profile = {}
    for field, count in shape.items():
        items = profile_match.record_items(field, row.get(field, ""))
        if items:
            profile[field] = rng.sample(items, min(count, len(items)))
    # A record too thin to meet the form's own minimums is not a probe.
    if any(len(profile.get(field, [])) < 1 for field in PROFILE_REQUIRED):
        return None
    if len(profile.get("skills", [])) < 2:
        return None
    return profile


def rank_of(result, title):
    titles = [normalize_text(m["job_title"]) for m in result.get("matches", [])]
    wanted = normalize_text(title)
    return titles.index(wanted) + 1 if wanted in titles else None


def main():
    parser = argparse.ArgumentParser(
        description="Offline probe of advanced analysis: ranks profiles with use_llm=False.")
    parser.add_argument("--sample", type=int, default=60,
                        help="records the derived probes are built from (default 60)")
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--show", type=int, default=10, help="failures printed (default 10)")
    args = parser.parse_args()

    if not OCCUPATIONS_PATH:
        sys.exit("Set OCCUPATIONS_PATH (e.g. OCCUPATIONS_PATH=Merged_Occupations.xlsx)")

    print("Building engine…", flush=True)
    engine = JobQAEngine(OCCUPATIONS_PATH)
    n = len(engine.df)
    rng = random.Random(args.seed)
    sample = sorted(rng.sample(range(n), min(args.sample, n)))
    print(f"{n} records; {len(sample)} derived probes (seed={args.seed}) "
          f"+ {len(NATURAL_PROBES)} natural + {len(FANTASY_PROBES)} fantasy\n", flush=True)

    started = time.time()
    scores, failures = [], []
    for shape_name, shape in PROBE_SHAPES.items():
        first = top5 = derived = covered = 0
        for idx in sample:
            row = engine.df.iloc[idx]
            # The same seed per record, so the two shapes draw the same items where they overlap.
            profile = build_probe(row, random.Random(args.seed + idx), shape)
            if profile is None:
                continue
            derived += 1
            result = engine.analyze(profile, use_llm=False)
            place = rank_of(result, row["job_title"])
            first += place == 1
            top5 += place is not None
            if result.get("matches"):
                covered += result["matches"][0]["coverage"]
            if place is None:
                got = "، ".join(m["job_title"] for m in result.get("matches", [])[:2]) or "—"
                failures.append(f"[{shape_name}] {row['job_title']} -> {got}")
        scores += [(f"derived rank 1 ({shape_name})", first, derived),
                   (f"derived in top 5 ({shape_name})", top5, derived)]
        if derived:
            print(f"{shape_name}: best match's coverage, mean {covered / derived:.2f}", flush=True)

    natural_hits, natural_fails = 0, []
    unknown_items = total_items = 0
    for profile, wanted in NATURAL_PROBES:
        result = engine.analyze(profile, use_llm=False)
        titles = [m["job_title"] for m in result.get("matches", [])]
        hit = any(any(part in title for part in wanted.split("|")) for title in titles)
        natural_hits += hit
        for field in (result.get("matches") or [{"fields": []}])[0]["fields"]:
            unknown_items += len(field["unknown"])
            total_items += len(field["matched"]) + len(field["missing"]) + len(field["unknown"])
        if not hit:
            natural_fails.append(f"«{wanted}» | mode={result['mode']} -> "
                                 + ("، ".join(titles[:3]) or "—"))

    refused = sum(engine.analyze(p, use_llm=False)["mode"] == "out_of_domain"
                  for p in FANTASY_PROBES)
    elapsed = time.time() - started

    print(f"\n{'category':34s} {'pass':>6s} {'total':>6s} {'pct':>7s}")
    for name, passed, total in scores + [("natural wording", natural_hits, len(NATURAL_PROBES)),
                                         ("fantasy refused", refused, len(FANTASY_PROBES))]:
        if total:
            print(f"{name:34s} {passed:6d} {total:6d} {passed / total:6.1%}")
    print(f"\nnatural wording: items the corpus has no word for "
          f"{unknown_items / max(total_items, 1):.1%}   ({elapsed:.0f}s)")

    for name, rows in (("derived", failures), ("natural", natural_fails)):
        if rows:
            print(f"\n--- {name}: {len(rows)} missed (showing {min(len(rows), args.show)}) ---")
            for line in rows[:args.show]:
                print(f"  {line}")


if __name__ == "__main__":
    main()

# -*- coding: utf-8 -*-
"""Measures the engine's routing and retrieval, with no LLM calls and no database.

    OCCUPATIONS_PATH=Merged_Occupations.xlsx venv/bin/python3 -m scripts.eval_engine
    ... --sample 150 --seed 0     # how many records the generated probes are built from
    ... --all                     # every record (slow on CPU: one encode per question)

This is the runner `eval_questions.csv` never had. Everything runs with
`use_llm=False`, which is the point rather than a compromise: what is being measured is
**which record answers and which path the question takes** — the part every threshold in
`job_qa_service/config.py` was calibrated on — and that part is deterministic and free.
The prose an LLM writes on top of it is not measurable this way and is not measured.

Six categories, each with its own pass rule, because they fail differently:

  fixture     eval_questions.csv — hand-written pairs. The answering record must be
              one of the expected titles («|»-separated: some questions sit honestly
              between two records, and «راننده زره‌پوش» answered from either armored
              record serves the reader); an empty expected means out_of_domain. A title
              that no longer exists in the corpus fails loudly as STALE, so the file
              cannot rot silently across a retranslation.
  title       «وظایف <title> چیست؟» — the leader must be that record.
  bare        the title alone — a description request; the leader must be that record.
  alias       «وظایف <first alias> چیست؟» — the answering record must *carry* that
              alias (title or aliases). Aliases are shared between records on purpose,
              so demanding the exact source record would count right answers as wrong.
  ood         fixed off-topic probes — the mode must be out_of_domain: whatever else
              happens, nothing is answered and nothing is offered.
  about       questions about the assistant, and greetings — the mode must be `about`.

The score to watch is per category, not the total: the total moves with --sample while
each category is comparable run to run under the same seed. Read a drop against the
baselines in CLAUDE.md before blaming the change you just made — and re-run with the
same --seed, since the generated probes are sampled.
"""

import argparse
import random
import sys
import time

from job_qa_service import JobQAEngine, normalize_text
from job_qa_service.config import OCCUPATIONS_PATH

# Off-topic on purpose and phrased as questions about the *topic*, never as «شغلی
# می‌خواهم…»: a job-request phrasing legitimately reaches the discovery path, which is a
# different behaviour with its own rules, and these probes must measure the OOD gate.
OOD_PROBES = [
    "طرز تهیه قورمه‌سبزی چیست؟", "قیمت دلار امروز چند است؟", "بهترین گوشی سال کدام است؟",
    "قیمت بیت‌کوین امروز چند است؟", "پایتخت فرانسه کجاست؟", "نتیجه بازی پرسپولیس چه شد؟",
    "آب و هوای فردا چطور است؟", "درمان سرماخوردگی چیست؟", "بهترین فیلم سال کدام است؟",
    "تاریخ ایران باستان", "کیک شکلاتی", "تعمیر یخچال در منزل",
    "چگونه وزن کم کنم؟", "بهترین رستوران شهر کجاست؟", "قانون جدید سربازی چیست؟",
    "معنی خواب مار چیست؟", "طرز کاشت گوجه در گلدان", "بلیط قطار مشهد چند است؟",
    "فرمول محیط دایره چیست؟", "بهترین دانشگاه ایران کدام است؟",
]

# Caught before retrieval, so the answer must be the fixed `about` text — see
# `intents.is_about_system` / `is_greeting` for what makes each safe.
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
    """The record(s) a result answers from, whatever its mode calls them."""
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
    # Two lookaside tables the pass rules need: normalized title -> row index, and per
    # record one normalized string of every name it carries (title + aliases). The alias
    # rule tests membership by substring on purpose: «برنامه‌نویس» asked, «برنامه‌نویسان
    # کامپیوتر» answering, is the system working, not a miss.
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

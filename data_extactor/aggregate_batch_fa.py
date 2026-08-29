#!/usr/bin/env python3
"""
aggregate_batch_fa.py — merge the translated batches into one xlsx, and check them
against the English originals they came from.

    python3 aggregate_batch_fa.py                    # batch_10_fa/ -> onet_master_database_fa.xlsx
    python3 aggregate_batch_fa.py --dry-run          # check only, write nothing
    python3 aggregate_batch_fa.py --report report.json

`translate_script.py` writes one `<batch>_fa.xlsx` per batch and validates each one on
its own, against the ten rows it just sent. That is the wrong scale for two questions
this script exists to answer: whether the *set* of batches is complete, and whether the
result still lines up with `batch_10/` row for row. It reads both sides, joins them on
`job_code`, and refuses to write a merged file that fails a hard check.

The checks, and why each one is here rather than in the translator:

  hard (a failure means the merged file is not written)
    * every English batch has a translation, and no translation has no English original
    * the row count matches the English corpus exactly
    * job_code: no duplicates, no additions, no losses, same order
    * no empty cell anywhere, and no cell holding only a placeholder ("-", "نامشخص", …)
    * no "|" in the prose columns — a comma is punctuation there, and a pipe in
      `description` is the corruption `scripts/backfill_from_xlsx.py` exists to repair
    * the column set is exactly COLUMNS, in that order

  soft (reported, and the file is still written)
    * per-cell item counts against the English, for the eight "|"-separated columns
    * **one English term translated more than one way.** Four of these columns are fixed
      O*NET taxonomies (10 skills / 33 knowledge / 52 abilities / 55 work_context phrases
      shared by all 1016 rows) and `career_path_next` is drawn from the occupation titles,
      so each English term has exactly one right Persian phrase. Each batch translated
      them afresh, though, and «Problem Sensitivity» came back four ways. Nothing else
      here can see that: every variant is fair Persian of the right item count, so the
      counts agree and the no-Persian check passes. It matters because the engine compares
      items as *strings* — `job_qa_service/profile.py` scores a user's skills by token
      containment against these cells — so a corpus that says one thing four ways ranks
      the same person differently depending on which row they meet. `repair_batch_fa.py`
      is what settles on one form; this is what proves it stayed settled.
    * cells that contain **no Persian at all**. This is the check the translator does not
      make and the one that matters most here: an untranslated batch comes back with its
      item counts perfectly intact — it is the English CSV echoed back — so every count
      test passes and the batch is accepted. Six batches (60 rows) reached the corpus
      that way. `tools` is exempt because it is *meant* to stay English (brand names),
      and `aliases` is only reported, since a list of proper names legitimately has none.

The output mirrors `onet_master_database_en.xlsx`: same columns, same order, same row
count, so the two can be diffed cell by cell. It is deliberately **not** written to
`../Merged_Occupations.xlsx` — that file is the live seed corpus, carries `row_index` /
`source` and 100 rows of military occupations this pass knows nothing about, and merging
the two is a separate decision.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

import pandas as pd
from openpyxl import load_workbook
from openpyxl.styles import Alignment, Font, PatternFill

HERE = Path(__file__).resolve().parent

# The ten content columns plus the join key, in the order the batches carry them.
COLUMNS = ["job_code", "job_title", "aliases", "tools", "skills", "knowledge",
           "abilities", "work_context", "career_path_next", "description",
           "responsibilities"]

# Cells here are " | "-separated lists and their item counts must survive translation.
LIST_COLUMNS = ["aliases", "tools", "skills", "knowledge", "abilities",
                "work_context", "career_path_next", "responsibilities"]

# ...and here a comma is punctuation, so a "|" is damage rather than a separator.
PROSE_COLUMNS = ["job_title", "description"]

# Columns where one English term has exactly one right Persian phrase: four fixed O*NET
# taxonomies, the occupation titles `career_path_next` is drawn from, and `tools`, whose
# generic descriptors ("Web browser software") are translated while its brand names are
# not — either answer is fine, the same answer everywhere is what is being checked.
CANONICAL_COLUMNS = ["skills", "knowledge", "abilities", "work_context",
                     "career_path_next", "tools"]

# Columns whose Persian is the whole point of the pass. `tools` is absent on purpose:
# 1099 of its cells are brand names that must stay in English. `aliases` is absent
# because a row whose alternative titles are all acronyms has no Persian to show either
# — it is counted and printed, but it does not make the file wrong.
MUST_BE_PERSIAN = ["job_title", "skills", "knowledge", "abilities", "work_context",
                   "career_path_next", "description", "responsibilities"]

# What the dataset uses for "nothing here". A cell holding only one of these is empty in
# every way that matters downstream — `job_qa_service` drops them rather than render a box.
PLACEHOLDERS = {"-", "--", "—", "–", ".", "‌", "نامشخص", "ندارد", "n/a", "N/A", "none"}

PERSIAN_RE = re.compile(r"[؀-ۿ]")


def items(value) -> list[str]:
    return [p.strip() for p in str(value).split("|") if p.strip()]


def blank(value) -> bool:
    v = str(value).strip()
    return not v or v.lower() in PLACEHOLDERS


def read_dir(directory: Path, suffix: str) -> tuple[pd.DataFrame, list[str]]:
    """Every batch in a directory as one frame, in batch order, plus the names read."""
    files = sorted(p for p in directory.glob("*.xlsx")
                   if not p.name.startswith("~$"))
    frames, names = [], []
    for path in files:
        name = path.stem[:-len(suffix)] if suffix and path.stem.endswith(suffix) else path.stem
        frame = pd.read_excel(path, dtype=str).fillna("")
        frame.columns = [str(c).strip() for c in frame.columns]
        frame["_batch"] = name
        frames.append(frame)
        names.append(name)
    if not frames:
        return pd.DataFrame(columns=COLUMNS + ["_batch"]), names
    return pd.concat(frames, ignore_index=True), names


def check(en: pd.DataFrame, fa: pd.DataFrame, en_names: list[str],
          fa_names: list[str]) -> tuple[list[str], dict]:
    """Every hard failure, and a report of everything soft."""
    hard: list[str] = []
    soft: dict = {}

    missing = [b for b in en_names if b not in set(fa_names)]
    extra = [b for b in fa_names if b not in set(en_names)]
    if missing:
        hard.append(f"{len(missing)} batches have no translation: {missing[:10]}"
                    + (" …" if len(missing) > 10 else ""))
    if extra:
        hard.append(f"{len(extra)} translated batches have no English original: {extra[:10]}")

    for label, frame in (("English", en), ("Persian", fa)):
        absent = [c for c in COLUMNS if c not in frame.columns]
        unknown = [c for c in frame.columns if c not in COLUMNS + ["_batch"]]
        if absent:
            hard.append(f"{label} side is missing columns: {absent}")
        if unknown:
            hard.append(f"{label} side has unexpected columns: {unknown}")
    if hard:
        return hard, soft          # nothing below can be trusted without the columns

    if len(fa) != len(en):
        hard.append(f"row count: {len(fa)} translated against {len(en)} English")

    dup = fa.job_code[fa.job_code.duplicated()].tolist()
    if dup:
        hard.append(f"{len(dup)} duplicated job_code in the translation: {dup[:10]}")
    lost = [c for c in en.job_code if c not in set(fa.job_code)]
    added = [c for c in fa.job_code if c not in set(en.job_code)]
    if lost:
        hard.append(f"{len(lost)} job_code lost in translation: {lost[:10]}")
    if added:
        hard.append(f"{len(added)} job_code in the translation but not in the source: {added[:10]}")
    if not lost and not added and list(en.job_code) != list(fa.job_code):
        hard.append("job_code order differs between the two sides")

    empty = {}
    for column in COLUMNS:
        rows = fa.index[fa[column].map(blank)].tolist()
        if rows:
            empty[column] = [f"{fa.job_code.iloc[i]} ({fa._batch.iloc[i]})" for i in rows[:10]]
            hard.append(f"{column}: {len(rows)} empty or placeholder-only cells")
    soft["empty_cells"] = empty

    for column in PROSE_COLUMNS:
        rows = fa.index[fa[column].str.contains("|", regex=False)].tolist()
        if rows:
            hard.append(f"{column}: {len(rows)} cells contain '|', which is not a "
                        f"separator in a prose column: "
                        f"{[fa.job_code.iloc[i] for i in rows[:5]]}")

    if hard:
        return hard, soft

    # --- soft: item counts, aligned on job_code rather than on position -------------
    left = en.set_index("job_code")
    right = fa.set_index("job_code")
    drift = {}
    for column in LIST_COLUMNS:
        rows = []
        for code in left.index:
            want, got = len(items(left[column][code])), len(items(right[column][code]))
            if want != got:
                rows.append({"job_code": code, "batch": right["_batch"][code],
                             "expected": want, "got": got})
        if rows:
            drift[column] = rows
    soft["item_drift"] = drift

    # --- soft: one English term written more than one way ---------------------------
    # Aligned position by position, so only cells whose two sides agree on item count
    # can vote; anything else could pair item 3 with item 4 and invent a disagreement.
    variants = {}
    for column in CANONICAL_COLUMNS:
        votes = {}
        for code in left.index:
            source, target = items(left[column][code]), items(right[column][code])
            if len(source) != len(target):
                continue
            for term, persian in zip(source, target):
                votes.setdefault(term, {}).setdefault(persian, 0)
                votes[term][persian] += 1
        split = {t: forms for t, forms in votes.items() if len(forms) > 1}
        if split:
            variants[column] = {
                "terms": len(split),
                "of": len(votes),
                "examples": [{"term": t, "forms": sorted(f, key=f.get, reverse=True)[:4]}
                             for t in sorted(split, key=lambda k: -sum(split[k].values()))[:5]],
            }
    soft["term_variants"] = variants

    # --- soft: cells with no Persian in them at all ---------------------------------
    untranslated = {}
    for column in COLUMNS[1:]:
        rows = [i for i in fa.index
                if str(fa[column].iloc[i]).strip() and not PERSIAN_RE.search(str(fa[column].iloc[i]))]
        if rows:
            untranslated[column] = {
                "rows": len(rows),
                "must_be_persian": column in MUST_BE_PERSIAN,
                "batches": sorted({fa._batch.iloc[i] for i in rows}),
                "examples": [fa.job_code.iloc[i] for i in rows[:5]],
            }
    soft["untranslated"] = untranslated
    return hard, soft


def write_xlsx(frame: pd.DataFrame, path: Path) -> None:
    """The merged sheet, formatted the way `aggregation_translated_jobs.py` formats its own."""
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_excel(path, index=False)

    book = load_workbook(path)
    sheet = book.active
    sheet.title = "onet_fa"
    fill = PatternFill("solid", start_color="4472C4", end_color="4472C4")
    head = Font(name="Arial", bold=True, color="FFFFFF", size=11)
    body = Font(name="Arial", size=10)
    for cell in sheet[1]:
        cell.fill, cell.font = fill, head
        cell.alignment = Alignment(horizontal="center", vertical="center")
    for row in sheet.iter_rows(min_row=2):
        for cell in row:
            cell.font = body
            cell.alignment = Alignment(wrap_text=False)
    sheet.freeze_panes = "A2"
    for column in sheet.columns:
        width = max((len(str(c.value)) if c.value else 0) for c in column)
        sheet.column_dimensions[column[0].column_letter].width = min(width + 4, 60)
    book.save(path)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="in_dir", default=str(HERE / "batch_10_fa"),
                    help="the translated batches")
    ap.add_argument("--source", default=str(HERE / "batch_10"),
                    help="the English batches they were made from")
    ap.add_argument("--out", default=str(HERE / "onet_master_database_fa.xlsx"))
    ap.add_argument("--report", default="")
    ap.add_argument("--dry-run", action="store_true", help="check only, write nothing")
    ap.add_argument("--force", action="store_true",
                    help="write the merged file even if a hard check failed")
    args = ap.parse_args()

    in_dir, src_dir = Path(args.in_dir), Path(args.source)
    fa, fa_names = read_dir(in_dir, "_fa")
    en, en_names = read_dir(src_dir, "")
    if not en_names:
        print(f"error: no .xlsx in {src_dir}", file=sys.stderr)
        return 2
    if not fa_names:
        print(f"error: no .xlsx in {in_dir}", file=sys.stderr)
        return 2
    print(f"english : {len(en_names):4} batches, {len(en):5} rows  ({src_dir})")
    print(f"persian : {len(fa_names):4} batches, {len(fa):5} rows  ({in_dir})")

    hard, soft = check(en, fa, en_names, fa_names)

    print("\n--- hard checks ---")
    if hard:
        for line in hard:
            print(f"  FAIL  {line}")
    else:
        print("  all passed: batch set complete, row counts equal, job_code aligned, "
              "no empty cell, no '|' in prose")

    drift = soft.get("item_drift") or {}
    print("\n--- item counts against the English ---")
    if drift:
        for column, rows in sorted(drift.items()):
            lost = sum(r["expected"] - r["got"] for r in rows if r["expected"] > r["got"])
            print(f"  {column:18} {len(rows):4} cells differ (items lost {lost})"
                  f"  e.g. {rows[0]['job_code']} {rows[0]['expected']}→{rows[0]['got']}")
    else:
        print("  every cell has exactly the item count of its English original")

    variants = soft.get("term_variants") or {}
    print("\n--- one English term written more than one way ---")
    if variants:
        for column, info in sorted(variants.items()):
            print(f"  {column:18} {info['terms']:4} of {info['of']} terms have several forms")
            for e in info["examples"][:2]:
                print(f"       {e['term'][:40]:40} {' / '.join(e['forms'])}")
    else:
        print("  none: every English term has exactly one Persian form corpus-wide")

    untranslated = soft.get("untranslated") or {}
    print("\n--- cells with no Persian in them ---")
    if untranslated:
        for column, info in sorted(untranslated.items(),
                                   key=lambda kv: (not kv[1]["must_be_persian"], kv[0])):
            mark = "MUST BE PERSIAN" if info["must_be_persian"] else "allowed to be English"
            print(f"  {column:18} {info['rows']:4} rows  [{mark}]  "
                  f"{len(info['batches'])} batches, e.g. {info['examples'][:3]}")
    else:
        print("  none")

    if args.report:
        Path(args.report).write_text(
            json.dumps({"hard": hard, "soft": soft}, ensure_ascii=False, indent=2),
            encoding="utf-8")
        print(f"\nreport written to {args.report}")

    if hard and not args.force:
        print("\nnot writing the merged file: fix the hard failures above, or pass --force",
              file=sys.stderr)
        return 1
    if args.dry_run:
        print("\ndry run: nothing written")
        return 0

    merged = fa.sort_values("_batch", kind="stable").drop(columns=["_batch"])
    merged = merged[COLUMNS]
    write_xlsx(merged, Path(args.out))
    print(f"\nwrote {len(merged)} rows × {len(merged.columns)} columns to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

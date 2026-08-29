#!/usr/bin/env python3
"""
repair_batch_fa.py — fill in the cells the translation pass left in English.

    python3 repair_batch_fa.py --dry-run      # what it would change, and what it cannot
    python3 repair_batch_fa.py                # rewrite batch_10_fa/ in place

`translate_script.py` validates a batch by counting items, and an untranslated batch is
the English CSV echoed back — every count matches, so it is accepted. Six batches came
back that way whole, and `career_path_next` / `job_title` / `aliases` were skipped in
several more. `aggregate_batch_fa.py` is what finds them; this is what repairs them.

Two sources, in this order:

1. **The corpus itself.** `skills`, `knowledge`, `abilities` and `work_context` are fixed
   O*NET taxonomies — the same 10 / 33 / 52 / 55 phrases recur across all 1016
   occupations — and `career_path_next` is drawn from the occupation titles themselves.
   So a cell left in English can be rebuilt from the rows that *were* translated, item by
   item. That is not a guess: it is the terminology this corpus already uses, which makes
   the repaired rows consistent with their neighbours rather than merely correct. The
   pairing is learned only from cells whose English and Persian item counts agree, so a
   dropped item cannot shift the whole list by one and teach 30 wrong pairs.

2. **`translation_fixes/*.json`** — hand-written, for what no other row contains: the 45
   occupation titles that were skipped, five titles that appear only inside
   `career_path_next`, the task statements unique to one occupation, and the alternative
   titles. Each file is `{"<column>": {"<english>": "<persian>"}}` and they are merged in
   filename order, so a later file wins and a correction is a new file rather than an
   edit to a big one.

   A file may also carry `{"by_code": {"<job_code>": {"<column>": ["…", "…"]}}}`, which
   replaces a whole cell with a list in the English order. That is the shape the task
   statements use: they belong to one occupation and nothing else, so repeating each
   English sentence as a key would double the file for nothing. The list is checked
   against the English item count on every run — a list of the wrong length is refused
   rather than silently shifting the row by one.

   Unlike everything else here, a `by_code` cell is applied **even when the cell already
   holds Persian**, and to any list column including `tools`. It is the only way to fix
   the other failure this pass produces: a cell where the model dropped one item and
   translated the rest one position out, so that every remaining item is the translation
   of its neighbour. There is nothing to detect in such a cell — it is fluent Persian of
   the right shape — and nothing to rebuild it from, so the corrected list is written by
   hand and the item count is what proves it lines up again.

   A file may finally carry `{"canonical": {"<column>": {"<english>": "<persian>"}}}`,
   which is the same table shape but **authoritative over Persian too**: wherever that
   English item appears in that column, in any row, it is written as that one Persian
   phrase. This is the third failure the pass produces and the one no per-batch check can
   see. `skills`, `knowledge`, `abilities` and `work_context` are fixed O*NET taxonomies —
   10 / 33 / 52 / 55 phrases across all 1016 occupations — and each batch translated them
   afresh, so «Problem Sensitivity» came back as «حساسیت به مسئله» in 747 rows, «حساسیت به
   مشکل» in 104 and «حساسیت به مشکلات» in 104 more. Every one of those is a fair
   translation, which is why nothing flags them; together they are three different items
   as far as the engine is concerned. `job_qa_service/profile.py` matches a user's items
   against these by token containment, so a corpus that says the same thing four ways
   scores the same person differently depending on which row they are compared against.
   `career_path_next` is canonicalized the same way, against a different authority: its
   items *are* occupation titles, so each one is written exactly as that occupation's own
   `job_title` reads, and a career path can be followed to the record it names.

   A canonical rewrite needs the two sides to have the same item count, or there is no
   telling which English item a Persian one came from; such a cell is counted and left
   alone rather than guessed at. `by_code` still wins — it is written for one row.

Two more things it normalises, both consistency rather than translation:

  * `job_title` is «Persian (English)» in 971 rows and «English (Persian)» in the 60 that
    came back from the weaker pass. The halves are swapped so the column reads one way,
    and a title that came back as Persian alone has the English put back after it — the
    English half is what `career_path_next` and the alias lists are matched against.
  * a repaired list is re-joined with " | " exactly, since that separator is what every
    reader downstream splits on.

`tools` is deliberately left alone: 1099 of its cells across this dataset are brand names
(`AutoCAD | Revit | Adobe Acrobat`) that are supposed to stay in English.

The first run copies the untouched batches to `batch_10_fa/_pre_repair/`, so the raw
output of the translation pass is still there to compare against.
"""

from __future__ import annotations

import argparse
import collections
import json
import re
import shutil
import sys
from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent

LIST_COLUMNS = ["aliases", "tools", "skills", "knowledge", "abilities",
                "work_context", "career_path_next", "responsibilities"]

# What this script will rewrite. `tools` is not here on purpose — see the docstring.
REPAIR_COLUMNS = ["job_title", "aliases", "skills", "knowledge", "abilities",
                  "work_context", "career_path_next", "responsibilities"]

PERSIAN_RE = re.compile(r"[؀-ۿ]")
# «Persian (English)» is the house style; this is its mirror image, which is what the
# batches that came back half-done used.
REVERSED_TITLE_RE = re.compile(r"^([^()]*[A-Za-z][^()]*)\(([^()]*[؀-ۿ][^()]*)\)\s*$")


def items(value) -> list[str]:
    return [p.strip() for p in str(value).split("|") if p.strip()]


def has_persian(value) -> bool:
    return bool(PERSIAN_RE.search(str(value)))


def read_pairs(src_dir: Path, fa_dir: Path) -> dict[str, tuple[pd.DataFrame, pd.DataFrame]]:
    pairs = {}
    for path in sorted(src_dir.glob("*.xlsx")):
        name = path.stem
        translated = fa_dir / f"{name}_fa.xlsx"
        if not translated.exists():
            continue
        left = pd.read_excel(path, dtype=str).fillna("")
        right = pd.read_excel(translated, dtype=str).fillna("")
        left.columns = [str(c).strip() for c in left.columns]
        right.columns = [str(c).strip() for c in right.columns]
        pairs[name] = (left, right)
    return pairs


def build_lexicon(pairs) -> dict[str, dict[str, str]]:
    """english item -> the Persian this corpus most often gives it, per column.

    Learned only from cells where the two sides have the same number of items and the
    Persian side actually is Persian — anything else would align item 3 with item 4.
    """
    votes = collections.defaultdict(lambda: collections.defaultdict(collections.Counter))
    for left, right in pairs.values():
        for i in range(len(left)):
            for column in LIST_COLUMNS:
                english, persian = items(left[column].iloc[i]), items(right[column].iloc[i])
                if len(english) != len(persian):
                    continue
                for source, target in zip(english, persian):
                    if has_persian(target) and not has_persian(source):
                        votes[column][source][target] += 1
    return {c: {s: t.most_common(1)[0][0] for s, t in d.items()} for c, d in votes.items()}


def load_fixes(directory: Path):
    """`{column: {english: persian}}`, `{job_code: {column: [persian, …]}}`, and the
    canonical table `{column: {english: persian}}` that outranks the Persian already
    in the cell."""
    fixes: dict[str, dict[str, str]] = collections.defaultdict(dict)
    by_code: dict[str, dict[str, list]] = collections.defaultdict(dict)
    canonical: dict[str, dict[str, str]] = collections.defaultdict(dict)
    if not directory.exists():
        return fixes, by_code, canonical
    for path in sorted(directory.glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        for key, value in data.items():
            if key == "by_code":
                for code, columns in value.items():
                    by_code[code].update(columns)
            elif key == "canonical":
                for column, table in value.items():
                    canonical[column].update(table)
            else:
                fixes[key].update(value)
    return fixes, by_code, canonical


def repair(pairs, lexicon, fixes, by_code, canonical, fix_titles: bool):
    """Returns the repaired frames, a per-column change count, and what is still English."""
    changed = collections.Counter()
    misses = collections.defaultdict(collections.Counter)
    rejected: list[str] = []
    unaligned = collections.Counter()
    touched = {}

    for name, (left, right) in pairs.items():
        out = right.copy()
        dirty = False
        for i in range(len(out)):
            # --- job_title: a bare English title, or the halves the wrong way round ---
            title = str(out.job_title.iloc[i]).strip()
            english_title = str(left.job_title.iloc[i]).strip()
            if not has_persian(title):
                persian = fixes.get("job_title", {}).get(english_title)
                if persian:
                    out.iat[i, out.columns.get_loc("job_title")] = f"{persian} ({english_title})"
                    changed["job_title"] += 1
                    dirty = True
                else:
                    misses["job_title"][english_title] += 1
            elif fix_titles:
                m = REVERSED_TITLE_RE.match(title)
                if m:
                    out.iat[i, out.columns.get_loc("job_title")] = \
                        f"{m.group(2).strip()} ({m.group(1).strip()})"
                    changed["job_title_reordered"] += 1
                    dirty = True
                elif not re.search(r"[A-Za-z]", title) and english_title:
                    # translated, but the English half was dropped; put it back
                    out.iat[i, out.columns.get_loc("job_title")] = f"{title} ({english_title})"
                    changed["job_title_english_restored"] += 1
                    dirty = True

            # --- the list columns -------------------------------------------------
            # --- cells written out by hand: authoritative, even over Persian ---------
            code = str(left.job_code.iloc[i]).strip()
            for column, supplied in by_code.get(code, {}).items():
                if column not in LIST_COLUMNS:
                    rejected.append(f"{code}: {column} is not a list column")
                    continue
                english = items(left[column].iloc[i])
                if len(supplied) != len(english):
                    rejected.append(f"{code} / {column}: the hand-written list has "
                                    f"{len(supplied)} items, the English has "
                                    f"{len(english)}")
                    continue
                # The hand-written list decides which English item each position holds;
                # the canonical table still decides how that item is worded, or the two
                # would overwrite each other on every run.
                table = canonical.get(column, {})
                joined = " | ".join(table.get(source, str(x).strip())
                                    for source, x in zip(english, supplied))
                if joined != str(out[column].iloc[i]).strip():
                    out.iat[i, out.columns.get_loc(column)] = joined
                    changed[column] += len(supplied)
                    dirty = True

            # --- the fixed taxonomies: one Persian phrase per English term ---------
            # This runs *after* by_code rather than deferring to it: the two answer
            # different questions. A hand-written cell decides which English item each
            # position holds; the table decides how that item is worded. Letting the
            # hand-written cells opt out left 58 rows spelling «Near Vision» three ways.
            for column, table in canonical.items():
                english = items(left[column].iloc[i])
                persian = items(out[column].iloc[i])
                if not english:
                    continue
                if len(english) != len(persian):
                    unaligned[column] += 1        # no telling which item came from which
                    continue
                rebuilt, n = [], 0
                for source, target in zip(english, persian):
                    want = table.get(source)
                    if want and want != target:
                        rebuilt.append(want)
                        n += 1
                    else:
                        rebuilt.append(target)
                if n:
                    out.iat[i, out.columns.get_loc(column)] = " | ".join(rebuilt)
                    changed[f"{column} (canonical)"] += n
                    dirty = True

            for column in REPAIR_COLUMNS:
                if column == "job_title" or column in by_code.get(code, {}):
                    continue
                cell = str(out[column].iloc[i]).strip()
                if not cell or has_persian(cell):
                    continue
                english = items(left[column].iloc[i])
                table = dict(lexicon.get(column, {}))
                table.update(fixes.get(column, {}))
                rebuilt, hit = [], 0
                for term in english:
                    persian = table.get(term)
                    if persian:
                        rebuilt.append(persian)
                        hit += 1
                    else:
                        rebuilt.append(term)          # keep it rather than lose the item
                        misses[column][term] += 1
                if hit:
                    out.iat[i, out.columns.get_loc(column)] = " | ".join(rebuilt)
                    changed[column] += hit
                    dirty = True
        if dirty:
            touched[name] = out
    return touched, changed, misses, rejected, unaligned


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="fa_dir", default=str(HERE / "batch_10_fa"))
    ap.add_argument("--source", default=str(HERE / "batch_10"))
    ap.add_argument("--fixes", default=str(HERE / "translation_fixes"))
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--no-title-reorder", action="store_true",
                    help="leave «English (Persian)» titles as they are")
    ap.add_argument("--dump-missing", default="",
                    help="write the terms no source could translate to this json")
    args = ap.parse_args()

    fa_dir, src_dir = Path(args.fa_dir), Path(args.source)
    pairs = read_pairs(src_dir, fa_dir)
    if not pairs:
        print(f"error: no batch pairs between {src_dir} and {fa_dir}", file=sys.stderr)
        return 2

    lexicon = build_lexicon(pairs)
    fixes, by_code, canonical = load_fixes(Path(args.fixes))
    print(f"{len(pairs)} batch pairs")
    print("lexicon learned from the corpus: "
          + ", ".join(f"{c} {len(v)}" for c, v in sorted(lexicon.items())))
    print("hand-written fixes: "
          + (", ".join(f"{c} {len(v)}" for c, v in sorted(fixes.items())) or "none")
          + f";  whole cells by job_code: {len(by_code)}")
    print("canonical terms: "
          + (", ".join(f"{c} {len(v)}" for c, v in sorted(canonical.items())) or "none"))

    touched, changed, misses, rejected, unaligned = repair(
        pairs, lexicon, fixes, by_code, canonical, not args.no_title_reorder)
    if rejected:
        print("\n--- REFUSED: a hand-written cell does not match its English ---")
        for line in rejected:
            print(f"  {line}")
    if unaligned:
        print("\n--- left alone: item counts differ, so the two sides cannot be paired ---")
        for column, n in sorted(unaligned.items()):
            print(f"  {column:22} {n:5} cells")

    print("\n--- repaired ---")
    for column, n in sorted(changed.items()):
        print(f"  {column:22} {n:6} items")
    if not changed:
        print("  nothing to repair")

    print("\n--- still English (no source could translate these) ---")
    for column, counter in sorted(misses.items()):
        total = sum(counter.values())
        print(f"  {column:22} {len(counter):5} distinct terms, {total:6} occurrences"
              f"   e.g. {[t for t, _ in counter.most_common(3)]}")
    if not misses:
        print("  none")

    if args.dump_missing:
        Path(args.dump_missing).write_text(
            json.dumps({c: sorted(v) for c, v in misses.items()},
                       ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"\nmissing terms written to {args.dump_missing}")

    if args.dry_run:
        print(f"\ndry run: {len(touched)} batch files would be rewritten")
        return 0

    backup = fa_dir / "_pre_repair"
    if not backup.exists():
        backup.mkdir(parents=True)
        for path in fa_dir.glob("*.xlsx"):
            shutil.copy2(path, backup / path.name)
        print(f"\nkept the untouched batches in {backup}")

    for name, frame in touched.items():
        with pd.ExcelWriter(fa_dir / f"{name}_fa.xlsx", engine="openpyxl") as writer:
            frame.to_excel(writer, index=False, sheet_name="onet_fa")
    print(f"\nrewrote {len(touched)} batch files in {fa_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

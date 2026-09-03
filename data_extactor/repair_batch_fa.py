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

REPAIR_COLUMNS = ["job_title", "aliases", "skills", "knowledge", "abilities",
                  "work_context", "career_path_next", "responsibilities"]

PERSIAN_RE = re.compile(r"[؀-ۿ]")
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
    changed = collections.Counter()
    misses = collections.defaultdict(collections.Counter)
    rejected: list[str] = []
    unaligned = collections.Counter()
    touched = {}

    for name, (left, right) in pairs.items():
        out = right.copy()
        dirty = False
        for i in range(len(out)):
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
                    out.iat[i, out.columns.get_loc("job_title")] = f"{title} ({english_title})"
                    changed["job_title_english_restored"] += 1
                    dirty = True

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
                table = canonical.get(column, {})
                joined = " | ".join(table.get(source, str(x).strip())
                                    for source, x in zip(english, supplied))
                if joined != str(out[column].iloc[i]).strip():
                    out.iat[i, out.columns.get_loc(column)] = joined
                    changed[column] += len(supplied)
                    dirty = True

            for column, table in canonical.items():
                english = items(left[column].iloc[i])
                persian = items(out[column].iloc[i])
                if not english:
                    continue
                if len(english) != len(persian):
                    unaligned[column] += 1
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
                        rebuilt.append(term)
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

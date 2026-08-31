#!/usr/bin/env python3
"""
merge_occupations_fa.py — build the seed corpus from the second translation pass plus
the military occupations that only ever existed in Persian.

    python3 merge_occupations_fa.py --dry-run     # report only, write nothing
    python3 merge_occupations_fa.py               # write ../Merged_Occupations.xlsx

Two inputs that have nothing in common but their column names:

  * `onet_master_database_fa.xlsx` — 1016 O*NET occupations, the output of
    `translate_script.py` → `repair_batch_fa.py` → `aggregate_batch_fa.py`. It replaces
    the 1014 O*NET rows the old `Merged_Occupations.xlsx` carried, which came from an
    earlier and weaker pass; nothing is matched between the two, because a retranslated
    corpus shares no cell with its predecessor and pretending otherwise would only
    produce a diff nobody could read.
  * the **102 military rows of the existing `Merged_Occupations.xlsx`** (`source` names
    them). They have no English original anywhere — they were written in Persian — so
    they pass through this script rather than through the translation pipeline.

Three things are reshaped on the way in, and each one is a contract this repo already
has rather than a preference:

1. **`work_context` stays a list.** It arrives from the batches as O*NET's own context
   factors, 14 of them per record, and it is written through unchanged. This was briefly
   flattened to «، »-joined prose, because the engine used to count the column as prose;
   holding 14 items that way made it stop discriminating — the whole cell is one piece to
   `profile.record_items`, so «فشار زمانی» matched 801 of 1118 records instead of 54.
   `job_qa_service/columns.py` moved it out of `PROSE_COLUMNS` instead. The 102 military
   rows carry one authored sentence there, which is simply a one-item list.
2. **The English half of `job_title` moves into `aliases`.** The batches write
   «توسعه‌دهندگان نرم‌افزار (Software Developers)»; the military rows have no English half,
   and `career_path_next` names occupations by their Persian alone — so a title carrying
   its English never equals the link that points at it. Persian alone is the house style
   on all three counts. The English is not dropped, it is appended to `aliases`, where
   `engine._title_alias_text` still embeds it and an English query still reaches the
   record.
3. **The military `tools` are translated.** All 277 distinct items were English generic
   descriptors ("Precision rifles", "Night vision goggles") and not one was a brand name,
   which is the one thing that is allowed to stay English in that column.
   `merge_fixes/military_tools_fa.json` is the table; it is checked for completeness on
   every run and the script refuses to write if a term is missing from it.
4. **The military rows are filled out from the O*NET taxonomies.** They were authored with
   three `skills`, three `knowledge`, three `abilities` and one `work_context` each,
   against an O*NET median of 7 / 8 / 19 / 18. `profile.coverage` is set arithmetic over
   items, so a record with three skills covers less of a rich profile than one with eight,
   and the military occupations ranked below the O*NET ones in advanced search even on
   military profiles. `merge_fixes/military_enrichment.json` supplies what they were
   missing, **selected from the same four fixed taxonomies every O*NET row is described
   with** rather than invented — a phrase written fresh here would be an item no user
   profile could match and no other record shares, which is the problem being fixed, not
   a fix for it. What each row already held is specific and good («بالستیک برد بلند») and
   is kept, listed first, ahead of the shared vocabulary.
   `merge_fixes/build_military_enrichment.py` writes that file by composing a per-service
   base, thirteen trait bundles and per-occupation extras, and it checks every phrase
   against `translation_fixes/40_taxonomy_canonical.json` before writing.

`row_index` is renumbered over the whole file and `source` says which pass a row came
from, both as bookkeeping — `scripts/seed_from_xlsx.py` projects onto its own ten columns
and drops them, along with `job_code`.

**The military rows are read from `Merged_Occupations_v1.xlsx`, not from the output.**
The first run copies the corpus it is about to replace to that name and reads it from
then on, because the two would otherwise be the same file: a second run would find the
military `tools` already translated, fail every lookup in the table, and refuse to write.
Reading the frozen original instead makes the script idempotent and keeps the table
meaning one thing — English in, Persian out.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from pathlib import Path

import pandas as pd
from openpyxl import load_workbook
from openpyxl.styles import Alignment, Font, PatternFill

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent

COLUMNS = ["job_title", "aliases", "tools", "skills", "knowledge", "abilities",
           "work_context", "career_path_next", "description", "responsibilities"]
OUT_COLUMNS = ["row_index", "job_code"] + COLUMNS + ["source"]

# Where a comma is punctuation rather than a separator — job_qa_service/columns.py.
PROSE_COLUMNS = ["job_title", "description"]

# The O*NET median item count per column, and the ceiling the enriched military rows are
# held to. `profile.coverage` divides by the *user's* item count, never the record's, so a
# record carrying more items can only score higher — a military row given 26 abilities
# against an O*NET median of 19 would win on volume rather than on fit, and did: it put
# «متخصصان جنگ الکترونیک زمینی» above «برنامه‌نویسان کامپیوتر» for a programmer's profile.
# The enrichment is ordered most-specific-first, so what a cap drops is the generic tail.
CAP = {"skills": 7, "knowledge": 8, "abilities": 19, "work_context": 18}

SOURCE_ONET = "O*NET — ترجمهٔ دوم (بازبینی‌شده)"
SOURCE_MILITARY = "تولیدی — تکمیل مشاغل نظامی"
SOURCE_AUTHORED = "تولیدی — مشاغل فناوری اطلاعات"

# The four columns that are a fixed O*NET taxonomy shared by every record, and so the
# four an authored row may not write freely in. See `check_vocabulary`.
TAXONOMY_COLUMNS = ["skills", "knowledge", "abilities", "work_context"]

# The 76 O*NET residual categories — "Engineers, All Other" and its kind. Two things
# are wrong with them as the batches leave them. Their titles keep O*NET's inverted
# list form («مهندسان، سایر»), which is not how the phrase is written in Persian; and
# the second translation pass rendered "All Other" two different ways, so 68 records
# say «، سایر» and 8 say «، همه موارد دیگر» for exactly the same thing.
#
# A record is residual by its *description* — a 62-character «تمام Xهایی که به طور
# جداگانه فهرست نشده‌اند» — not by its title, which is what keeps «مونتاژکاران موتور و
# سایر ماشین‌آلات» and «داوران، قضاوت‌کنندگان و سایر مقامات ورزشی» out of it: those say
# «سایر» about the work, not about the classification. The title pattern is then
# required *as well*, which is what excludes «سرپرستان رده اول سایر متخصصان عملیات
# تاکتیکی» — a real supervisory occupation whose description says "not classified
# separately" about the specialists it supervises rather than about itself.
RESIDUAL_DESC = re.compile(r"(به\s*طور\s*جداگانه|به‌طور\s*جداگانه).{0,20}(فهرست|طبقه)")
RESIDUAL_TAIL = re.compile(
    r"^(.*)،\s*(?:سایر(?:\s+\S+)*|همه\s+(?:موارد|مشاغل)\s+دیگر)$")
RESIDUAL_SUFFIX = " (طبقه‌بندی‌نشده)"

LATIN = re.compile(r"[A-Za-z]")
# «فارسی (English)» — the house style of the batches, and what is unpacked here.
TITLE_RE = re.compile(r"^(.*?)\s*\(([^()]*[A-Za-z][^()]*)\)\s*$")


def items(value) -> list[str]:
    return [p.strip() for p in str(value).split("|") if p.strip()]


def unique(values) -> list[str]:
    """The same members, first occurrence kept.

    Canonicalizing `tools` collapsed near-identical English terms onto one Persian
    phrase — "Computer aided design CAD software" and "Computer aided design and
    drafting CADD software" are one phrase in Persian — which leaves the same item
    twice in eight cells. The batches must keep it, since `aggregate_batch_fa.py`
    holds every cell to its English item count; the corpus must not, because a client
    drawing one chip per item would draw that one twice.
    """
    seen, out = set(), []
    for value in values:
        if value not in seen:
            seen.add(value)
            out.append(value)
    return out


def split_title(title: str) -> tuple[str, str]:
    """«فارسی (English)» -> (فارسی, English); anything else -> (as is, "")."""
    match = TITLE_RE.match(str(title).strip())
    return (match.group(1).strip(), match.group(2).strip()) if match \
        else (str(title).strip(), "")


def rename_residual_titles(frame: pd.DataFrame) -> dict[str, str]:
    """Rewrites the residual categories' titles in place and returns the mapping.

    `career_path_next` names occupations by their title, so the links have to move with
    them or `check()`'s "every career path resolves" stops holding — 62 links across 45
    records point at one of these. Renaming the titles without the links is the one way
    to get this wrong, and the check is what catches it.
    """
    mapping = {}
    for i in range(len(frame)):
        title = str(frame.at[i, "job_title"]).strip()
        if not RESIDUAL_DESC.search(str(frame.at[i, "description"])):
            continue
        match = RESIDUAL_TAIL.match(title)
        if match:
            mapping[title] = "سایر " + match.group(1).strip() + RESIDUAL_SUFFIX
        elif title.startswith("سایر "):
            mapping[title] = title + RESIDUAL_SUFFIX
    frame["job_title"] = frame.job_title.map(lambda t: mapping.get(str(t).strip(), t))
    frame["career_path_next"] = frame.career_path_next.map(
        lambda cell: " | ".join(mapping.get(it, it) for it in items(cell)))
    return mapping


def load_translated(path: Path) -> pd.DataFrame:
    frame = pd.read_excel(path, dtype=str).fillna("")
    frame.columns = [str(c).strip().lower() for c in frame.columns]
    rows = []
    for i in range(len(frame)):
        row = frame.iloc[i]
        persian, english = split_title(row["job_title"])
        aliases = items(row["aliases"])
        if english and english not in aliases:
            aliases.append(english)          # keep it reachable, out of the title
        record = {"job_code": row.get("job_code", ""), "job_title": persian,
                  "aliases": " | ".join(unique(aliases)),
                  "description": str(row["description"]).strip(),
                  "source": SOURCE_ONET}
        for column in COLUMNS:
            if column not in record:
                record[column] = " | ".join(unique(items(row[column])))
        rows.append(record)
    return pd.DataFrame(rows)


def load_military(path: Path, tools: dict[str, str],
                  enrichment: dict) -> tuple[pd.DataFrame, list[str], list[str]]:
    frame = pd.read_excel(path, dtype=str).fillna("")
    frame.columns = [str(c).strip().lower() for c in frame.columns]
    frame = frame[frame["source"].str.contains("نظامی", na=False)]
    missing, unenriched, rows = [], [], []
    for i in range(len(frame)):
        row = frame.iloc[i]
        rebuilt = []
        for term in items(row["tools"]):
            if term in tools:
                rebuilt.append(tools[term])
            else:
                rebuilt.append(term)
                missing.append(term)

        title = str(row["job_title"]).strip()
        add = enrichment.get(title)
        if add is None:
            unenriched.append(title)
            add = {}
        filled = {}
        for column in COLUMNS:
            value = str(row[column]).strip()
            if column not in add:
                filled[column] = value
                continue
            # what the row already said comes first: it is specific to this occupation,
            # where the taxonomy items are the vocabulary it shares with the corpus
            merged = unique(items(value) + list(add[column]))
            filled[column] = " | ".join(merged[:CAP[column]] if column in CAP else merged)

        # the tools table translates what the row already had; the enrichment adds to it
        rows.append({"job_code": "", **filled,
                     "tools": " | ".join(unique(rebuilt + list(add.get("tools", [])))),
                     "source": SOURCE_MILITARY})
    return pd.DataFrame(rows), sorted(set(missing)), unenriched


def load_authored(path: Path) -> pd.DataFrame:
    """Occupations written by hand because O*NET has no row for them.

    A third input beside the translated batches and the military rows, and it exists for
    the same reason they do: the corpus has to hold the job somebody will search for. The
    O*NET classification predates the split every Persian job posting now makes — سمت
    سرور against سمت کاربر — and «Software Developers» does not stand in for it. What the
    gap cost was retrieval: «برنامه‌نویس بک‌اند» ranked «برنامه‌نویسان ابزار کنترل عددی
    کامپیوتری» first at dense 0.614, a CNC machine-tool occupation that wins on nothing
    but a title holding «برنامه‌نویسان» and «کامپیوتری» at once.

    Kept in JSON rather than typed into the xlsx, because the xlsx is *output*: this
    script rewrites it from its inputs on every run, and a row added to the sheet by hand
    would survive exactly until the next one.
    """
    data = json.loads(path.read_text(encoding="utf-8"))
    rows = [{"job_code": "", **{c: str(row.get(c, "")).strip() for c in COLUMNS},
             "source": data.get("source", SOURCE_AUTHORED)}
            for row in data["occupations"]]
    return pd.DataFrame(rows, columns=["job_code"] + COLUMNS + ["source"])


def check_vocabulary(frame: pd.DataFrame, canonical: Path) -> list[str]:
    """Authored rows must describe themselves in the corpus's own words.

    `skills`, `knowledge`, `abilities` and `work_context` are fixed O*NET taxonomies —
    10 / 33 / 52 / 55 phrases that all 1016 translated rows draw on — and
    `job_qa_service/profile.py` scores a user's items by token containment against those
    cells. A phrase written fresh here would be an item no profile could match and no
    other record shares, which is the thing `translation_fixes/40_taxonomy_canonical.json`
    exists to prevent. The same check `merge_fixes/build_military_enrichment.py` runs
    before it writes, applied to the one other place a human writes into these columns.
    """
    table = json.loads(canonical.read_text(encoding="utf-8"))["canonical"]
    allowed = {column: set(table[column].values()) for column in TAXONOMY_COLUMNS}
    problems = []
    for i in range(len(frame)):
        row = frame.iloc[i]
        for column in TAXONOMY_COLUMNS:
            stray = [it for it in items(row[column]) if it not in allowed[column]]
            if stray:
                problems.append(f"«{row['job_title']}» {column}: {len(stray)} phrase(s) "
                                f"outside the canonical taxonomy: {stray}")
    return problems


def check(frame: pd.DataFrame) -> list[str]:
    """Everything that would make this file wrong to seed from."""
    problems = []
    for column in COLUMNS:
        blank = frame.index[frame[column].str.strip() == ""].tolist()
        if blank:
            problems.append(f"{column}: {len(blank)} empty cells, e.g. rows {blank[:5]}")
    for column in PROSE_COLUMNS:
        piped = frame.index[frame[column].str.contains("|", regex=False)].tolist()
        if piped:
            problems.append(f"{column}: {len(piped)} cells hold '|', which is not a "
                            f"separator in a prose column: rows {piped[:5]}")
    duplicated = frame.job_title[frame.job_title.duplicated()].tolist()
    if duplicated:
        problems.append(f"{len(duplicated)} duplicated job_title: {duplicated[:5]}")
    repeated = [(frame.job_title.iloc[i], column)
                for i in range(len(frame))
                for column in COLUMNS if column not in PROSE_COLUMNS
                if len(items(frame[column].iloc[i]))
                != len(set(items(frame[column].iloc[i])))]
    if repeated:
        problems.append(f"{len(repeated)} cells hold the same item twice: {repeated[:5]}")
    # Every O*NET career path must name a record this corpus holds — those items are
    # occupation titles and `repair_batch_fa.py` writes each one exactly as its own row
    # reads. The authored rows are held to it as well, and that is most of the point of
    # checking at all: their links are typed by hand against a corpus of 1118 titles, and
    # a mistyped one is invisible until somebody follows it. The military rows are not: their next roles were written as
    # generic aspirations («افسر ارتباطات») rather than as links, none of them resolved
    # in the previous corpus either, and fuzzy-matching them onto real records produces
    # confident nonsense — «افسر ارتباطات» lands on «افسران ضداطلاعات».
    titles = set(frame.job_title)
    linked = frame[frame.source.isin([SOURCE_ONET, SOURCE_AUTHORED])]
    unresolved = sorted({it for cell in linked.career_path_next
                         for it in items(cell) if it not in titles})
    if unresolved:
        problems.append(f"{len(unresolved)} career_path_next links name no "
                        f"record: {unresolved[:5]}")
    return problems


def write_xlsx(frame: pd.DataFrame, path: Path) -> None:
    frame.to_excel(path, index=False)
    book = load_workbook(path)
    sheet = book.active
    sheet.title = "occupations"
    fill = PatternFill("solid", start_color="4472C4", end_color="4472C4")
    for cell in sheet[1]:
        cell.fill = fill
        cell.font = Font(name="Arial", bold=True, color="FFFFFF", size=11)
        cell.alignment = Alignment(horizontal="center", vertical="center")
    for row in sheet.iter_rows(min_row=2):
        for cell in row:
            cell.font = Font(name="Arial", size=10)
            cell.alignment = Alignment(wrap_text=False)
    sheet.freeze_panes = "A2"
    for column in sheet.columns:
        width = max((len(str(c.value)) if c.value else 0) for c in column)
        sheet.column_dimensions[column[0].column_letter].width = min(width + 4, 60)
    book.save(path)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--translated", default=str(HERE / "onet_master_database_fa.xlsx"))
    ap.add_argument("--existing", default="",
                    help="where the military rows come from; defaults to the frozen "
                         "Merged_Occupations_v1.xlsx, or to --out on the first run")
    ap.add_argument("--tools", default=str(HERE / "merge_fixes" / "military_tools_fa.json"))
    ap.add_argument("--enrichment",
                    default=str(HERE / "merge_fixes" / "military_enrichment.json"))
    ap.add_argument("--authored",
                    default=str(HERE / "merge_fixes" / "authored_occupations_fa.json"))
    ap.add_argument("--canonical",
                    default=str(HERE / "translation_fixes" / "40_taxonomy_canonical.json"))
    ap.add_argument("--out", default=str(ROOT / "Merged_Occupations.xlsx"))
    ap.add_argument("--backup", default=str(HERE / "Merged_Occupations_v1.xlsx"))
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    out, backup = Path(args.out), Path(args.backup)
    # Never the output: on a second run its military tools are already Persian and every
    # lookup in the table would miss. The backup is frozen at the first run for that.
    existing = Path(args.existing) if args.existing else (backup if backup.exists() else out)
    if not existing.exists():
        print(f"error: no corpus to take the military rows from ({existing})",
              file=sys.stderr)
        return 2

    tools = json.loads(Path(args.tools).read_text(encoding="utf-8"))["tools"]
    enrichment = json.loads(Path(args.enrichment).read_text(encoding="utf-8"))["add"]
    onet = load_translated(Path(args.translated))
    renamed = rename_residual_titles(onet)
    military, missing, unenriched = load_military(existing, tools, enrichment)
    authored = load_authored(Path(args.authored))
    print(f"translated O*NET : {len(onet):5} rows  ({args.translated})")
    print(f"residual titles  : {len(renamed):5} rewritten as «سایر … (طبقه‌بندی‌نشده)»")
    print(f"military         : {len(military):5} rows  ({existing})")
    print(f"authored         : {len(authored):5} rows  ({args.authored})")

    if missing:
        print(f"\nerror: {len(missing)} military tool terms have no translation:",
              file=sys.stderr)
        for term in missing[:20]:
            print(f"  {term}", file=sys.stderr)
        print(f"add them to {args.tools}", file=sys.stderr)
        return 1
    if unenriched:
        print(f"\nerror: {len(unenriched)} military occupations are not in "
              f"{args.enrichment}:", file=sys.stderr)
        for title in unenriched[:20]:
            print(f"  {title}", file=sys.stderr)
        return 1

    merged = pd.concat([onet, military, authored], ignore_index=True)
    merged.insert(0, "row_index", range(len(merged)))
    merged = merged[OUT_COLUMNS]

    problems = check(merged) + check_vocabulary(authored, Path(args.canonical))
    print("\n--- checks ---")
    if problems:
        for line in problems:
            print(f"  FAIL  {line}")
    else:
        print("  no empty cell, no '|' in a prose column, no duplicate title, "
              "every career path resolves,\n  every authored taxonomy phrase is canonical")

    english_titles = merged.index[merged.job_title.map(lambda t: bool(LATIN.search(t)))]
    print(f"\n{len(merged)} rows; {len(english_titles)} titles still carry Latin text")
    listed = merged.work_context.map(lambda v: len(items(v)))
    print(f"work_context: {listed.min()}–{listed.max()} items per row, "
          f"{(listed == 1).sum()} rows holding a single one")

    titles = set(merged.job_title)
    military = merged[merged.source == SOURCE_MILITARY]
    loose = sorted({it for cell in military.career_path_next
                    for it in items(cell) if it not in titles})
    total = sum(len(items(c)) for c in merged.career_path_next)
    print(f"career paths: {total} links, of which {len(loose)} distinct names in the "
          f"military rows point at no record (they never did; see check() for why)")

    if problems:
        print("\nnot writing: fix the failures above", file=sys.stderr)
        return 1
    if args.dry_run:
        print("\ndry run: nothing written")
        return 0

    if out.exists() and not backup.exists():
        shutil.copy2(out, backup)
        print(f"\nkept the previous corpus as {backup}")
    write_xlsx(merged, out)
    print(f"wrote {len(merged)} rows × {len(merged.columns)} columns to {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Backfills knowledge/abilities into a database seeded before those columns existed,
and inserts dataset rows the database does not have yet. Records are matched on job_title.

It also repairs prose columns still holding "|" where a comma belongs, a leftover from
the data_extactor pass that ran every column through the list-separator normalizer.

scripts.seed_from_xlsx skips everything once jobs_info is non-empty, so it cannot fill
columns added later; this script is built for a populated database and is idempotent.
Only approved records are updated — pending suggestions are left as their author wrote them.

Usage (inside the api container or venv, after `alembic upgrade head`):
    python -m scripts.backfill_from_xlsx Merged_Occupations.xlsx
    python -m scripts.backfill_from_xlsx --dry-run     # report only, write nothing
    python -m scripts.backfill_from_xlsx --overwrite   # also replace non-empty values
"""
import re
import sys

import pandas as pd

from app.database import SessionLocal
from app.models import JobRecord, JobStatus

COLUMNS = ["job_title", "aliases", "tools", "skills", "knowledge", "abilities",
           "work_context", "career_path_next", "description", "responsibilities"]

# The columns this script exists to fill. Everything else on an existing record is left
# alone, so an admin's edits are never overwritten by the dataset.
BACKFILL_COLUMNS = ["knowledge", "abilities"]

# Single-value columns where a comma is punctuation. An early data_extactor pass ran
# every column through the list-separator normalizer and left "|" in these; the repair
# below undoes that on rows already in the database. "|" stays untouched everywhere
# else, since it is the intended separator for the list columns.
PROSE_COLUMNS = ["job_title", "description", "work_context"]


def _key(title) -> str:
    """Match key. Tolerates the Arabic ي/ك spellings, stray whitespace, and «،»/«|»
    drift — a database seeded from the mangled dataset still matches the repaired one,
    instead of every affected title looking like a brand-new job."""
    text = str(title).replace("ي", "ی").replace("ك", "ک").replace("|", "،")
    return " ".join(text.replace("،", " ، ").split())


def _load(xlsx_path: str) -> pd.DataFrame:
    df = pd.read_excel(xlsx_path)
    df.columns = [str(c).strip().lower() for c in df.columns]
    for col in COLUMNS:
        if col not in df.columns:
            df[col] = ""
        df[col] = df[col].fillna("").astype(str)
    return df[df["job_title"].str.strip() != ""]


def main(xlsx_path: str, overwrite: bool = False, dry_run: bool = False):
    df = _load(xlsx_path)
    db = SessionLocal()
    try:
        approved = db.query(JobRecord).filter(JobRecord.status == JobStatus.approved).all()
        by_key = {}
        for record in approved:
            by_key.setdefault(_key(record.job_title), record)
        # Insert decisions consider every status, so a title that exists only as a
        # pending suggestion does not get duplicated as an approved record.
        known = {_key(title) for (title,) in db.query(JobRecord.job_title)}

        filled, inserted, unchanged = 0, 0, 0
        for _, row in df.iterrows():
            record = by_key.get(_key(row["job_title"]))

            if record is None:
                if _key(row["job_title"]) in known:
                    unchanged += 1                     # present, but not as approved
                    continue
                db.add(JobRecord(**{c: row[c] for c in COLUMNS}, status=JobStatus.approved))
                inserted += 1
                continue

            changed = [c for c in BACKFILL_COLUMNS
                       if row[c].strip() and (overwrite or not (getattr(record, c) or "").strip())]
            if not changed:
                unchanged += 1
                continue
            for col in changed:
                setattr(record, col, row[col])
            filled += 1

        # Rows inserted above come from the repaired dataset; rows already stored may
        # still carry "|" where a comma belongs. This only rewrites punctuation.
        repaired = 0
        for record in approved:
            fixes = {col: re.sub(r"\s*\|\s*", "، ", getattr(record, col) or "").strip()
                     for col in PROSE_COLUMNS if "|" in (getattr(record, col) or "")}
            if not fixes:
                continue
            for col, value in fixes.items():
                setattr(record, col, value)
            repaired += 1

        print(f"filled     {filled:5d} record(s) with {'/'.join(BACKFILL_COLUMNS)}")
        print(f"inserted   {inserted:5d} new approved record(s)")
        print(f"unchanged  {unchanged:5d} record(s)"
              + ("" if overwrite else " (already populated; --overwrite replaces them)"))
        print(f"repaired   {repaired:5d} record(s) holding '|' in {'/'.join(PROSE_COLUMNS)}")

        if dry_run:
            db.rollback()
            print("\nDry run: nothing was written.")
            return
        db.commit()
        print("\nDone. The engine still serves the previous corpus — POST /admin/rebuild "
              "(or restart the api) to pick this up.")
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    flags = {a for a in sys.argv[1:] if a.startswith("--")}
    paths = [a for a in sys.argv[1:] if not a.startswith("--")]
    unknown = flags - {"--overwrite", "--dry-run"}
    if unknown:
        sys.exit(f"unknown flag(s): {', '.join(sorted(unknown))}")
    main(paths[0] if paths else "Merged_Occupations.xlsx",
         overwrite="--overwrite" in flags, dry_run="--dry-run" in flags)

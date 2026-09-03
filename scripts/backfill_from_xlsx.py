import re
import sys

import pandas as pd

from src.database import connect, db, disconnect
from src.models import JobStatus

COLUMNS = ["job_title", "aliases", "tools", "skills", "knowledge", "abilities",
           "work_context", "career_path_next", "description", "responsibilities"]

BACKFILL_COLUMNS = ["knowledge", "abilities"]

PROSE_COLUMNS = ["job_title", "description"]


def _key(title) -> str:
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
    connect()
    try:
        records = db.jobrecord.find_many()
        approved = [r for r in records if r.status == JobStatus.approved]
        by_key = {}
        for record in approved:
            by_key.setdefault(_key(record.job_title), record)
        known = {_key(r.job_title) for r in records}

        updates: list[tuple[int, dict[str, str]]] = []
        inserts: list[dict[str, str]] = []
        unchanged = 0
        for _, row in df.iterrows():
            record = by_key.get(_key(row["job_title"]))

            if record is None:
                if _key(row["job_title"]) in known:
                    unchanged += 1
                    continue
                inserts.append({c: row[c] for c in COLUMNS})
                continue

            changed = {c: row[c] for c in BACKFILL_COLUMNS
                       if row[c].strip()
                       and (overwrite or not (getattr(record, c) or "").strip())}
            if not changed:
                unchanged += 1
                continue
            updates.append((record.id, changed))

        repairs: list[tuple[int, dict[str, str]]] = []
        for record in approved:
            fixes = {col: re.sub(r"\s*\|\s*", "، ", getattr(record, col) or "").strip()
                     for col in PROSE_COLUMNS if "|" in (getattr(record, col) or "")}
            if fixes:
                repairs.append((record.id, fixes))

        print(f"filled     {len(updates):5d} record(s) with {'/'.join(BACKFILL_COLUMNS)}")
        print(f"inserted   {len(inserts):5d} new approved record(s)")
        print(f"unchanged  {unchanged:5d} record(s)"
              + ("" if overwrite else " (already populated; --overwrite replaces them)"))
        print(f"repaired   {len(repairs):5d} record(s) holding '|' in {'/'.join(PROSE_COLUMNS)}")

        if dry_run:
            print("\nDry run: nothing was written.")
            return

        if inserts:
            db.jobrecord.create_many(
                data=[{**values, "status": JobStatus.approved} for values in inserts])
        for record_id, values in [*updates, *repairs]:
            db.jobrecord.update(where={"id": record_id}, data=values)

        print("\nDone. The engine still serves the previous corpus — POST /admin/rebuild "
              "(or restart the api) to pick this up.")
    finally:
        disconnect()


if __name__ == "__main__":
    flags = {a for a in sys.argv[1:] if a.startswith("--")}
    paths = [a for a in sys.argv[1:] if not a.startswith("--")]
    unknown = flags - {"--overwrite", "--dry-run"}
    if unknown:
        sys.exit(f"unknown flag(s): {', '.join(sorted(unknown))}")
    main(paths[0] if paths else "Merged_Occupations.xlsx",
         overwrite="--overwrite" in flags, dry_run="--dry-run" in flags)

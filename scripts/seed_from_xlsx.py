"""One-time seed: imports the xlsx dataset as approved records and creates the
bootstrap super admin from env. Usage (inside the api container or venv):
    python -m scripts.seed_from_xlsx Merged_Occupations.xlsx
"""
import sys

import pandas as pd

from app.auth import hash_password
from app.config import settings
from app.database import SessionLocal
from app.models import User, Role, JobRecord, JobStatus

COLUMNS = ["job_title", "aliases", "tools", "skills", "knowledge", "abilities",
           "work_context", "career_path_next", "description", "responsibilities"]


def main(xlsx_path: str):
    # Schema must already exist: run `alembic upgrade head` first.
    db = SessionLocal()
    try:
        # The bootstrap account: every other account is created through the API by the
        # level above it, so the first super_admin has to come from somewhere else.
        if not db.query(User).filter(User.username == settings.ADMIN_USERNAME).first():
            db.add(User(username=settings.ADMIN_USERNAME,
                        hashed_password=hash_password(settings.ADMIN_PASSWORD),
                        role=Role.super_admin))
            print(f"Super admin '{settings.ADMIN_USERNAME}' created.")

        if db.query(JobRecord).count() == 0:
            df = pd.read_excel(xlsx_path)
            df.columns = [str(c).strip().lower() for c in df.columns]
            for col in COLUMNS:
                if col not in df.columns:
                    df[col] = ""
                df[col] = df[col].fillna("").astype(str)
            df = df[df["job_title"].str.strip() != ""]
            db.add_all(JobRecord(**{c: row[c] for c in COLUMNS}, status=JobStatus.approved)
                       for _, row in df.iterrows())
            print(f"Seeded {len(df)} approved job records.")
        else:
            print("jobs_info is not empty; skipping dataset seed.")
        db.commit()
    finally:
        db.close()


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "Merged_Occupations.xlsx")

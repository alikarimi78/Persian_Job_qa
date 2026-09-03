import sys

import pandas as pd

from src.auth import hash_password
from src.config import settings
from src.database import connect, db, disconnect
from src.models import JobStatus, Role

COLUMNS = ["job_title", "aliases", "tools", "skills", "knowledge", "abilities",
           "work_context", "career_path_next", "description", "responsibilities"]


def main(xlsx_path: str):
    connect()
    try:
        if not db.user.find_unique(where={"username": settings.ADMIN_USERNAME}):
            db.user.create(data={
                "username": settings.ADMIN_USERNAME,
                "hashed_password": hash_password(settings.ADMIN_PASSWORD),
                "role": Role.super_admin})
            print(f"Super admin '{settings.ADMIN_USERNAME}' created.")

        if db.jobrecord.count() == 0:
            df = pd.read_excel(xlsx_path)
            df.columns = [str(c).strip().lower() for c in df.columns]
            for col in COLUMNS:
                if col not in df.columns:
                    df[col] = ""
                df[col] = df[col].fillna("").astype(str)
            df = df[df["job_title"].str.strip() != ""]
            db.jobrecord.create_many(
                data=[{**{c: row[c] for c in COLUMNS}, "status": JobStatus.approved}
                      for _, row in df.iterrows()])
            print(f"Seeded {len(df)} approved job records.")
        else:
            print("jobs_info is not empty; skipping dataset seed.")
    finally:
        disconnect()


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "Merged_Occupations.xlsx")

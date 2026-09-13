EXPECTED_COLUMNS = ["job_title", "aliases", "tools", "skills", "knowledge", "abilities",
                    "work_context", "career_path_next", "description", "responsibilities"]

PROSE_COLUMNS = ["job_title", "description"]

# Not one of the ten, and never part of a record's text: which organization's corpus the
# record sits in. The DataFrame column is an integer, so the database's NULL — the public
# corpus every organization searches — arrives here as 0.
ORGANIZATION_COLUMN = "organization_id"
PUBLIC_ORGANIZATION = 0

FIELD_LABELS = {
    "job_title": "عنوان شغل", "aliases": "نام‌های دیگر", "tools": "ابزارها",
    "skills": "مهارت‌ها و شایستگی‌ها", "knowledge": "دانش تخصصی",
    "abilities": "توانایی‌ها", "work_context": "محیط کاری",
    "career_path_next": "مسیر شغلی بعدی", "description": "شرح شغل",
    "responsibilities": "وظایف و مسئولیت‌ها",
}

DISCOVERY_FIELDS = ["description", "responsibilities", "skills", "knowledge", "abilities",
                    "tools", "work_context", "career_path_next"]

DETAIL_FIELDS = DISCOVERY_FIELDS + ["aliases"]

RANKED_FIELDS = ["tools", "responsibilities", "career_path_next"]

DISCOVERY_PRIMARY = ["description", "responsibilities"]

EMPTY_CELLS = {"", "-", "–", "—", "_"}

PROFILE_FIELDS = ["skills", "knowledge", "abilities", "responsibilities",
                  "work_context", "career_path_next"]

PROFILE_REQUIRED = ["skills"]

PROFILE_LABELS = {f: FIELD_LABELS[f] for f in PROFILE_FIELDS}

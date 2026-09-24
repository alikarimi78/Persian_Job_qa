EXPECTED_COLUMNS = ["job_title", "aliases", "tools", "skills", "knowledge", "abilities",
                    "work_context", "career_path_next", "description", "responsibilities"]

PROSE_COLUMNS = ["job_title", "description"]

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

_DETAIL_TAIL = ["tools", "career_path_next", "aliases"]
DETAIL_FIELDS = [f for f in DISCOVERY_FIELDS if f not in _DETAIL_TAIL] + _DETAIL_TAIL

RANKED_FIELDS = ["tools", "responsibilities", "career_path_next"]

DISCOVERY_PRIMARY = ["description", "responsibilities"]

EMPTY_CELLS = {"", "-", "–", "—", "_"}

PROFILE_FIELDS = ["skills", "knowledge", "abilities", "responsibilities",
                  "work_context", "career_path_next", "tools"]

PROFILE_REQUIRED = ["skills", "knowledge", "abilities", "work_context"]
VOCABULARY_FIELDS = [f for f in PROFILE_FIELDS if f != "responsibilities"]

MATCH_COLUMNS = ["job_title", "aliases", "skills", "knowledge", "abilities",
                 "responsibilities", "work_context", "tools", "description"]

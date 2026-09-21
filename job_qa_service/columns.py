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

# The client and `routers/search/schemas.py` hold the same list; that one carries the counts.
PROFILE_REQUIRED = ["skills", "knowledge", "abilities", "work_context"]
VOCABULARY_FIELDS = [f for f in PROFILE_FIELDS if f != "responsibilities"]

# Where a profile item is looked for once its own column does not hold it, most telling first.
# A person who can «برنامه‌نویسی» is covered by the record whose duties say so, whichever column
# they typed it in — the taxonomy columns are closed vocabularies and cannot hold the word at all.
# `career_path_next` is deliberately absent: that column is where this job leads, not what it is,
# so «پایگاه داده» meeting «مدیران پایگاه داده» there is not coverage.
MATCH_COLUMNS = ["job_title", "aliases", "skills", "knowledge", "abilities",
                 "responsibilities", "work_context", "tools", "description"]

PROFILE_LABELS = {f: FIELD_LABELS[f] for f in PROFILE_FIELDS}

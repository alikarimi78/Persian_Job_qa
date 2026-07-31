# -*- coding: utf-8 -*-
"""The dataset's ten canonical columns, and the projections taken of them.

This is the engine's half of the column contract that also lives in `app/models.py`,
`app/schemas.py`, `app/engine_manager.py` and `scripts/seed_from_xlsx.py`. Adding a
content column means editing every one of them plus the frontend's `JobForm.jsx`; see
the checklist in CLAUDE.md.
"""

EXPECTED_COLUMNS = ["job_title", "aliases", "tools", "skills", "knowledge", "abilities",
                    "work_context", "career_path_next", "description", "responsibilities"]

# The three columns where a comma is punctuation rather than a list separator;
# everything else is a "|"-joined list. A generated draft is checked against this
# before it leaves the engine, so a model reaching for "|" in prose cannot
# reintroduce the corruption the dataset was repaired of.
PROSE_COLUMNS = ["job_title", "description", "work_context"]

FIELD_LABELS = {
    "job_title": "عنوان شغل", "aliases": "نام‌های دیگر", "tools": "ابزارها",
    "skills": "مهارت‌ها و شایستگی‌ها", "knowledge": "دانش تخصصی",
    "abilities": "توانایی‌ها", "work_context": "محیط کاری",
    "career_path_next": "مسیر شغلی بعدی", "description": "شرح شغل",
    "responsibilities": "وظایف و مسئولیت‌ها",
}

# Shown when a job is presented as a whole profile rather than as an answer to one intent
DISCOVERY_FIELDS = ["description", "responsibilities", "skills", "knowledge", "abilities",
                    "tools", "work_context", "career_path_next"]

# Column order of the per-field detail boxes that ride along with every answer
# (`details`). job_title is absent on purpose: it heads the boxes rather than being
# one. Derived from DISCOVERY_FIELDS so adding a content column reaches both.
DETAIL_FIELDS = DISCOVERY_FIELDS + ["aliases"]

# The discovery path has no intent to key the boxes on — the user described a job
# instead of asking about one facet of it — so the profile leads with the two fields
# that introduce a job and the rest of the record rides along behind them.
DISCOVERY_PRIMARY = ["description", "responsibilities"]

# What the dataset writes in a cell it has nothing for. A box rendering «-» is
# worse than no box, so such a cell is treated as absent.
EMPTY_CELLS = {"", "-", "–", "—", "_"}

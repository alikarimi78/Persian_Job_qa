# -*- coding: utf-8 -*-
"""The dataset's ten canonical columns, and the projections taken of them.

This is the engine's half of the column contract that also lives in `app/models.py`,
`app/schemas.py`, `app/engine_manager.py` and `scripts/seed_from_xlsx.py`. Adding a
content column means editing every one of them plus the frontend's `JobForm.jsx`; see
the checklist in CLAUDE.md.
"""

EXPECTED_COLUMNS = ["job_title", "aliases", "tools", "skills", "knowledge", "abilities",
                    "work_context", "career_path_next", "description", "responsibilities"]

# The two columns where a comma is punctuation rather than a list separator;
# everything else is a "|"-joined list. A generated draft is checked against this
# before it leaves the engine, so a model reaching for "|" in prose cannot
# reintroduce the corruption the dataset was repaired of.
#
# `work_context` was here until the second translation pass (2026-08-29), and moved
# because the *data* moved: it now carries O*NET's own context factors, 14 of them per
# record, where the first corpus carried one authored sentence. Holding 14 items as
# prose made the column stop discriminating — the whole cell is one piece to
# `profile.record_items`, so a 57-token blob contains almost any item a user types, and
# «فشار زمانی» matched 801 of 1118 records against 54 of 1116 before. A record whose
# work_context genuinely is one sentence (the 102 military rows, and anything suggested
# through the form before this) is simply a one-item list.
PROSE_COLUMNS = ["job_title", "description"]

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

# The list columns whose stored order carries no information — the only ones there is
# anything to gain by ranking against a question.
#
# The other four are already ranked, by O*NET rather than by us.
# `data_extactor/aggrigation_script_for_jobs.py` extracts `skills`, `knowledge`,
# `abilities` and `work_context` under a scale filter and sorts them by the survey's own
# rating (`df.sort_values("Data Value", ascending=False)`, with `sort_items = False` so
# the dedup keeps that order), which the translation passes preserved item for item. So
# the first five items of those columns *are* the five that matter most to the
# occupation, measured rather than guessed, and nothing an LLM or an embedding could
# say about them would be an improvement. The corpus confirms it: no pair of common
# items keeps a consistent relative order across records, which alphabetical order
# would — 0 of 45 pairs in `skills`, 0 of 91 in `abilities`.
#
# These three were extracted with no scale, so `dedup_keep_order` sorted them
# alphabetically instead (`tools` keeps the source file's order, which the Persian
# prefixes added in translation have since scrambled). «Django» sits at position 47 of
# the 293 tools of «برنامه‌نویسان کامپیوتر» for no reason whatever, and that is the
# order this list exists to replace.
#
# `aliases` is left out although it is alphabetical too: it holds a job's other names,
# ten at the median, and no name is a better answer to a question than another.
RANKED_FIELDS = ["tools", "responsibilities", "career_path_next"]

# The discovery path has no intent to key the boxes on — the user described a job
# instead of asking about one facet of it — so the profile leads with the two fields
# that introduce a job and the rest of the record rides along behind them.
DISCOVERY_PRIMARY = ["description", "responsibilities"]

# What the dataset writes in a cell it has nothing for. A box rendering «-» is
# worse than no box, so such a cell is treated as absent.
EMPTY_CELLS = {"", "-", "–", "—", "_"}

# ---------- advanced search ----------
# The columns a person may describe *themselves* with, in the order the form asks for
# them. Deliberately not all ten: `job_title` and `aliases` are what the plain search
# box is for — someone who knows the name of the job types it there — and `description`
# is a summary of the other columns rather than something anyone lists.
#
# `work_context` is in the list although it is prose: the user still enters short
# phrases («فضای باز»، «کار تیمی») and they are matched against the whole cell, since
# there is nothing to split a sentence on. See `profile.record_items`.
#
# **`tools` is deliberately absent**, and this is a fact about the data rather than a
# preference: the O*NET tool names were never translated, so 1099 of the 1116 cells are
# Latin-only («AutoCAD | Revit | Adobe Acrobat»), including the generated military rows.
# A Persian «آچار» can never match any of them, so offering the field would report a
# permanent 0% and make the analysis text say the person lacks every tool they listed.
# It stays in the suggestion form (all ten columns do) and in `_combined_text`, where
# the dense channel still reads it. Translating that column is what would let this list
# grow by one line.
PROFILE_FIELDS = ["skills", "knowledge", "abilities", "responsibilities",
                  "work_context", "career_path_next"]

# What a profile must carry before it is worth ranking 1116 records against. `skills`
# because it is the column that most decides what an occupation *is* — tools and
# knowledge follow from it, and a profile of tools alone matches every job that happens
# to use a computer. The counts live in `config.py`, with the rest of the tuning.
PROFILE_REQUIRED = ["skills"]

# The columns the analysis is written from, flagged `primary` in the detail boxes. The
# whole profile is the question here, so the fields the user actually filled in are the
# ones to open — decided per request in `engine.analyze`, unlike DISCOVERY_PRIMARY.
PROFILE_LABELS = {f: FIELD_LABELS[f] for f in PROFILE_FIELDS}

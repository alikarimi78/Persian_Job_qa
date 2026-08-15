# -*- coding: utf-8 -*-
"""Engine settings: environment plus the tuning constants retrieval is calibrated on.

Everything here is read from the **real process environment at import time**, which is
the whole reason this file is separate from `app/config.py`: that one is a
pydantic-settings object for the web layer and never reaches this module. Loading a
`.env` through pydantic would not set these — in deployment the compose `env_file`
injects them into the environment instead.
"""

import os

EMBED_MODEL_NAME = os.getenv("EMBED_MODEL_NAME", "BAAI/bge-m3")
EMB_CACHE_DIR = os.getenv("EMB_CACHE_DIR", "emb_cache")

LLM_MODEL = os.getenv("LLM_MODEL", "gpt-4o-mini")
LLM_BASE_URL = os.getenv("OPENAI_BASE_URL")
LLM_API_KEY = os.getenv("OPENAI_API_KEY")
OCCUPATIONS_PATH = os.getenv("OCCUPATIONS_PATH")
LLM_MAX_RETRIES, LLM_BASE_DELAY = 3, 2.0

W_FULL, W_TITLE = 0.6, 0.4      # dense hybrid weights (sum = 1)
RRF_K = 60
MAX_CANDIDATES = 15
SCAN_DEPTH = 5                  # depth searched for a distinct secondary job

# Calibrated for bge-m3 on this dataset (correct ≈ 0.58–0.66, OOD ceiling ≈ 0.36).
# Changing EMBED_MODEL_NAME invalidates every threshold below.
THRESHOLD_MATCH  = 0.49         # dense out-of-domain gate
THRESHOLD_SPARSE = 0.15         # sparse out-of-domain gate
SECONDARY_MIN    = 0.50         # min dense score of the 2nd job (interdisciplinary)
SECONDARY_MARGIN = 0.01         # max gap between 1st and 2nd job
PAIR_SIM_MAX     = 0.85         # above this, two jobs are near-duplicates -> single mode

# Job-request mode. A spec description must match an existing record more closely
# than a plain question does before we call it "the same job", hence the higher
# bar than THRESHOLD_MATCH; below DISCOVERY_FLOOR the text is not about work at all.
DISCOVERY_MATCH   = 0.60        # >= this: an existing job already covers the request
DISCOVERY_FLOOR   = 0.35        # < this (and sparse weak too): out of domain, do not invent
DISCOVERY_RELATED = 3           # neighbouring jobs shown to the user / fed to the generator

# Advanced search (profile analysis). A different question from the two above: not
# "what is this job" or "invent me one", but "which of the 1116 records fits the
# capabilities I listed". Two independent signals, deliberately kept apart until the
# end because they fail differently — dense similarity understands Persian synonymy but
# cannot say *which* of the user's items it honoured, while coverage can name every
# matched item but only sees the words that are actually there.
PROFILE_TOP_N        = 5        # matches returned, ranked
PROFILE_W_DENSE      = 0.5      # weight of the hybrid dense score (~0.35–0.75 here)
PROFILE_W_COVER      = 0.5      # weight of the fraction of the user's items found
# What dense alone has to reach before a profile with **zero** matched items is still
# called a match. Measured: real profiles land at 0.65–0.75 on their own occupation
# even when the wording differs, while «تربیت اژدها / پرواز با جارو / عصای جادویی»
# — which matches no item anywhere — still measures 0.53 against «مربیان حیوانات»,
# because dense similarity is topical and cannot tell a real job from an invented one
# (the same reason DISCOVERY_FLOOR is not the realism check). Between those two ranges
# is the only place this line can sit.
PROFILE_DENSE_ONLY   = 0.62
PROFILE_MIN_ITEMS    = 2        # items the required field (skills) must carry
PROFILE_MIN_FIELDS   = 2        # fields that must be filled in at all
PROFILE_TOKEN_MIN    = 4        # below this a token is an affix or stopword, as above

# Question-path title tiebreak. Dense similarity ranks «خدمه توپخانه و موشک» (0.667)
# above «افسران توپخانه و موشک» (0.650) for «وظایف افسر توپخانه چیست؟» — same unit,
# wrong rank — so the answer describes the crew when the user asked about officers.
# When the leaders are this close, a title that actually shares a content word with
# the question is the better bet. Applied only in the question path: the discovery
# path matches a description of duties against titles, where the overlap is noise.
TITLE_TIEBREAK_MARGIN = 0.05    # dense gap within which titles may break the tie
TITLE_TIEBREAK_DEPTH  = 4       # how deep to look for a better-titled candidate
TITLE_TOKEN_MIN       = 4       # below this a token is an affix or stopword, not content

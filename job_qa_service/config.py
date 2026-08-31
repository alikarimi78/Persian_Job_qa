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

# How the corpus is fed to the encoder. Both are bounds on *memory*, and both exist
# because the corpus grew: the first translation pass wrote records of ~200 tokens,
# the second writes 1406 at the median and 4401 at the longest (responsibilities,
# work_context and the four taxonomies are all lists now). bge-m3 accepts 8192 tokens
# and sentence-transformers batches 32 of them by default, sorted longest-first — so
# the very first batch of a full re-encode asks a 4 GB card for more than it has and
# the engine never loads.
#
# Measured on the dev GTX 1050 (3.94 GiB), fp32 weights alone taking 2.12 GiB, over
# the longest records in the corpus — peak reserved, and throughput per text:
#
#   seq=1024  bs=1 2.20 GiB  bs=4 2.41  bs=8 2.69  bs=16 3.22   ~0.8 s/text
#   seq=2048  bs=1 2.27 GiB  bs=4 2.69  bs=8 3.22               ~1.85 s/text
#
# A bigger batch buys **nothing** on this card — the GPU is already saturated by one
# sequence of this length, so 4 and 16 encode at the same rate — and the only thing it
# changes is how close the peak sits to the ceiling. Hence 4: ~1.2 GiB of headroom for
# the same speed. On a larger card raise both.
#
# The cap truncates 7.6% of records, and only their tail: `_combined_text` orders the
# columns so that what is lost is the end of `career_path_next` — a list of *other*
# occupations' titles — and never the title, description or duties. Lowering it to
# 1024 would cut into `work_context` on the median record.
EMB_BATCH_SIZE = int(os.getenv("EMB_BATCH_SIZE", "4"))
EMB_MAX_SEQ_LEN = int(os.getenv("EMB_MAX_SEQ_LEN", "2048"))

LLM_MODEL = os.getenv("LLM_MODEL", "gpt-4o-mini")
LLM_BASE_URL = os.getenv("OPENAI_BASE_URL")
LLM_API_KEY = os.getenv("OPENAI_API_KEY")
OCCUPATIONS_PATH = os.getenv("OCCUPATIONS_PATH")
LLM_MAX_RETRIES, LLM_BASE_DELAY = 3, 2.0

W_FULL, W_TITLE = 0.6, 0.4      # dense hybrid weights (sum = 1)
RRF_K = 60
MAX_CANDIDATES = 15
SCAN_DEPTH = 5                  # depth searched for a distinct secondary job

# Recalibrated 2026-08-31 for the retranslated corpus, over 50 out-of-domain probes and
# 150 questions built from the corpus's own titles:
#
#   out-of-domain : max 0.573, p95 0.538, median 0.439   (sparse max 0.272)
#   legitimate    : min 0.599, p5  0.652, median 0.709
#
# The two ranges no longer overlap, which the old corpus's «correct ≈ 0.58–0.66, OOD
# ceiling ≈ 0.36» did not promise — but **the gap is a mirage and 0.54 is a trap**. The
# 150 questions are «وظایف <exact title> چیست؟», the easiest shape there is; a real
# question phrased in the user's own words lands far lower. «مهندسی راهسازی» measures
# 0.520 against «مهندسان حمل و نقل», which is the right answer, so any gate above 0.52
# reintroduces the out-of-domain refusal this was raised to fix. 0.50 buys 3 fewer
# wrong acceptances out of 50 at no cost to either set and keeps 0.02 of headroom over
# that real low-water mark; 0.51 would leave 0.01.
#
# Several of those "wrong acceptances" are also not wrong. A corpus of 1118 occupations
# covers most of human activity, so «چگونه عکاسی یاد بگیرم؟» reaching «عکاسان» (0.573)
# is the system working. Read the count as an upper bound on the failures, not as one.
#
# Changing EMBED_MODEL_NAME invalidates every threshold below.
THRESHOLD_MATCH  = 0.50         # dense out-of-domain gate
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

# How many items of a list column the client shows before the reader asks for the rest.
# A record is 121 items at the median and 532 at the largest — a page of chips nobody
# reads — and five per column brings that to ~40. Nothing is hidden from the *payload*:
# every item is still sent and `preview` only says where the client's own toggle cuts,
# so opening a column costs no request and the PDF report still prints all of it.
PREVIEW_ITEMS = 5

# The reply to the item-selection call is at most three columns of five integers, so
# this is generous. It is not a quality knob: a truncated reply simply fails to parse
# and the column keeps the order the dataset stored.
SELECT_MAX_TOKENS = 300

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
# Dense-leader override, applied *before* the title tiebreak below. `_retrieve` fuses
# the dense and sparse rankings with RRF, and RRF compares **ranks, not margins**: a
# record BM25 puts first can lead the fused order while carrying a clearly lower dense
# score than the runner-up. The 77 O*NET residual categories are what make that common
# here — «مهندسان، سایر» describes nothing (a 62-character «all engineers not listed
# separately») but stuffs ten «مهندس …» aliases and twenty «مهندسان …» career links
# into its text, so BM25 ranks it first for any «مهندس X» question. It led
# «وظایف مهندس راهسازی» at dense 0.545 over «مهندسان عمران» at 0.592.
#
# Measured over 150 questions built from the corpus's own titles: 144/150 correct with
# no override, **149/150 at this margin**, 147/150 at 0.05 — five leaders changed and
# every one of them changed to the right record. Out-of-domain behaviour is untouched
# (the same 1 of 8 probes leaks either way). It runs before `prefer_title_match` so the
# title tiebreak still has the last word — that one exists precisely to promote a
# *lower*-dense candidate, and would be undone by a dense override running after it.
DENSE_LEAD_MARGIN = 0.03        # dense advantage that lets a runner-up take the lead
DENSE_LEAD_DEPTH  = 5           # how deep in the fused order to look for one

TITLE_TIEBREAK_MARGIN = 0.05    # dense gap within which titles may break the tie
TITLE_TIEBREAK_DEPTH  = 4       # how deep to look for a better-titled candidate
TITLE_TOKEN_MIN       = 4       # below this a token is an affix or stopword, not content

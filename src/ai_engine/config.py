import os

EMBED_MODEL_NAME = os.getenv("EMBED_MODEL_NAME", "BAAI/bge-m3")
EMB_CACHE_DIR = os.getenv("EMB_CACHE_DIR", "emb_cache")

EMB_BATCH_SIZE = int(os.getenv("EMB_BATCH_SIZE", "4"))
EMB_MAX_SEQ_LEN = int(os.getenv("EMB_MAX_SEQ_LEN", "2048"))

LLM_MODEL = os.getenv("LLM_MODEL", "gpt-5.6-luna")
LLM_BASE_URL = os.getenv("OPENAI_BASE_URL")
LLM_API_KEY = os.getenv("OPENAI_API_KEY")
OCCUPATIONS_PATH = os.getenv("OCCUPATIONS_PATH")
LLM_MAX_RETRIES, LLM_BASE_DELAY = 3, 2.0

W_FULL, W_TITLE = 0.6, 0.4
RRF_K = 60
MAX_CANDIDATES = 15
SCAN_DEPTH = 5

THRESHOLD_MATCH  = 0.50
THRESHOLD_SPARSE = 0.30
SECONDARY_MIN    = 0.50
SECONDARY_MARGIN = 0.01
PAIR_SIM_MAX     = 0.85
# How far below a half's own best record the whole-question pair may score and still count
# as covering that half (engine._combination_pair). Calibrated on six combination questions:
# halves the pair really covered sat 0.000-0.044 below the best, halves it had missed
# 0.073-0.166 below, and the one in between — 0.066, a biomedical engineer standing for
# «پزشکی» — reads as covered.
PAIR_COVER_MARGIN = 0.07

DISCOVERY_FLOOR      = 0.35
DISCOVERY_CANDIDATES = 1
DISCOVERY_RELATED    = 1
DISCOVERY_MATCH      = 0.60

# The bar a *typed job name* answers from its leader on when the model cannot be read
# (an outage, or use_llm=False). Looser than THRESHOLD_SPARSE on purpose: this route
# used to gate every bare name on it, and it survives as the offline rule only.
NAMED_JOB_SPARSE     = 0.40


PREVIEW_ITEMS = 5

SELECT_MAX_TOKENS = 300
# The composed record is a ten-column merge of the retrieved records and the request, so
# it is several times the size of the "match" reply the same call makes on the other
# branch; truncated JSON parses as nothing and costs the whole generation.
RESOLVE_MAX_TOKENS = 1500
# The upper bound of each list column in SYSTEM_JOB_RESOLVE rule 5, enforced on the draft
# rather than trusted to the prompt: the model does run over — ten responsibilities against
# a stated nine — and once approved, a record holding more items can only score higher in
# profile.coverage. Rule 8 orders every column most-important-first, so the cap drops the tail.
DRAFT_MAX_ITEMS = {"aliases": 4, "tools": 8, "skills": 8, "knowledge": 7, "abilities": 7,
                   "responsibilities": 9, "work_context": 7, "career_path_next": 4}
ADAPTED_MAX_TOKENS = 700

PROFILE_TOP_N        = 5
PROFILE_W_DENSE      = 0.5
PROFILE_W_COVER      = 0.5
PROFILE_DENSE_ONLY   = 0.62
# A cosine over bge-m3 never reaches either end: measured over three profiles against all 1120
# records it ran 0.47 to 0.74, so half of a raw dense score is a constant the reader sees as
# «میزان تطابق ۳۴٪» on a match that is exactly right. These two map that band onto 0..1 — both for
# what is shown and for what is ranked, since a channel compressed into a tenth of its partner's
# range cannot weigh half of anything. `PROFILE_DENSE_ONLY` stays on the raw score: it is calibrated
# against fantasy profiles (0.53) and has nothing to do with presentation.
PROFILE_DENSE_FLOOR  = 0.45
PROFILE_DENSE_CEIL   = 0.75
# The other half of the refusal gate. A dense score alone cannot tell a profile of nothing real
# from a thin one: «سفر در زمان / تسخیر سیارات / زبان موجودات فضایی …» measures 0.65, above
# PROFILE_DENSE_ONLY, where the six natural probes run 0.66–0.74 — and its coverage read 100%,
# because the one generic word that matched («زمان», in a record's work context) was the only item
# left in a ratio the four unknown ones are kept out of. What does separate them is how much of the
# profile the corpus has any word for: 0.86–1.00 for those six, 0.20 for that one. Below this share
# the coverage channel is measuring a single accident, so nothing is ranked whatever dense says and
# PROFILE_NONE asks for the suggested wording instead.
PROFILE_KNOWN_MIN    = 0.5
# An item is worth log(records / records holding it), so one every record holds is worth nothing.
# The cap is reached at about 20 records in 1120: below that the item is simply "specific", and
# without a cap a single item matching one record would decide the whole ranking by itself.
PROFILE_IDF_CAP      = 4.0
# How much of each column of a matched record the analysis prompt is shown, so it can say what the
# job holds instead of only which of the person's items it did not hold, and how many of the ranked
# jobs carry that. The prompt writes about the first and the second; sending all five cost 6k
# characters of context for nothing.
PROFILE_CONTEXT_ITEMS = 6
PROFILE_RECORD_MATCHES = 3
# The analysis answer runs on a thinking model, and the thinking comes out of the same budget:
# measured on gpt-5.6-luna, the richer context spent all 700 tokens on reasoning_tokens and returned
# content=None, which `LLMClient` hands back as "" and every caller reads as an outage — the answer
# silently became the plain template. The tightened prompt measures 516 reasoning + 315 content;
# this leaves room for twice that, and an unspent cap costs nothing.
PROFILE_MAX_TOKENS = 2000
# What an item found outside the column it was typed in is worth against one found inside it. A
# profile written in taxonomy wording is covered by its own record and by every record whose duties
# or description happen to use the word, and undiscounted the two rank alike.
PROFILE_ELSEWHERE_WEIGHT = 0.6
# Under this the profile is nothing but items the whole corpus holds, and the weighted ratio would
# be a division of noise by noise; the counted one stands in for it.
PROFILE_WEIGHT_MIN   = 0.05
# A profile word of PROFILE_TOKEN_MIN letters or more matches by prefix either way; a shorter one
# («حل», «دقت») only exactly, and is required only when its item has no longer word. Below
# PROFILE_SHORT_MIN a word is dropped. Until 2026-09-16 short words were dropped outright, so
# «دقت» matched nothing and «دقت کنترل» matched every record holding «کنترل».
PROFILE_TOKEN_MIN    = 4
PROFILE_SHORT_MIN    = 2

DENSE_LEAD_MARGIN = 0.03
DENSE_LEAD_DEPTH  = 5

TITLE_TIEBREAK_MARGIN = 0.05
TITLE_TIEBREAK_DEPTH  = 4
TITLE_TOKEN_MIN       = 4

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
PROFILE_MIN_ITEMS    = 2
PROFILE_MIN_FIELDS   = 2
PROFILE_TOKEN_MIN    = 4

DENSE_LEAD_MARGIN = 0.03
DENSE_LEAD_DEPTH  = 5

TITLE_TIEBREAK_MARGIN = 0.05
TITLE_TIEBREAK_DEPTH  = 4
TITLE_TOKEN_MIN       = 4

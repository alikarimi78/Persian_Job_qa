import logging
import re
import threading
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import pandas as pd
from sentence_transformers import SentenceTransformer

from . import profile as profile_match
from .bm25 import BM25
from .columns import (DISCOVERY_FIELDS, DISCOVERY_PRIMARY, EXPECTED_COLUMNS,
                      FIELD_LABELS, ORGANIZATION_COLUMN, PROSE_COLUMNS,
                      PUBLIC_ORGANIZATION, RANKED_FIELDS,
                      VOCABULARY_FIELDS)
from .config import (ADAPTED_MAX_TOKENS, DISCOVERY_CANDIDATES, DISCOVERY_FLOOR, DISCOVERY_MATCH,
                     DISCOVERY_RELATED, DRAFT_MAX_ITEMS, EMB_BATCH_SIZE, EMB_MAX_SEQ_LEN,
                     EMBED_MODEL_NAME, MAX_CANDIDATES, NAMED_JOB_SPARSE, PAIR_COVER_MARGIN,
                     PAIR_SIM_MAX, PREVIEW_ITEMS, PROFILE_DENSE_CEIL, PROFILE_DENSE_FLOOR,
                     PROFILE_DENSE_ONLY, PROFILE_KNOWN_MIN, PROFILE_MAX_TOKENS, PROFILE_TOP_N,
                     PROFILE_W_COVER, PROFILE_W_DENSE, RESOLVE_MAX_TOKENS, RRF_K, SCAN_DEPTH,
                     SECONDARY_MARGIN, SECONDARY_MIN, SELECT_MAX_TOKENS, THRESHOLD_MATCH,
                     THRESHOLD_SPARSE, W_FULL, W_TITLE)
from .emb_store import store
from .intents import (EXPLICIT_COMBO_WORDS, INTENT_TO_FIELDS, detect_intent,
                      is_about_system, is_bare_name, is_greeting, is_job_request)
from .llm import LLMClient
from .messages import (ABOUT_MESSAGE, DISCOVERY_NOT_REAL, DISCOVERY_UNAVAILABLE,
                       DISCOVERY_VAGUE, GREETING_MESSAGE, OOD_MESSAGE, PROFILE_NONE)
from .prompts import (SYSTEM_ADAPTED, SYSTEM_INTERDISCIPLINARY, SYSTEM_ITEM_SELECT,
                      SYSTEM_JOB_MATCH, SYSTEM_JOB_RESOLVE, SYSTEM_PROFILE_ANALYZE,
                      SYSTEM_SINGLE)
from .ranking import prefer_contained_title, prefer_dense_leader, prefer_title_match
from .render import (build_context, field_items, job_detail, preview_count, profile_context,
                     render_draft, template_one, template_profile, template_two)
from .text import normalize_text, parse_json_object

try:
    import torch
    _HAS_CUDA = torch.cuda.is_available()
    _OOM = tuple({getattr(torch, "OutOfMemoryError", None),
                  getattr(torch.cuda, "OutOfMemoryError", None)} - {None}) or (RuntimeError,)
except Exception:
    _HAS_CUDA = False
    _OOM = ()

NOT_A_JOB = object()
TOO_VAGUE = object()

log = logging.getLogger("job_qa_service")


# An organization whose accounts can reach no record at all — every row private to
# somebody else. Nothing to retrieve, so nothing to answer from.
def _nothing_in_reach(intent):
    return {"mode": "out_of_domain", "intent": intent, "answer": OOD_MESSAGE}


# A cosine that never leaves the middle of its range, read onto 0..1 — see PROFILE_DENSE_FLOOR.
def _scaled(score):
    span = PROFILE_DENSE_CEIL - PROFILE_DENSE_FLOOR
    return min(1.0, max(0.0, (score - PROFILE_DENSE_FLOOR) / span))


def _reorder(items, picks):
    lead, seen = [], set()
    for pick in picks if isinstance(picks, list) else []:
        if len(lead) == PREVIEW_ITEMS:
            break
        try:
            index = int(pick)
        except (TypeError, ValueError):
            continue
        if 0 <= index < len(items) and index not in seen:
            seen.add(index)
            lead.append(index)
    return ([items[i] for i in lead]
            + [item for n, item in enumerate(items) if n not in seen])


_MODEL_CACHE = {}
_MODEL_LOCK = threading.Lock()


def shared_model():
    device = "cuda" if _HAS_CUDA else "cpu"
    key = (EMBED_MODEL_NAME, device)
    with _MODEL_LOCK:
        if key not in _MODEL_CACHE:
            model = SentenceTransformer(EMBED_MODEL_NAME, device=device)
            if EMB_MAX_SEQ_LEN:
                model.max_seq_length = min(model.max_seq_length, EMB_MAX_SEQ_LEN)
            _MODEL_CACHE[key] = model
        return _MODEL_CACHE[key]


class JobQAEngine:
    def __init__(self, data, rebuild_embeddings=False):
        self.df = self._load_data(data)
        self.titles = self.df["job_title"].tolist()
        # A title can now be held twice — once by the public corpus and once by an
        # organization that keeps its own version — so the index maps to every record
        # with that title and the reader takes the first one in its own reach.
        self.title_index = defaultdict(list)
        for i, title in enumerate(self.titles):
            self.title_index[normalize_text(title)].append(i)
        self.org_ids = self.df[ORGANIZATION_COLUMN].to_numpy()

        self.model = shared_model()
        self.emb_full, self.emb_title = self._load_or_build_embeddings(rebuild_embeddings)
        self.bm25 = BM25(self.df["combined_text"].tolist())
        self.profile_tokens = [profile_match.record_tokens(row)
                               for _, row in self.df.iterrows()]
        self.llm = LLMClient()

    @staticmethod
    def _combined_text(row):
        parts = [
            f"{FIELD_LABELS['job_title']}: {row['job_title']}",
            f"{FIELD_LABELS['aliases']}: {row['aliases'].replace('|', '،')}",
            f"{FIELD_LABELS['description']}: {row['description']}",
            f"{FIELD_LABELS['responsibilities']}: {row['responsibilities']}",
            f"{FIELD_LABELS['skills']}: {row['skills']}",
            f"{FIELD_LABELS['knowledge']}: {row['knowledge']}",
            f"{FIELD_LABELS['abilities']}: {row['abilities']}",
            f"{FIELD_LABELS['tools']}: {row['tools']}",
            f"{FIELD_LABELS['work_context']}: {row['work_context']}",
            f"{FIELD_LABELS['career_path_next']}: {row['career_path_next']}",
        ]
        return " . ".join(p for p in parts if p.split(": ", 1)[-1].strip())

    @staticmethod
    def _title_alias_text(row):
        return f"{row['job_title']} ، {row['aliases'].replace('|', '،')}".strip(" ،")

    def _load_data(self, data):
        df = data.copy() if isinstance(data, pd.DataFrame) else pd.read_excel(data)
        df.columns = [str(c).strip().lower() for c in df.columns]
        for col in EXPECTED_COLUMNS:
            if col not in df.columns:
                df[col] = ""
            df[col] = df[col].map(normalize_text)
        df = df[df["job_title"].str.len() > 0].reset_index(drop=True)
        # Nothing outside the database carries the column — the xlsx, the REPL and
        # `eval_engine` all build a wholly public corpus. A Series either way: the bare
        # sentinel made `pd.to_numeric` return a scalar, which has no `.fillna`, and every
        # corpus built from anything but the database died there.
        column = (df[ORGANIZATION_COLUMN] if ORGANIZATION_COLUMN in df.columns
                  else pd.Series(PUBLIC_ORGANIZATION, index=df.index))
        df[ORGANIZATION_COLUMN] = (pd.to_numeric(column, errors="coerce")
                                   .fillna(PUBLIC_ORGANIZATION).astype(int))
        df["combined_text"] = df.apply(self._combined_text, axis=1)
        return df

    def _encode(self, texts, prefix):
        if "e5" in EMBED_MODEL_NAME.lower():
            texts = [f"{prefix}: {t}" for t in texts]
        return self._encode_bounded(texts)

    def _encode_bounded(self, texts):
        batch = max(1, EMB_BATCH_SIZE)
        while True:
            try:
                return self.model.encode(texts, batch_size=batch,
                                         normalize_embeddings=True, show_progress_bar=False)
            except _OOM:
                if not _HAS_CUDA:
                    raise
                torch.cuda.empty_cache()
                if batch > 1:
                    batch //= 2
                    log.warning(f"CUDA OOM while encoding; retrying at batch_size={batch}.")
                    continue
                log.warning("CUDA OOM at batch_size=1; moving the encoder to the CPU. "
                            "Lower EMB_MAX_SEQ_LEN or run on a larger card.")
                self.model.to("cpu")
                return self.model.encode(texts, batch_size=batch,
                                         normalize_embeddings=True, show_progress_bar=False)

    def _load_or_build_embeddings(self, rebuild):
        full_texts = self.df["combined_text"].tolist()
        title_texts = self.df.apply(self._title_alias_text, axis=1).tolist()

        def encode(texts):
            return self._encode(texts, "passage")

        if not rebuild:
            store.adopt_corpus_cache(full_texts, title_texts)
        emb_full = store.embed(full_texts, encode, force=rebuild)
        emb_title = store.embed(title_texts, encode, force=rebuild)
        store.save()
        return emb_full, emb_title

    # Which records this caller may reach: None is every one of them, and the scope's
    # own None is the public corpus. One boolean array over the whole corpus, not a
    # second engine — the embeddings, the encoder and the BM25 statistics are shared,
    # and an organization's records are simply the rows a mask keeps.
    # Who owns a stored record, as the API spells it: an organization id, or None for the
    # public corpus rather than the column's sentinel.
    def _owner(self, i):
        owner = int(self.org_ids[i])
        return None if owner == PUBLIC_ORGANIZATION else owner

    def _mask(self, scope):
        if scope is None:
            return None
        ids = [PUBLIC_ORGANIZATION if o is None else int(o) for o in scope]
        return np.isin(self.org_ids, ids)

    def _retrieve(self, q_norm, mask=None):
        q_emb = self._encode([q_norm], "query")[0]
        dense = W_FULL * (self.emb_full @ q_emb) + W_TITLE * (self.emb_title @ q_emb)
        sparse = self.bm25.score(q_norm)

        # The candidates come out of the reachable rows rather than being filtered after
        # the fact: a top-k taken over the whole corpus and masked afterwards would hand
        # RRF fewer and fewer candidates the narrower the scope.
        pool = np.arange(len(dense)) if mask is None else np.flatnonzero(mask)
        k = min(MAX_CANDIDATES, len(pool))
        rrf = defaultdict(float)
        for scores in (dense, sparse):
            for rank, idx in enumerate(pool[np.argsort(scores[pool])[::-1][:k]]):
                rrf[int(idx)] += 1.0 / (RRF_K + rank + 1)
        order = [i for i, _ in sorted(rrf.items(), key=lambda x: x[1], reverse=True)]
        return order, dense, sparse

    def _resolve_job(self, question, candidate_idxs, mask=None):
        records = "\n\n".join(
            f"رکورد {n}:\n{build_context(self.df.iloc[i], DISCOVERY_FIELDS)}"
            for n, i in enumerate(candidate_idxs))

        raw = self.llm([
            {"role": "system", "content": SYSTEM_JOB_RESOLVE},
            {"role": "user", "content":
                f"درخواست کاربر:\n{question}\n\n"
                f"رکوردهای نزدیک موجود در پایگاه داده:\n{records}"},
        ], temperature=0.3, max_tokens=RESOLVE_MAX_TOKENS, clean=False, json_object=True)

        obj = parse_json_object(raw)
        if obj is None:
            return None
        decision = str(obj.get("decision", "")).strip().lower()
        if decision == "not_a_job" or str(obj.get("not_a_job", "")).strip().lower() == "true":
            return NOT_A_JOB
        if decision == "too_vague":
            return TOO_VAGUE
        if decision == "match":
            try:
                pick = int(obj.get("match_index"))
            except (TypeError, ValueError):
                return None
            return candidate_idxs[pick] if 0 <= pick < len(candidate_idxs) else None

        draft = {c: normalize_text(obj.get(c, "")) for c in EXPECTED_COLUMNS}
        for col in PROSE_COLUMNS:
            draft[col] = re.sub(r"\s*\|\s*", "، ", draft[col]).strip("، ")
        for col, cap in DRAFT_MAX_ITEMS.items():
            draft[col] = " | ".join([i.strip() for i in draft[col].split("|") if i.strip()][:cap])
        if not draft["job_title"]:
            return None
        held = self._held_title(draft["job_title"], mask)
        return draft if held is None else held

    # The corpus already holding the composed title is only a duplicate if this caller
    # could have been shown it; another organization's record of the same name is not
    # theirs to be answered from, so for them the draft stays a draft.
    def _held_title(self, title, mask):
        for i in self.title_index.get(title, ()):
            if mask is None or mask[i]:
                return i
        return None

    def _related_titles(self, order, answered=None):
        return [self.df.iloc[i]["job_title"] for i in order
                if i != answered][:DISCOVERY_RELATED]

    def _nearest_detail(self, order, fields, answered=None):
        for i in order:
            if i != answered:
                return job_detail(self.df.iloc[i], fields)
        return None

    # A question that combines two fields names both, so each half is retrieved on its own.
    # Retrieving only the whole question let the field that dominated it fill both slots:
    # «مهندس کامپیوتر و پزشک» came back as two computer records and an answer admitting the
    # medical half was missing. But the whole-question pair is sometimes the better one —
    # «معلم و روان‌شناس» finds «روان‌شناسان مدارس», the intersection itself, which neither
    # half finds alone — so it is kept for every half it already covers, to within
    # PAIR_COVER_MARGIN of that half's own best record, and only a missed half is replaced.
    # None when the question does not split into two halves leading to two different,
    # unrelated records: «… مخلوط‌کن و ترکیب مواد» splits, but both halves find the same job.
    def _combination_pair(self, q_norm, i1, i2, mask=None):
        tokens = q_norm.split()
        at = next((n for n, tok in enumerate(tokens[1:-1], 1) if tok in ("و", "با")), None)
        if at is None:
            return None
        halves = []
        for half in (" ".join(tokens[:at]), " ".join(tokens[at + 1:])):
            order, dense, _ = self._retrieve(half, mask)
            if not order:
                return None
            lead = prefer_dense_leader(order, dense)[0]
            if float(dense[lead]) < THRESHOLD_MATCH - 0.05:
                return None
            halves.append((lead, dense))
        (ia, da), (ib, db) = halves
        if not self._unrelated(ia, ib):
            return None
        if i2 is not None:
            ka = max((i1, i2), key=lambda i: float(da[i]))
            kb = max((i1, i2), key=lambda i: float(db[i]))
            a_held = float(da[ka]) >= float(da[ia]) - PAIR_COVER_MARGIN
            b_held = float(db[kb]) >= float(db[ib]) - PAIR_COVER_MARGIN
            if a_held and b_held:
                ia, ib = (ka, kb) if ka != kb else (ka, i2 if ka == i1 else i1)
            elif a_held and self._unrelated(ka, ib):
                ia = ka
            elif b_held and self._unrelated(ia, kb):
                ib = kb
        return ia, ib

    def _unrelated(self, a, b):
        return a != b and float(self.emb_full[a] @ self.emb_full[b]) < PAIR_SIM_MAX

    # One card for two records: the prose combines them, and each keeps its own boxes. An
    # explicit combination also composes the combined job — beside the prose, not after it —
    # so it is offered for filing in the shape every composed job is: `job_draft` with its
    # `draft_detail`, or a `draft_reason` saying why there is none (`exists`, naming the stored
    # job in `draft_job`; `not_a_job`; `too_vague`; `unavailable`). The tie fallback composes
    # nothing, having no reading to compose with.
    def _combined(self, question, intent, fields, pair, use_llm, mask=None, compose=False):
        ia, ib = pair
        row1, row2 = self.df.iloc[ia], self.df.iloc[ib]
        with ThreadPoolExecutor(max_workers=1) as pool:
            composing = (pool.submit(self._resolve_job, question, [ia, ib], mask)
                         if compose and use_llm else None)
            ans, (picks1, picks2) = self._answer_and_select([
                {"role": "system", "content": SYSTEM_INTERDISCIPLINARY},
                {"role": "user", "content":
                    f"شغل اول:\n{build_context(row1, fields)}\n\n"
                    f"شغل دوم:\n{build_context(row2, fields)}\n\nسوال کاربر: {question}"},
            ], question, [row1, row2], use_llm)
            resolved = composing.result() if composing else None
        if not ans:
            ans = template_two(row1, row2, fields)
        out = {"mode": "interdisciplinary", "intent": intent,
               "jobs": [row1["job_title"], row2["job_title"]],
               "answer": ans,
               "details": [job_detail(row1, fields, picks1),
                           job_detail(row2, fields, picks2)]}
        if composing is None:
            return out
        if isinstance(resolved, dict):
            return out | {"job_draft": resolved, "draft_detail": job_detail(resolved, fields)}
        if isinstance(resolved, int):
            return out | {"draft_reason": "exists",
                          "draft_job": self.df.iloc[resolved]["job_title"]}
        return out | {"draft_reason": "not_a_job" if resolved is NOT_A_JOB
                      else "too_vague" if resolved is TOO_VAGUE else "unavailable"}

    def _discover(self, question, q_norm, use_llm=True, retrieved=None,
                  offline_match=None, mask=None):
        if retrieved is None:
            retrieved = self._retrieve(q_norm, mask)
        order, dense, sparse = retrieved
        if not order:
            return _nothing_in_reach("job_request")
        i1 = order[0]
        s1_dense, s1_sparse = float(dense[i1]), float(sparse[i1])
        related = self._related_titles(order)
        refusal = {"mode": "out_of_domain", "intent": "job_request",
                   "related_jobs": related,
                   "nearest": self._nearest_detail(order, DISCOVERY_PRIMARY)}

        if s1_dense < DISCOVERY_FLOOR and s1_sparse < THRESHOLD_SPARSE:
            return {"mode": "out_of_domain", "intent": "job_request",
                    "answer": OOD_MESSAGE}

        resolved = (self._resolve_job(question, order[:DISCOVERY_CANDIDATES], mask)
                    if use_llm else None)

        if resolved is NOT_A_JOB:
            return refusal | {"answer": DISCOVERY_NOT_REAL}
        if resolved is TOO_VAGUE:
            return refusal | {"mode": "needs_detail", "answer": DISCOVERY_VAGUE}
        offline_match = offline_match or (lambda dense_, sparse_: dense_ >= DISCOVERY_MATCH)
        if resolved is None and offline_match(s1_dense, s1_sparse):
            resolved = i1
        if resolved is None:
            return refusal | {"answer": DISCOVERY_UNAVAILABLE}

        if isinstance(resolved, int):
            row = self.df.iloc[resolved]
            ans, (picks,) = self._answer_and_select([
                {"role": "system", "content": SYSTEM_JOB_MATCH},
                {"role": "user", "content":
                    f"اطلاعات شغل:\n{build_context(row, DISCOVERY_FIELDS)}\n\n"
                    f"خواسته کاربر: {question}"},
            ], question, [row], use_llm)
            if not ans:
                ans = template_one(row, DISCOVERY_FIELDS)
            return {"mode": "job_match", "intent": "job_request",
                    "job": row["job_title"], "organization_id": self._owner(resolved),
                    "related_jobs": self._related_titles(order, resolved), "answer": ans,
                    "nearest": self._nearest_detail(order, DISCOVERY_PRIMARY, resolved),
                    "details": [job_detail(row, DISCOVERY_PRIMARY, picks)]}

        return {"mode": "job_generated", "intent": "job_request",
                "job": resolved["job_title"], "job_draft": resolved, "related_jobs": related,
                "nearest": self._nearest_detail(order, DISCOVERY_PRIMARY),
                "answer": render_draft(resolved),
                "details": [job_detail(resolved, DISCOVERY_PRIMARY)]}

    def _adapted_answer(self, question, record, use_llm):
        if not use_llm:
            return ""
        return self.llm([
            {"role": "system", "content": SYSTEM_ADAPTED},
            {"role": "user", "content":
                f"رکورد تدوین‌شده:\n{build_context(record, DISCOVERY_FIELDS)}\n\n"
                f"سوال کاربر: {question}"},
        ], temperature=0.3, max_tokens=ADAPTED_MAX_TOKENS)

    def _select_items(self, question, row):
        columns = {}
        for field in RANKED_FIELDS:
            items = field_items(field, str(row.get(field, "") or "").strip())
            # Only a column that folds has a "which five" to choose; one shown whole is not sent.
            if preview_count(len(items)) < len(items):
                columns[field] = items
        if not columns:
            return {}

        listing = "\n\n".join(
            f"### {field}\n" + "\n".join(f"{n}. {item}" for n, item in enumerate(items))
            for field, items in columns.items())
        raw = self.llm([
            {"role": "system", "content": SYSTEM_ITEM_SELECT},
            {"role": "user", "content":
                f"پرسش کاربر: {question}\n\n"
                f"عنوان شغل: {row['job_title']}\n\n{listing}"},
        ], temperature=0, max_tokens=SELECT_MAX_TOKENS, clean=False, json_object=True)

        picked = parse_json_object(raw)
        if picked is None:
            return {}
        return {field: _reorder(items, picked.get(field))
                for field, items in columns.items()}

    def _answer_and_select(self, messages, question, rows, use_llm, **kwargs):
        if not use_llm:
            return "", [{}] * len(rows)
        with ThreadPoolExecutor(max_workers=1 + len(rows)) as pool:
            answer = pool.submit(self.llm, messages, **kwargs)
            picks = [pool.submit(self._select_items, question, row) for row in rows]
            return answer.result(), [pick.result() for pick in picks]

    def analyze(self, profile, use_llm=True, scope=None):
        mask = self._mask(scope)
        prof = profile_match.clean_profile(profile)
        if not prof:
            return {"mode": "out_of_domain", "intent": "profile",
                    "answer": PROFILE_NONE, "matches": []}

        q_norm = normalize_text(profile_match.profile_query_text(prof))
        q_emb = self._encode([q_norm], "query")[0]
        dense = self.emb_full @ q_emb

        pool = [int(i) for i in (range(len(self.df)) if mask is None else np.flatnonzero(mask))]
        if not pool:
            return {"mode": "out_of_domain", "intent": "profile",
                    "answer": PROFILE_NONE, "matches": []}

        # One pass over everything in reach, not a shortlist: a record holding every item the
        # person typed must not be lost because dense ranked it twentieth. The weights each item
        # is scored with come out of that same pass, so the ranking is done in `profile.rank` and
        # only the two channels are mixed here.
        prepared = profile_match.prepare(prof)
        measured = profile_match.rank(prepared, [self.profile_tokens[i] for i in pool])
        ranked = [(PROFILE_W_DENSE * _scaled(float(dense[idx])) + PROFILE_W_COVER * weighted,
                   float(dense[idx]), ratio, fields, idx)
                  for idx, (fields, ratio, weighted) in zip(pool, measured)]
        ranked.sort(key=lambda r: r[0], reverse=True)

        best = ranked[0]
        # Which items the corpus has a word for at all is a property of the corpus, not of one
        # record, so the leader's fields carry it for the whole ranking — and a profile it knows
        # too little of is refused before a lone accidental match can be read as full coverage.
        known = sum(len(f["matched"]) + len(f["missing"]) for f in best[3])
        typed = known + sum(len(f["unknown"]) for f in best[3])
        if known < PROFILE_KNOWN_MIN * typed or (best[2] <= 0 and best[1] < PROFILE_DENSE_ONLY):
            return {"mode": "out_of_domain", "intent": "profile", "answer": PROFILE_NONE,
                    "matches": []}

        primary = list(prof.keys())
        matches = []
        for _, _, ratio, fields, idx in ranked[:PROFILE_TOP_N]:
            row = self.df.iloc[idx]
            matches.append({"job_title": row["job_title"], "coverage": ratio, "fields": fields,
                            "detail": job_detail(row, primary)})

        ans = self.llm([
            {"role": "system", "content": SYSTEM_PROFILE_ANALYZE},
            {"role": "user", "content": profile_context(prof, matches)},
        ], temperature=0.3, max_tokens=PROFILE_MAX_TOKENS) if use_llm else ""
        if not ans:
            ans = template_profile(matches)

        return {"mode": "profile_match", "intent": "profile", "answer": ans,
                "job": matches[0]["job_title"], "matches": matches}

    # Each vocabulary field's phrases across the records this caller may see, most common first —
    # what advanced analysis offers while typing, so a person picks «سخن گفتن» rather than describing
    # the same skill in words no record holds, and the item then counts toward coverage.
    def vocabulary(self, scope=None):
        mask = self._mask(scope)
        rows = self.df if mask is None else self.df[mask]
        vocabulary = {}
        for field in VOCABULARY_FIELDS:
            counts = Counter()
            for value in rows[field]:
                counts.update(set(profile_match.record_items(field, value)))
            vocabulary[field] = [{"text": text, "count": count} for text, count
                                 in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))]
        return vocabulary

    def answer(self, question, use_llm=True, scope=None):
        q = normalize_text(question)
        mask = self._mask(scope)

        if is_job_request(q):
            return self._discover(question, q, use_llm, mask=mask)

        if is_about_system(q):
            return {"mode": "about", "intent": "about", "answer": ABOUT_MESSAGE}

        if is_greeting(q):
            return {"mode": "about", "intent": "greeting", "answer": GREETING_MESSAGE}

        intent = detect_intent(q)

        bare_name = is_bare_name(q)
        if bare_name:
            intent = "description"
        fields = INTENT_TO_FIELDS.get(intent, INTENT_TO_FIELDS["general"])

        order, dense, sparse = self._retrieve(q, mask)
        if not order:
            return _nothing_in_reach(intent)
        order = prefer_dense_leader(order, dense)
        order = prefer_title_match(q, order, dense, self.titles)
        order = prefer_contained_title(q, order, self.titles, mask)
        i1 = order[0]
        s1_dense, s1_sparse = float(dense[i1]), float(sparse[i1])

        # Every bare name goes to `_discover`, whether or not `names_an_occupation` can see
        # an occupation in it. That test misses 17% of the corpus's own aliases — «رمال»,
        # «فالگیر», «بقال», «مورخ» carry no agent-noun head and no agentive suffix it knows —
        # and a miss did not merely skip the composing path, it dropped the input onto the
        # question path's stricter gate, where «رمال» (dense 0.41) was refused outright with
        # no call made at all. `_discover` re-checks `DISCOVERY_FLOOR` itself, so nothing
        # below the floor costs a call either way, and above it the prompt's `not_a_job`
        # branch is the realism check — a better one than any suffix list.
        if bare_name:
            return self._discover(
                question, q, use_llm, (order, dense, sparse),
                offline_match=lambda dense_, sparse_: (dense_ >= THRESHOLD_MATCH
                                                       or sparse_ >= NAMED_JOB_SPARSE),
                mask=mask)

        if s1_dense < THRESHOLD_MATCH and s1_sparse < THRESHOLD_SPARSE:
            return {"mode": "out_of_domain", "intent": intent,
                    "answer": OOD_MESSAGE}

        i2 = next((c for c in order[1:SCAN_DEPTH + 1]
                   if float(self.emb_full[i1] @ self.emb_full[c]) < PAIR_SIM_MAX), None)
        s2_dense = float(dense[i2]) if i2 is not None else None

        # An explicit combination is answered from a record for each half, and only when the
        # question really does name two fields — otherwise the combining word is just a
        # word, as in «داروهای ترکیبی» or «خدمات مشترکین», and the question is a normal one.
        if any(k in q for k in EXPLICIT_COMBO_WORDS):
            pair = self._combination_pair(q, i1, i2, mask)
            if pair:
                return self._combined(question, intent, fields, pair, use_llm,
                                      mask=mask, compose=True)

        resolved = (self._resolve_job(question, order[:DISCOVERY_CANDIDATES], mask)
                    if use_llm else None)

        if resolved is NOT_A_JOB:
            return {"mode": "out_of_domain", "intent": intent,
                    "related_jobs": self._related_titles(order),
                    "nearest": self._nearest_detail(order, fields), "answer": OOD_MESSAGE}

        if resolved is TOO_VAGUE:
            return {"mode": "needs_detail", "intent": intent,
                    "related_jobs": self._related_titles(order),
                    "nearest": self._nearest_detail(order, fields),
                    "answer": DISCOVERY_VAGUE}

        if isinstance(resolved, dict):
            ans = self._adapted_answer(question, resolved, use_llm)
            if not ans:
                ans = template_one(resolved, fields)
            # The composed record rides along as `job_draft` too, so the client can offer it
            # for filing — the boxes answer the question, the draft is what gets submitted.
            return {"mode": "job_adapted", "intent": intent,
                    "job": resolved["job_title"], "answer": ans,
                    "related_jobs": self._related_titles(order),
                    "nearest": self._nearest_detail(order, fields),
                    "details": [job_detail(resolved, fields)], "job_draft": resolved}

        # Two records within SECONDARY_MARGIN of each other used to be answered as a
        # combination *before* the resolve step, which is where a job named in one breath
        # went: «وظایف پرستار اورژانس هوایی چیست؟» came back as «پرستاران» plus the paramedics,
        # the aviation half missing, while the same title typed bare composed a record that
        # covered both. One job named is one job, so the tie is now only the fallback for
        # when there is no reading to go on — an outage, or use_llm=False, whose routing and
        # therefore `eval_engine` it leaves exactly as it was.
        if (resolved is None and i2 is not None and s2_dense >= SECONDARY_MIN
                and abs(s1_dense - s2_dense) <= SECONDARY_MARGIN):
            return self._combined(question, intent, fields, (i1, i2), use_llm)

        if isinstance(resolved, int):
            i1, s1_dense = resolved, float(dense[resolved])
        row1 = self.df.iloc[i1]

        ans, (picks,) = self._answer_and_select([
            {"role": "system", "content": SYSTEM_SINGLE},
            {"role": "user", "content":
                f"اطلاعات شغل:\n{build_context(row1, fields)}\n\nسوال کاربر: {question}"},
        ], question, [row1], use_llm)
        if not ans:
            ans = template_one(row1, fields)
        return {"mode": "single", "intent": intent, "job": row1["job_title"],
                "answer": ans,
                "organization_id": self._owner(i1),
                "related_jobs": self._related_titles(order, i1),
                "nearest": self._nearest_detail(order, fields, i1),
                "details": [job_detail(row1, fields, picks)]}

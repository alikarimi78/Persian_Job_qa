import logging
import re
import threading
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import pandas as pd
from sentence_transformers import SentenceTransformer

from . import profile as profile_match
from .bm25 import BM25
from .columns import (DISCOVERY_FIELDS, DISCOVERY_PRIMARY, EXPECTED_COLUMNS,
                      FIELD_LABELS, PROSE_COLUMNS, RANKED_FIELDS)
from .config import (ADAPTED_MAX_TOKENS, DISCOVERY_CANDIDATES, DISCOVERY_FLOOR,
                     DISCOVERY_MATCH, DISCOVERY_RELATED, EMB_BATCH_SIZE, EMB_MAX_SEQ_LEN,
                     EMBED_MODEL_NAME, MAX_CANDIDATES, NAMED_JOB_SPARSE, PAIR_SIM_MAX,
                     PREVIEW_ITEMS, PROFILE_DENSE_ONLY, PROFILE_TOP_N, PROFILE_W_COVER,
                     PROFILE_W_DENSE, RESOLVE_MAX_TOKENS, RRF_K, SCAN_DEPTH,
                     SECONDARY_MARGIN, SECONDARY_MIN, SELECT_MAX_TOKENS, THRESHOLD_MATCH,
                     THRESHOLD_SPARSE, W_FULL, W_TITLE)
from .emb_store import store
from .intents import (EXPLICIT_COMBO_WORDS, INTENT_TO_FIELDS, detect_intent,
                      is_about_system, is_bare_name, is_greeting, is_job_request,
                      names_an_occupation)
from .llm import LLMClient
from .messages import (ABOUT_MESSAGE, DISCOVERY_NOT_REAL, DISCOVERY_UNAVAILABLE,
                       GREETING_MESSAGE, MATCH_HEADER, OOD_MESSAGE, PROFILE_NONE)
from .prompts import (SYSTEM_ADAPTED, SYSTEM_INTERDISCIPLINARY, SYSTEM_ITEM_SELECT,
                      SYSTEM_JOB_MATCH, SYSTEM_JOB_RESOLVE, SYSTEM_PROFILE_ANALYZE,
                      SYSTEM_SINGLE)
from .ranking import prefer_contained_title, prefer_dense_leader, prefer_title_match
from .render import (build_context, field_items, job_detail, profile_context,
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

log = logging.getLogger("job_qa_service")


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
        self.title_index = {normalize_text(t): i for i, t in enumerate(self.titles)}

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

    def _retrieve(self, q_norm):
        q_emb = self._encode([q_norm], "query")[0]
        dense = W_FULL * (self.emb_full @ q_emb) + W_TITLE * (self.emb_title @ q_emb)
        sparse = self.bm25.score(q_norm)

        k = min(MAX_CANDIDATES, len(dense))
        rrf = defaultdict(float)
        for rank, idx in enumerate(np.argsort(dense)[::-1][:k]):
            rrf[int(idx)] += 1.0 / (RRF_K + rank + 1)
        for rank, idx in enumerate(np.argsort(sparse)[::-1][:k]):
            rrf[int(idx)] += 1.0 / (RRF_K + rank + 1)
        order = [i for i, _ in sorted(rrf.items(), key=lambda x: x[1], reverse=True)]
        return order, dense, sparse

    # Which job the user means, decided by the LLM over the retrieved candidates: a
    # corpus row index when one of them *is* that job, a composed record when none is,
    # NOT_A_JOB when the request names no real occupation, None when the call failed.
    # The composed record is a *merge* — the candidates are handed over whole as the
    # material to build from, not as a writing sample to avoid repeating.
    def _resolve_job(self, question, candidate_idxs):
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
        if decision == "match":
            try:
                pick = int(obj.get("match_index"))
            except (TypeError, ValueError):
                return None
            return candidate_idxs[pick] if 0 <= pick < len(candidate_idxs) else None

        draft = {c: normalize_text(obj.get(c, "")) for c in EXPECTED_COLUMNS}
        for col in PROSE_COLUMNS:
            draft[col] = re.sub(r"\s*\|\s*", "، ", draft[col]).strip("، ")
        if not draft["job_title"]:
            return None
        return self.title_index.get(draft["job_title"], draft)

    # The nearest corpus records, so the user can see what the search actually held.
    # `answered` is dropped before the slice rather than after it, so the list is
    # DISCOVERY_RELATED long either way and no record is printed beside itself.
    def _related_titles(self, order, answered=None):
        return [self.df.iloc[i]["job_title"] for i in order
                if i != answered][:DISCOVERY_RELATED]

    # Answers a job request, composing the record when the corpus does not hold it.
    # `offline_match(dense, sparse)` is the rule applied when there is no reading to
    # branch on — an outage, or use_llm=False. A *described* request needs the strict
    # DISCOVERY_MATCH bar, since a spec answered from a 0.52 record is a wrong answer;
    # a *typed name* gets the looser lexical rule the score gate here used to apply.
    def _discover(self, question, q_norm, use_llm=True, retrieved=None, offline_match=None):
        if retrieved is None:
            retrieved = self._retrieve(q_norm)
        order, dense, sparse = retrieved
        i1 = order[0]
        s1_dense, s1_sparse = float(dense[i1]), float(sparse[i1])
        related = self._related_titles(order)
        refusal = {"mode": "out_of_domain", "intent": "job_request",
                   "score": s1_dense, "related_jobs": related}

        if s1_dense < DISCOVERY_FLOOR and s1_sparse < THRESHOLD_SPARSE:
            return {"mode": "out_of_domain", "intent": "job_request",
                    "score": s1_dense, "answer": OOD_MESSAGE}

        resolved = (self._resolve_job(question, order[:DISCOVERY_CANDIDATES])
                    if use_llm else None)

        if resolved is NOT_A_JOB:
            return refusal | {"answer": DISCOVERY_NOT_REAL}
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
                ans = f"{MATCH_HEADER}\n\n{template_one(row, DISCOVERY_FIELDS)}"
            return {"mode": "job_match", "intent": "job_request",
                    "job": row["job_title"], "score": float(dense[resolved]),
                    "related_jobs": self._related_titles(order, resolved), "answer": ans,
                    "details": [job_detail(row, DISCOVERY_PRIMARY, picks)]}

        return {"mode": "job_generated", "intent": "job_request",
                "job": resolved["job_title"], "score": s1_dense,
                "job_draft": resolved, "related_jobs": related,
                "answer": render_draft(resolved),
                "details": [job_detail(resolved, DISCOVERY_PRIMARY)]}

    # The prose for a composed record, written from that record and not from the corpus
    # row it grew out of: one source for the whole card, so the sentences above the boxes
    # and the boxes themselves cannot disagree.
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
            if len(items) > PREVIEW_ITEMS:
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

    def analyze(self, profile, use_llm=True):
        prof = profile_match.clean_profile(profile)
        if not prof:
            return {"mode": "out_of_domain", "intent": "profile",
                    "answer": PROFILE_NONE, "matches": []}

        q_norm = normalize_text(profile_match.profile_query_text(prof))
        q_emb = self._encode([q_norm], "query")[0]
        dense = self.emb_full @ q_emb

        ranked = []
        for idx in range(len(self.df)):
            fields, ratio = profile_match.coverage(prof, self.profile_tokens[idx])
            ranked.append((PROFILE_W_DENSE * float(dense[idx]) + PROFILE_W_COVER * ratio,
                           float(dense[idx]), ratio, fields, idx))
        ranked.sort(key=lambda r: r[0], reverse=True)

        best = ranked[0]
        if best[2] <= 0 and best[1] < PROFILE_DENSE_ONLY:
            return {"mode": "out_of_domain", "intent": "profile", "score": best[1],
                    "answer": PROFILE_NONE, "matches": []}

        primary = list(prof.keys())
        matches = []
        for score, dense_score, ratio, fields, idx in ranked[:PROFILE_TOP_N]:
            row = self.df.iloc[idx]
            matches.append({"job_title": row["job_title"], "score": score,
                            "dense": dense_score, "coverage": ratio, "fields": fields,
                            "detail": job_detail(row, primary)})

        ans = self.llm([
            {"role": "system", "content": SYSTEM_PROFILE_ANALYZE},
            {"role": "user", "content": profile_context(prof, matches)},
        ], temperature=0.3, max_tokens=700) if use_llm else ""
        if not ans:
            ans = template_profile(matches)

        return {"mode": "profile_match", "intent": "profile", "answer": ans,
                "job": matches[0]["job_title"], "score": matches[0]["score"],
                "matches": matches}

    def answer(self, question, use_llm=True):
        q = normalize_text(question)

        if is_job_request(q):
            return self._discover(question, q, use_llm)

        if is_about_system(q):
            return {"mode": "about", "intent": "about", "answer": ABOUT_MESSAGE}

        if is_greeting(q):
            return {"mode": "about", "intent": "greeting", "answer": GREETING_MESSAGE}

        intent = detect_intent(q)

        bare_name = is_bare_name(q)
        if bare_name:
            intent = "description"
        fields = INTENT_TO_FIELDS.get(intent, INTENT_TO_FIELDS["general"])

        order, dense, sparse = self._retrieve(q)
        order = prefer_dense_leader(order, dense)
        order = prefer_title_match(q, order, dense, self.titles)
        order = prefer_contained_title(q, order, self.titles)
        i1 = order[0]
        s1_dense, s1_sparse = float(dense[i1]), float(sparse[i1])

        if bare_name and names_an_occupation(q):
            return self._discover(
                question, q, use_llm, (order, dense, sparse),
                offline_match=lambda dense_, sparse_: (dense_ >= THRESHOLD_MATCH
                                                       or sparse_ >= NAMED_JOB_SPARSE))

        if s1_dense < THRESHOLD_MATCH and s1_sparse < THRESHOLD_SPARSE:
            return {"mode": "out_of_domain", "intent": intent,
                    "score": s1_dense, "answer": OOD_MESSAGE}

        i2 = next((c for c in order[1:SCAN_DEPTH + 1]
                   if float(self.emb_full[i1] @ self.emb_full[c]) < PAIR_SIM_MAX), None)

        explicit = any(k in q for k in EXPLICIT_COMBO_WORDS)
        interdisciplinary, s2_dense = False, None
        if i2 is not None:
            s2_dense = float(dense[i2])
            interdisciplinary = (
                (not bare_name and s2_dense >= SECONDARY_MIN
                 and abs(s1_dense - s2_dense) <= SECONDARY_MARGIN)
                or (explicit and s2_dense >= THRESHOLD_MATCH - 0.05)
            )

        if interdisciplinary:
            row1 = self.df.iloc[i1]
            row2 = self.df.iloc[i2]
            ans, (picks1, picks2) = self._answer_and_select([
                {"role": "system", "content": SYSTEM_INTERDISCIPLINARY},
                {"role": "user", "content":
                    f"شغل اول:\n{build_context(row1, fields)}\n\n"
                    f"شغل دوم:\n{build_context(row2, fields)}\n\nسوال کاربر: {question}"},
            ], question, [row1, row2], use_llm)
            if not ans:
                ans = template_two(row1, row2, fields)
            return {"mode": "interdisciplinary", "intent": intent,
                    "jobs": [row1["job_title"], row2["job_title"]],
                    "scores": [s1_dense, s2_dense], "answer": ans,
                    "details": [job_detail(row1, fields, picks1),
                                job_detail(row2, fields, picks2)]}

        # A question is resolved the way a request is, and for the same reason: retrieval
        # ranks by topic, so its leader is the *nearest* job rather than the one asked
        # about, and the two are answered differently. Where a candidate is the job, the
        # stored record answers it; where none is, the record composed for the user's own
        # job does — its columns, not a neighbour's, are what the boxes then show.
        resolved = (self._resolve_job(question, order[:DISCOVERY_CANDIDATES])
                    if use_llm else None)

        if resolved is NOT_A_JOB:
            return {"mode": "out_of_domain", "intent": intent, "score": s1_dense,
                    "related_jobs": self._related_titles(order), "answer": OOD_MESSAGE}

        if isinstance(resolved, dict):
            ans = self._adapted_answer(question, resolved, use_llm)
            if not ans:
                ans = template_one(resolved, fields)
            # Every neighbour is kept: none of them is the answer, and they are how the
            # user checks that the corpus really does lack the job just described to them.
            return {"mode": "job_adapted", "intent": intent,
                    "job": resolved["job_title"], "score": s1_dense, "answer": ans,
                    "related_jobs": self._related_titles(order),
                    "details": [job_detail(resolved, fields)]}

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
                "score": s1_dense, "answer": ans,
                "related_jobs": self._related_titles(order, i1),
                "details": [job_detail(row1, fields, picks)]}

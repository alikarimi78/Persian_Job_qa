# -*- coding: utf-8 -*-
"""JobQAEngine: corpus loading, embeddings, retrieval, and the two answer paths."""

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
from .config import (DISCOVERY_FLOOR, DISCOVERY_MATCH, DISCOVERY_RELATED,
                     EMB_BATCH_SIZE, EMB_MAX_SEQ_LEN, EMBED_MODEL_NAME,
                     MAX_CANDIDATES, NAMED_JOB_SPARSE, PAIR_SIM_MAX, PREVIEW_ITEMS,
                     PROFILE_DENSE_ONLY, PROFILE_TOP_N, PROFILE_W_COVER, PROFILE_W_DENSE,
                     RRF_K, SCAN_DEPTH, SECONDARY_MARGIN, SECONDARY_MIN, SELECT_MAX_TOKENS,
                     THRESHOLD_MATCH, THRESHOLD_SPARSE, W_FULL, W_TITLE)
from .emb_store import store
from .intents import (EXPLICIT_COMBO_WORDS, INTENT_TO_FIELDS, detect_intent,
                      is_about_system, is_bare_name, is_greeting, is_job_request,
                      names_an_occupation)
from .llm import LLMClient
from .messages import (ABOUT_MESSAGE, DISCOVERY_NOT_REAL, DISCOVERY_UNAVAILABLE,
                       GREETING_MESSAGE, MATCH_HEADER, OOD_MESSAGE, PROFILE_NONE)
from .prompts import (SYSTEM_INTERDISCIPLINARY, SYSTEM_ITEM_SELECT, SYSTEM_JOB_GENERATE,
                      SYSTEM_JOB_MATCH, SYSTEM_PROFILE_ANALYZE, SYSTEM_SINGLE)
from .ranking import prefer_dense_leader, prefer_title_match
from .render import (build_context, field_items, job_detail, profile_context,
                     render_draft, template_one, template_profile, template_two)
from .text import normalize_text, parse_json_object

try:
    import torch
    _HAS_CUDA = torch.cuda.is_available()
    # torch.OutOfMemoryError is 2.5+; torch.cuda.OutOfMemoryError is the older name.
    _OOM = tuple({getattr(torch, "OutOfMemoryError", None),
                  getattr(torch.cuda, "OutOfMemoryError", None)} - {None}) or (RuntimeError,)
except Exception:
    _HAS_CUDA = False
    _OOM = ()

# Third outcome of generation, distinct from None: the model looked at the request
# and judged that no real occupation matches it. Retrieval cannot make that call --
# dense similarity is topical, so «تربیت اژدها» sits legitimately close to «مربیان
# حیوانات» (measured 0.466, above DISCOVERY_FLOOR) while a real but niche request
# like «عصاره‌گیری گیاهان دارویی» measures 0.515. The two ranges overlap, so no
# threshold separates them; "is this a real job" is world knowledge, not geometry.
NOT_A_JOB = object()

log = logging.getLogger("job_qa_service")


def _reorder(items, picks):
    """`items` with the ones `picks` names first and the rest behind them, in order.

    `picks` is whatever the model returned for one column, so every value is checked
    rather than trusted: an index outside the column, a repeat, a string, a null, a
    list that is not a list. What survives leads; everything else follows in the order
    the dataset stored. The result is therefore always a permutation of `items` and
    never shorter than it, which is the property `job_detail` re-checks by length and
    the reason a bad reply costs nothing but the order it would have improved.
    """
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
    """The encoder, loaded once per process and shared by every engine instance.

    A rebuild deliberately builds a *whole new engine* while the old one keeps serving
    (app/engine_manager.py), so an encoder per engine means two copies of bge-m3 alive
    at once — 2.2 GB each in fp32. On CPU that only wasted RAM; on a GPU it does not
    fit beside the old one on a 4 GB card and the rebuild dies with CUDA OOM. The
    weights are read-only and inference never mutates them, so one copy serves both,
    and a rebuild no longer pays to reload the model either."""
    device = "cuda" if _HAS_CUDA else "cpu"
    key = (EMBED_MODEL_NAME, device)
    with _MODEL_LOCK:
        if key not in _MODEL_CACHE:
            model = SentenceTransformer(EMBED_MODEL_NAME, device=device)
            # bge-m3 declares 8192; a record is 1406 tokens at the median and the cap
            # is what bounds the activation memory of a batch. See EMB_MAX_SEQ_LEN.
            if EMB_MAX_SEQ_LEN:
                model.max_seq_length = min(model.max_seq_length, EMB_MAX_SEQ_LEN)
            _MODEL_CACHE[key] = model
        return _MODEL_CACHE[key]


class JobQAEngine:
    def __init__(self, data, rebuild_embeddings=False):
        self.df = self._load_data(data)
        self.titles = self.df["job_title"].tolist()

        self.model = shared_model()
        self.emb_full, self.emb_title = self._load_or_build_embeddings(rebuild_embeddings)
        self.bm25 = BM25(self.df["combined_text"].tolist())
        # Advanced search compares item by item, so every record's items are split and
        # tokenized once here rather than 1116 times per request. Nothing is encoded:
        # this is the lexical half of that path (see profile.py).
        self.profile_tokens = [profile_match.record_tokens(row)
                               for _, row in self.df.iterrows()]
        self.llm = LLMClient()

    # ---------- data ----------
    @staticmethod
    def _combined_text(row):
        # Important fields first: encoder-side truncation drops the least critical tail
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

    # ---------- embeddings ----------
    def _encode(self, texts, prefix):
        if "e5" in EMBED_MODEL_NAME.lower():          # E5 models need query:/passage: prefixes
            texts = [f"{prefix}: {t}" for t in texts]
        return self._encode_bounded(texts)

    def _encode_bounded(self, texts):
        """Encode under an explicit batch size, halving it and finally falling back to
        the CPU rather than letting a CUDA OOM leave the process with no engine.

        The default batch is sized for the smallest card this runs on (see
        EMB_BATCH_SIZE), but a full re-encode is the one moment the sizing can still be
        wrong: sentence-transformers sorts by length, so the very first batch is the
        longest records in the corpus and an OOM here costs the whole startup — the
        engine simply never loads and /search answers 503. The retry is deliberately
        not silent: falling back to the CPU turns a ~25-minute re-encode into hours and
        takes every query with it, and the log is the only place that would show why."""
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
                # One text at a time still does not fit: the weights and a single
                # sequence are already more than the card has. The encoder is shared
                # process-wide, so this moves every engine and every query onto the CPU
                # — slow, and the alternative is no engine at all.
                log.warning("CUDA OOM at batch_size=1; moving the encoder to the CPU. "
                            "Lower EMB_MAX_SEQ_LEN or run on a larger card.")
                self.model.to("cpu")
                return self.model.encode(texts, batch_size=batch,
                                         normalize_embeddings=True, show_progress_bar=False)

    def _load_or_build_embeddings(self, rebuild):
        """Vectors for the whole corpus, encoding only the texts the store lacks.

        `rebuild` is the escape hatch, not the normal path: the store is keyed on text
        content, so an edited record misses the cache by itself and a rebuild after one
        approval costs the two texts of that one record. Forcing re-encodes everything
        and overwrites the store — for a corrupted store or a changed encoder, nothing
        that happens on every approval."""
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

    # ---------- retrieval ----------
    def _retrieve(self, q_norm):
        """Dense hybrid + BM25 rankings fused with Reciprocal Rank Fusion."""
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

    # ---------- job generation ----------
    def _generate_job(self, question, neighbour_idxs):
        """Designs a new occupation record from the user's spec. Returns a dict with
        the dataset's own columns (so it can be stored as a suggestion), NOT_A_JOB if
        the model judged the request to describe no real occupation, or None if the
        API is unavailable or its reply is unusable."""
        reference = "\n\n".join(
            f"نمونه {n + 1}:\n{build_context(self.df.iloc[i], DISCOVERY_FIELDS)}"
            for n, i in enumerate(neighbour_idxs))

        raw = self.llm([
            {"role": "system", "content": SYSTEM_JOB_GENERATE},
            {"role": "user", "content":
                f"درخواست کاربر:\n{question}\n\n"
                f"مشاغل مشابه موجود (فقط برای الگوی سبک نگارش و پرهیز از تکرار):\n{reference}"},
        ], temperature=0.5, max_tokens=900, clean=False)

        obj = parse_json_object(raw)
        if obj is None:
            return None
        # Compared explicitly rather than by truthiness: the string "false" is truthy
        flag = obj.get("not_a_job")
        if flag is True or str(flag).strip().lower() == "true":
            return NOT_A_JOB
        draft = {c: normalize_text(obj.get(c, "")) for c in EXPECTED_COLUMNS}
        for col in PROSE_COLUMNS:
            draft[col] = re.sub(r"\s*\|\s*", "، ", draft[col]).strip("، ")
        return draft if draft["job_title"] else None

    def _discover(self, question, q_norm, use_llm=True, retrieved=None):
        """Job-request path: return a close existing job, or design a new one.

        `retrieved` is the question path handing over the retrieval it has already
        paid for, when it decides mid-way that the input names a job the corpus does
        not hold (see `answer`). Its order carries the two corrections `ranking`
        applies, which is right here for that caller and only for it: the corrections
        compare the query against *titles*, which is noise against a described spec
        and exactly the point against a typed job name."""
        if retrieved is None:
            retrieved = self._retrieve(q_norm)
        order, dense, sparse = retrieved
        i1 = order[0]
        s1_dense, s1_sparse = float(dense[i1]), float(sparse[i1])
        related = [self.df.iloc[i]["job_title"] for i in order[:DISCOVERY_RELATED]]

        # Nothing in the request relates to work at all -> do not invent a job
        if s1_dense < DISCOVERY_FLOOR and s1_sparse < THRESHOLD_SPARSE:
            return {"mode": "out_of_domain", "intent": "job_request",
                    "score": s1_dense, "answer": OOD_MESSAGE}

        if s1_dense >= DISCOVERY_MATCH:
            row = self.df.iloc[i1]
            ans, (picks,) = self._answer_and_select([
                {"role": "system", "content": SYSTEM_JOB_MATCH},
                {"role": "user", "content":
                    f"اطلاعات شغل:\n{build_context(row, DISCOVERY_FIELDS)}\n\n"
                    f"توصیف کاربر: {question}"},
            ], question, [row], use_llm)
            if not ans:
                ans = f"{MATCH_HEADER}\n\n{template_one(row, DISCOVERY_FIELDS)}"
            return {"mode": "job_match", "intent": "job_request",
                    "job": row["job_title"], "score": s1_dense,
                    "related_jobs": related, "answer": ans,
                    "details": [job_detail(row, DISCOVERY_PRIMARY, picks)]}

        draft = self._generate_job(question, order[:DISCOVERY_RELATED]) if use_llm else None
        if draft is NOT_A_JOB:
            # Refused on content, not availability -- say so rather than blaming the API
            return {"mode": "out_of_domain", "intent": "job_request",
                    "score": s1_dense, "related_jobs": related,
                    "answer": DISCOVERY_NOT_REAL + "\n" + "\n".join(f"- {t}" for t in related)}
        if draft is None:
            # Generation needs the API; without it the nearest records are the best we have
            return {"mode": "out_of_domain", "intent": "job_request",
                    "score": s1_dense, "related_jobs": related,
                    "answer": DISCOVERY_UNAVAILABLE + "\n" + "\n".join(f"- {t}" for t in related)}

        # `details` shows the proposal; `job_draft` is what gets submitted. Same
        # content, different jobs: one is read by a person deciding, the other is
        # posted verbatim to /jobs/suggestions.
        return {"mode": "job_generated", "intent": "job_request",
                "job": draft["job_title"], "score": s1_dense,
                "job_draft": draft, "related_jobs": related,
                "answer": render_draft(draft, related),
                "details": [job_detail(draft, DISCOVERY_PRIMARY)]}

    # ---------- item selection ----------
    def _select_items(self, question, row):
        """Which members of this record's long columns the question is actually about.

        Returns `{field: [item, ...]}` — each column *whole*, with the items the model
        judged relevant first — or `{}`, which means "leave every column as the dataset
        stored it". Only `columns.RANKED_FIELDS` are offered, and only where the column
        holds more items than the client shows unopened: the four taxonomy columns
        arrive in O*NET's own importance order and there is nothing here that could
        improve on that, and a column of five has nothing to choose between.

        The model answers with **indices**, never with text, so it can point at an item
        but cannot write one — see SYSTEM_ITEM_SELECT. Everything it returns is checked
        in `_reorder`, and like every other call in this engine the failure path is the
        useful one: no API key, a refused request, unparseable JSON and an empty reply
        all end at `{}`, which is exactly what the boxes looked like before this
        existed.
        """
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
        ], temperature=0, max_tokens=SELECT_MAX_TOKENS, clean=False)

        picked = parse_json_object(raw)
        if picked is None:
            return {}
        return {field: _reorder(items, picked.get(field))
                for field, items in columns.items()}

    def _answer_and_select(self, messages, question, rows, use_llm, **kwargs):
        """The answer prose and one item selection per record, asked for at once.

        The two calls are independent — the selection reads the record's own columns
        and never the prose — so running them in sequence would add the whole of the
        second to every /search. Measured on the dev machine: 1.95 s for the answer
        alone and 2.79 s for the answer and one selection together, against the ~4.3 s
        the two cost one after the other.

        Returns the answer text (`""` if the API gave nothing, as the callers' template
        fallbacks expect) and one `{field: [item, ...]}` per row, in the rows' order.
        """
        if not use_llm:
            return "", [{}] * len(rows)
        with ThreadPoolExecutor(max_workers=1 + len(rows)) as pool:
            answer = pool.submit(self.llm, messages, **kwargs)
            picks = [pool.submit(self._select_items, question, row) for row in rows]
            return answer.result(), [pick.result() for pick in picks]

    # ---------- public API ----------
    def analyze(self, profile, use_llm=True):
        """Advanced search: rank the corpus against a described profile.

        `profile` is `{column: [item, ...]}` over `columns.PROFILE_FIELDS` — what the
        person can do, not a question about a job. Returns:

            mode      'profile_match' | 'out_of_domain'
            intent    'profile'
            answer    the analysis prose (or the template, if the API gave nothing)
            matches   PROFILE_TOP_N records, ranked, each with the per-field breakdown
                      of which of the user's items it accounted for, plus its own
                      `detail` in `job_detail`'s shape

        Nothing here can invent a job. That is the whole difference from `_discover`,
        which answers the same *need* from free text and may design a record: this path
        is an analysis of the corpus as it stands, so an empty result says so rather
        than filling the gap with a draft.
        """
        prof = profile_match.clean_profile(profile)
        if not prof:
            return {"mode": "out_of_domain", "intent": "profile",
                    "answer": PROFILE_NONE, "matches": []}

        # Dense against the full-record vectors only, not the hybrid the question path
        # uses: `emb_title` measures a query against titles and aliases, and a list of
        # skills has nothing to say to a job title. Mixing it in at W_TITLE would be
        # 40% noise. Nothing new is encoded — the query is written in `_combined_text`'s
        # own shape (profile.profile_query_text), so it meets the cached vectors on
        # their own terms.
        q_norm = normalize_text(profile_match.profile_query_text(prof))
        q_emb = self._encode([q_norm], "query")[0]
        dense = self.emb_full @ q_emb

        # Coverage is computed for the whole corpus rather than for a retrieved
        # shortlist: it is pure set arithmetic over pre-split tokens, and a record whose
        # words match every item the user typed must not be lost because the dense
        # channel ranked it 20th.
        ranked = []
        for idx in range(len(self.df)):
            fields, ratio = profile_match.coverage(prof, self.profile_tokens[idx])
            ranked.append((PROFILE_W_DENSE * float(dense[idx]) + PROFILE_W_COVER * ratio,
                           float(dense[idx]), ratio, fields, idx))
        ranked.sort(key=lambda r: r[0], reverse=True)

        best = ranked[0]
        # Nothing matched a single item the user typed, and dense alone is not strong
        # enough to vouch for the corpus without that evidence. Both halves are needed:
        # a profile written entirely in synonyms genuinely scores 0 here and is saved by
        # the dense clause, while «تربیت اژدها» measures 0.53 — high enough to look like
        # an answer, and it would come back as five unrelated jobs at 0% coverage.
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
        """Answers one question. Returns a dict with keys:
        mode ('single'|'interdisciplinary'|'job_match'|'job_generated'|'about'|
        'out_of_domain'), intent, answer, plus job/score fields depending on mode.
        'about' is the one mode decided before retrieval and carrying nothing but fixed
        text: the question was about this assistant rather than about an occupation
        ('about'), or was a greeting and no question at all ('greeting').
        Every mode but it and out_of_domain also carries 'details': the matched record(s) column by column
        (see render.job_detail), with the columns the answer was written from flagged
        'primary' and each column carrying the whole of itself plus a 'preview' count
        of how much of it is worth showing unopened. The columns whose stored order
        says nothing are re-ordered against this question first (_select_items), in the
        same round trip as the answer. 'job_generated' is an offer the user still has to accept; it carries
        'job_draft', the proposed record in the dataset's own columns, for the client
        to prefill its form with. It is reached two ways — from a sentence about
        wanting a job (`is_job_request`, before retrieval) and from a bare job name
        the corpus turns out not to hold (after it) — and both go through
        `_discover`, so the offer is the same offer."""
        q = normalize_text(question)

        # A described spec is a different task from a question about a known job
        if is_job_request(q):
            return self._discover(question, q, use_llm)

        # ...and a question about the assistant is not a question about a job at all.
        # Nothing downstream can improve on this answer: there is no record that holds
        # it, so retrieval only ever ranked something unrelated first. It sits *after*
        # the job request on purpose — «یک شغل خوب معرفی کنید» addresses the assistant
        # too, and the offer it asks for beats a description of what the assistant does.
        if is_about_system(q):
            return {"mode": "about", "intent": "about", "answer": ABOUT_MESSAGE}

        # A greeting is the same question with no question in it. Same mode and the same
        # text under a greeting of its own — the intent is what tells the two apart in a
        # log. It has to be the *whole* input: «سلام، وظایف پرستار چیست؟» is a question.
        if is_greeting(q):
            return {"mode": "about", "intent": "greeting", "answer": GREETING_MESSAGE}

        intent = detect_intent(q)

        # Bare job names ("معلم جغرافیا") carry no question verb -> description request
        bare_name = is_bare_name(q)
        if bare_name:
            intent = "description"
        fields = INTENT_TO_FIELDS.get(intent, INTENT_TO_FIELDS["general"])

        order, dense, sparse = self._retrieve(q)
        # Two corrections to the fused order, and the sequence matters: the first
        # undoes RRF outvoting a clear dense gap, the second lets a title that shares
        # the question's words beat a marginally denser record. Reversed, the dense
        # override would simply undo the title tiebreak on every question it fires on.
        order = prefer_dense_leader(order, dense)
        order = prefer_title_match(q, order, dense, self.titles)
        i1 = order[0]
        s1_dense, s1_sparse = float(dense[i1]), float(sparse[i1])

        # The user named a job and the corpus does not hold it. That is a request to
        # create one, not a question to answer from whatever ranked first: the record
        # that did rank first is unrelated, and describing it is how «مدیر فصلنامه»
        # came back as an answer reading «اطلاعات کافی در این مورد موجود نیست» sitting
        # on top of every column of «مدیران علوم طبیعی». `names_an_occupation` is what
        # keeps «کیک شکلاتی» out of the moderation queue — it scores in the same band,
        # and only the vocabulary says one of the two was never about work.
        # `_discover` still refuses below its own floor and still asks the model
        # whether the thing is a real occupation at all, so nothing here can invent a
        # record on its own.
        if (bare_name and s1_dense < THRESHOLD_MATCH and s1_sparse < NAMED_JOB_SPARSE
                and names_an_occupation(q)):
            return self._discover(question, q, use_llm, (order, dense, sparse))

        # Out-of-domain only if BOTH channels are weak
        if s1_dense < THRESHOLD_MATCH and s1_sparse < THRESHOLD_SPARSE:
            return {"mode": "out_of_domain", "intent": intent,
                    "score": s1_dense, "answer": OOD_MESSAGE}

        # First candidate that is NOT a near-duplicate of the leader
        i2 = next((c for c in order[1:SCAN_DEPTH + 1]
                   if float(self.emb_full[i1] @ self.emb_full[c]) < PAIR_SIM_MAX), None)

        explicit = any(k in q for k in EXPLICIT_COMBO_WORDS)
        interdisciplinary, s2_dense = False, None
        if i2 is not None:
            s2_dense = float(dense[i2])
            # abs(), because `order` is RRF-fused and its leader is not always the
            # dense leader: RRF compares *ranks*, not margins, so a record BM25 puts
            # first can carry a lower dense score than the runner-up. Written as
            # `s1 - s2 <= margin` that gap is negative and the test passes on its own —
            # which is how «وظایف مهندس راهسازی» came back as an interdisciplinary
            # answer over «مهندسان، سایر» (0.545) and «مهندسان عمران» (0.592), two jobs
            # 0.047 apart and in the wrong order. Measured over 158 questions: 7 answers
            # were interdisciplinary and 5 of them only because this abs() was missing.
            interdisciplinary = (
                (s2_dense >= SECONDARY_MIN and abs(s1_dense - s2_dense) <= SECONDARY_MARGIN)
                or (explicit and s2_dense >= THRESHOLD_MATCH - 0.05)
            )

        row1 = self.df.iloc[i1]

        if interdisciplinary:
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

        ans, (picks,) = self._answer_and_select([
            {"role": "system", "content": SYSTEM_SINGLE},
            {"role": "user", "content":
                f"اطلاعات شغل:\n{build_context(row1, fields)}\n\nسوال کاربر: {question}"},
        ], question, [row1], use_llm)
        if not ans:
            ans = template_one(row1, fields)
        return {"mode": "single", "intent": intent, "job": row1["job_title"],
                "score": s1_dense, "answer": ans,
                "details": [job_detail(row1, fields, picks)]}

# -*- coding: utf-8 -*-
"""Content-keyed embedding cache: one stored vector per text, not one file per corpus.

The cache used to be a single `.npz` per corpus, keyed on a digest of *every* text in
it. One approved suggestion therefore invalidated the other 1115 records' vectors too
and cost a full re-encode of 2232 texts — 258 s on the dev GPU, ~31 min on the
container's CPU torch — which is O(n) work for an O(1) change. Keying each vector on
its own text makes a rebuild encode only what it has never seen: two texts for a new
record, two for an edited one, none for a restart.

The vectors are unchanged — same model, same `normalize_embeddings=True`, same order
handed back to the engine — so retrieval and every threshold calibrated in `config.py`
mean exactly what they meant before.

The file holds two parallel arrays (`keys`, `vectors`) rather than one npz member per
text: 2232 members are slow to write and read, two are not.
"""

import hashlib
import logging
import os
import threading

import numpy as np

from .config import EMB_CACHE_DIR, EMBED_MODEL_NAME
from .text import corpus_fingerprint

log = logging.getLogger("job_qa_service")

_MODEL_TAG = EMBED_MODEL_NAME.replace("/", "_")


def text_key(text):
    """Cache key of one text: sha256 over the embedding model and the text itself.
    The model is part of the key because vectors from two models are not
    interchangeable, and both live in the same store file otherwise."""
    digest = hashlib.sha256(EMBED_MODEL_NAME.encode("utf-8"))
    digest.update(b"\x1f")
    digest.update(text.encode("utf-8"))
    return digest.hexdigest()


class EmbeddingStore:
    """A `key -> vector` map persisted as one npz under EMB_CACHE_DIR."""

    def __init__(self, cache_dir=None):
        self.dir = cache_dir or EMB_CACHE_DIR
        self.path = os.path.join(self.dir, f"vectors_{_MODEL_TAG}.npz")
        self._vectors = None                  # None until the file is first read
        self._dirty = False
        self._lock = threading.RLock()        # a rebuild runs on its own thread

    # ---------- persistence ----------
    def _ensure_loaded(self):
        """Reads the store once per process. Also creates EMB_CACHE_DIR here rather
        than at save time, so an unwritable cache dir (the classic one: EMB_CACHE_DIR
        left at the container's /srv/emb_cache while running locally) fails before
        half an hour of encoding instead of after it."""
        if self._vectors is not None:
            return
        os.makedirs(self.dir, exist_ok=True)
        self._vectors = {}
        if not os.path.exists(self.path):
            return
        try:
            with np.load(self.path) as data:
                keys, vectors = data["keys"], data["vectors"]
        except Exception as e:                # a truncated store costs a re-encode, not a crash
            log.warning(f"Embedding store at {self.path} unreadable ({e}); starting empty.")
            return
        if len(keys) == len(vectors):
            self._vectors = {str(k): v for k, v in zip(keys, vectors)}
        log.info(f"Embedding store: {len(self._vectors)} cached vectors.")

    def save(self):
        """Atomic: written beside the store and renamed over it, so a crash mid-write
        (or a second process saving at the same time) cannot leave a partial file."""
        with self._lock:
            if not self._dirty or not self._vectors:
                return
            keys = list(self._vectors)
            tmp = f"{self.path}.{os.getpid()}.tmp.npz"   # .npz or numpy appends it
            np.savez(tmp, keys=np.array(keys),
                     vectors=np.stack([self._vectors[k] for k in keys]))
            os.replace(tmp, self.path)
            self._dirty = False

    # ---------- the operation the engine needs ----------
    def embed(self, texts, encode, force=False):
        """Vectors for `texts`, in their order. `encode` — a callable taking a list of
        texts and returning a matrix — is called once, with only the texts the store
        does not already hold, or with all of them when `force`."""
        if not texts:
            return np.zeros((0, 0), dtype="float32")
        with self._lock:
            self._ensure_loaded()
            keys = [text_key(t) for t in texts]
            wanted = {}                       # key -> text, deduplicated
            for key, text in zip(keys, texts):
                if force or key not in self._vectors:
                    wanted.setdefault(key, text)
            if wanted:
                log.info(f"Encoding {len(wanted)} text(s); {len(texts) - len(wanted)} reused.")
                fresh = encode(list(wanted.values()))
                for key, vector in zip(wanted, fresh):
                    self._vectors[key] = vector
                self._dirty = True
            return np.stack([self._vectors[k] for k in keys])

    # ---------- migration ----------
    def adopt_corpus_cache(self, full_texts, title_texts):
        """Imports the vectors of the pre-per-row cache, if one matching this exact
        corpus is still lying in EMB_CACHE_DIR.

        Without this, the first startup after the switch would re-encode a corpus it
        already has perfectly good vectors for — 31 min of boot on CPU, and the
        deployed volume's `corpus_*.npz` thrown away for nothing. The old file is
        keyed on a digest of all its texts, which is exactly what makes it safe to
        adopt: if the name matches, the texts match, and the vectors are the ones this
        corpus would produce anyway. Superseded corpus files simply never match and
        are ignored (they are still safe to delete by hand)."""
        with self._lock:
            self._ensure_loaded()
            if all(text_key(t) in self._vectors for t in full_texts + title_texts):
                return
            name = (f"corpus_{_MODEL_TAG}_{len(full_texts)}_"
                    f"{corpus_fingerprint(full_texts, title_texts)}.npz")
            path = os.path.join(self.dir, name)
            if not os.path.exists(path):
                return
            try:
                with np.load(path) as data:
                    full, title = data["full"], data["title"]
            except Exception as e:
                log.warning(f"Corpus cache {name} unreadable ({e}); ignoring it.")
                return
            if len(full) != len(full_texts) or len(title) != len(title_texts):
                return
            for texts, vectors in ((full_texts, full), (title_texts, title)):
                for text, vector in zip(texts, vectors):
                    self._vectors[text_key(text)] = vector
            self._dirty = True
            log.info(f"Adopted {len(full) + len(title)} vectors from {name}.")


store = EmbeddingStore()

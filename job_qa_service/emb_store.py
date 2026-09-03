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
    digest = hashlib.sha256(EMBED_MODEL_NAME.encode("utf-8"))
    digest.update(b"\x1f")
    digest.update(text.encode("utf-8"))
    return digest.hexdigest()


class EmbeddingStore:
    def __init__(self, cache_dir=None):
        self.dir = cache_dir or EMB_CACHE_DIR
        self.path = os.path.join(self.dir, f"vectors_{_MODEL_TAG}.npz")
        self._vectors = None
        self._dirty = False
        self._lock = threading.RLock()

    def _ensure_loaded(self):
        if self._vectors is not None:
            return
        os.makedirs(self.dir, exist_ok=True)
        self._vectors = {}
        if not os.path.exists(self.path):
            return
        try:
            with np.load(self.path) as data:
                keys, vectors = data["keys"], data["vectors"]
        except Exception as e:
            log.warning(f"Embedding store at {self.path} unreadable ({e}); starting empty.")
            return
        if len(keys) == len(vectors):
            self._vectors = {str(k): v for k, v in zip(keys, vectors)}
        log.info(f"Embedding store: {len(self._vectors)} cached vectors.")

    def save(self):
        with self._lock:
            if not self._dirty or not self._vectors:
                return
            keys = list(self._vectors)
            tmp = f"{self.path}.{os.getpid()}.tmp.npz"
            np.savez(tmp, keys=np.array(keys),
                     vectors=np.stack([self._vectors[k] for k in keys]))
            os.replace(tmp, self.path)
            self._dirty = False

    def embed(self, texts, encode, force=False):
        if not texts:
            return np.zeros((0, 0), dtype="float32")
        with self._lock:
            self._ensure_loaded()
            keys = [text_key(t) for t in texts]
            wanted = {}
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

    def adopt_corpus_cache(self, full_texts, title_texts):
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

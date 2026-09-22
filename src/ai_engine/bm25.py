import math
from collections import Counter, defaultdict

import numpy as np


class BM25:
    K1, B = 1.5, 0.75

    def __init__(self, texts):
        corpus_tokens = [t.lower().split() for t in texts]
        self.doc_count = len(corpus_tokens)
        self.doc_lengths = np.array([len(d) for d in corpus_tokens], dtype=np.float32)
        self.avg_len = float(np.mean(self.doc_lengths)) if self.doc_count else 1.0

        self.inverted = defaultdict(dict)
        for doc_id, tokens in enumerate(corpus_tokens):
            for tok, cnt in Counter(tokens).items():
                self.inverted[tok][doc_id] = cnt
        self.idf = {tok: math.log((self.doc_count - len(dd) + 0.5) / (len(dd) + 0.5) + 1.0)
                    for tok, dd in self.inverted.items()}
        self.oov_idf = math.log((self.doc_count + 0.5) / 0.5 + 1.0)

    def score(self, query):
        scores = np.zeros(self.doc_count, dtype=np.float32)
        max_possible = 0.0
        for tok in set(query.lower().split()):
            idf = self.idf.get(tok, self.oov_idf)
            max_possible += idf * (self.K1 + 1.0)
            dd = self.inverted.get(tok)
            if not dd:
                continue
            idxs = np.fromiter(dd.keys(), dtype=np.int64)
            tfs = np.fromiter(dd.values(), dtype=np.float32)
            lens = self.doc_lengths[idxs]
            denom = tfs + self.K1 * (1.0 - self.B + self.B * lens / self.avg_len)
            scores[idxs] += idf * tfs * (self.K1 + 1.0) / denom
        return scores / max_possible if max_possible > 0 else scores

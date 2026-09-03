import re

from .config import (DENSE_LEAD_DEPTH, DENSE_LEAD_MARGIN, TITLE_TIEBREAK_DEPTH,
                     TITLE_TIEBREAK_MARGIN, TITLE_TOKEN_MIN)
from .intents import QUESTION_WORDS


def prefer_dense_leader(order, dense):
    if len(order) < 2:
        return order
    head = order[:DENSE_LEAD_DEPTH + 1]
    best = max(head, key=lambda i: float(dense[i]))
    if best == order[0] or float(dense[best]) - float(dense[order[0]]) <= DENSE_LEAD_MARGIN:
        return order
    return [best] + [i for i in order if i != best]


def content_tokens(text):
    return {t for t in re.split(r"[\s،|/()\-–]+", text)
            if len(t) >= TITLE_TOKEN_MIN} - QUESTION_WORDS


def title_overlap(q_tokens, title):
    t_tokens = content_tokens(title)
    return sum(any(tt.startswith(qt) or qt.startswith(tt) for tt in t_tokens)
               for qt in q_tokens)


_WORD_SPLIT = re.compile(r"[\s،؛:؟?!.,«»()\[\]/|\-–]+")


def _words(text):
    return [w for w in _WORD_SPLIT.split(text) if w]


def prefer_contained_title(q_norm, order, titles):
    q_words = _words(q_norm)
    if not q_words:
        return order
    best = None
    for idx, title in enumerate(titles):
        t_words = _words(title)
        n = len(t_words)
        if n < 2 or n > len(q_words):
            continue
        if any(q_words[k:k + n] == t_words for k in range(len(q_words) - n + 1)):
            key = (n, len(title), idx)
            if best is None or key[:2] > best[:2]:
                best = key
    if best is None or best[2] == order[0]:
        return order
    return [best[2]] + [i for i in order if i != best[2]]


def prefer_title_match(q_norm, order, dense, titles):
    if len(order) < 2:
        return order
    lead = float(dense[order[0]])
    close = [i for i in order[:TITLE_TIEBREAK_DEPTH]
             if lead - float(dense[i]) <= TITLE_TIEBREAK_MARGIN]
    if len(close) < 2:
        return order

    q_tokens = content_tokens(q_norm)
    if not q_tokens:
        return order
    scored = [(title_overlap(q_tokens, titles[i]), i) for i in close]
    best_n, best_i = max(scored, key=lambda s: s[0])
    if best_i == order[0] or best_n <= scored[0][0]:
        return order
    return [best_i] + [i for i in order if i != best_i]

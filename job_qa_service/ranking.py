# -*- coding: utf-8 -*-
"""The question-path title tiebreak.

Dense similarity put «خدمه توپخانه و موشک» (0.667) above «افسران توپخانه و موشک»
(0.650) for «وظایف افسر توپخانه چیست؟» — right unit, wrong rank — and the answer then
described the crew to someone asking about officers, or refused outright with «اطلاعات
کافی...» in 5 runs out of 6 because SYSTEM_SINGLE rule 6 read crew-vs-officer as
unrelated. The refusal was the symptom; the ranking was the bug.

Used only on the question path. `_discover` matches a description of duties against
job titles, where this overlap would be noise.
"""

import re

from .config import TITLE_TIEBREAK_DEPTH, TITLE_TIEBREAK_MARGIN, TITLE_TOKEN_MIN
from .intents import QUESTION_WORDS


def content_tokens(text):
    """Content words of a question or a job title, with affixes and stopwords dropped."""
    return {t for t in re.split(r"[\s،|/()\-–]+", text)
            if len(t) >= TITLE_TOKEN_MIN} - QUESTION_WORDS


def title_overlap(q_tokens, title):
    """How many content words a title shares with the question. Prefix matching in
    both directions absorbs Persian plural and adjective suffixes, which is the whole
    point here: «افسر» in the question has to reach «افسران» in the title."""
    t_tokens = content_tokens(title)
    return sum(any(tt.startswith(qt) or qt.startswith(tt) for tt in t_tokens)
               for qt in q_tokens)


def prefer_title_match(q_norm, order, dense, titles):
    """Promotes a near-tied candidate whose title shares more content words with
    the question than the leader's does. Conservative by construction: it only
    looks at candidates within TITLE_TIEBREAK_MARGIN of the leader, and it needs
    a strict improvement, so an unbeaten leader and a no-overlap tie both keep
    the dense ordering untouched."""
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

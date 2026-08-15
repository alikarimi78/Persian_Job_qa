# -*- coding: utf-8 -*-
"""Advanced search: a described profile, matched against the corpus column by column.

The question this answers is not the one `answer()` answers. There the user has a job in
mind and wants prose about it; here they have a list of things they can do and want to
know which records those things add up to. So the unit of comparison is the **item** —
one skill, one tool — and the output has to be able to name which of the user's items a
record honoured, because "چرا این شغل؟" is the whole point of an analysis.

That is also why this sits beside the dense channel rather than replacing it. Dense
similarity understands that «حل مسئله» and «تحلیل مشکلات» are the same thing but cannot
say which item it honoured; the overlap below can name every match but only sees words
that are literally there. Ranking uses both (`engine.analyze`), and only the overlap is
ever shown to the user, because only it can be pointed at.
"""

import re

from .columns import EMPTY_CELLS, FIELD_LABELS, PROFILE_FIELDS, PROSE_COLUMNS
from .config import PROFILE_TOKEN_MIN
from .text import normalize_text

# The separators a person might reach for, all of them. The whole reason the client now
# collects items one box at a time is that «،» / «,» / «|» was a guess the user had to
# make — but an item pasted from somewhere else still arrives with punctuation in it,
# and it is cheaper to split here than to refuse it.
_SPLIT = re.compile(r"[\s،,;؛/|()\[\]\-–—.]+")


def clean_items(values):
    """Normalized, de-duplicated items, in the order given.

    Order is kept because it is the user's own — the first tool they thought of is the
    one they care about — and duplicates are dropped because they would otherwise weight
    the coverage ratio twice for the same idea.
    """
    seen, items = set(), []
    for value in values or []:
        item = normalize_text(value)
        if not item or item in EMPTY_CELLS:
            continue
        key = item.lower()
        if key in seen:
            continue
        seen.add(key)
        items.append(item)
    return items


def clean_profile(profile):
    """The whole profile, projected onto PROFILE_FIELDS and cleaned.

    Projected rather than taken as given: this is the one place a request from the web
    layer becomes engine input, and a key nobody here knows about must not travel any
    further. Fields that end up empty are dropped, so `len(profile)` afterwards is the
    number of fields actually filled in.
    """
    cleaned = {}
    for field in PROFILE_FIELDS:
        items = clean_items((profile or {}).get(field))
        if items:
            cleaned[field] = items
    return cleaned


def profile_query_text(profile):
    """The profile written out the way `engine._combined_text` writes a record.

    Same labels, same «، » joins, same `label: value` lines — the corpus vectors were
    built from that shape, and handing the encoder a query in the same one is free
    accuracy. It is also why nothing new has to be encoded for this feature: the query
    goes against the embeddings that are already cached.
    """
    return " . ".join(f"{FIELD_LABELS[field]}: " + "، ".join(items)
                      for field, items in profile.items() if items)


# ---------- item matching ----------

def tokens(text):
    """Content tokens of one item, with affixes and stopwords dropped by length.

    Same rule as `ranking.content_tokens` and for the same reason — Persian glues short
    function words onto phrases, and «و» or «با» shared between two items is not
    evidence of anything.
    """
    return {t for t in _SPLIT.split(text.lower()) if len(t) >= PROFILE_TOKEN_MIN}


def _covers(small, large):
    """Every token of `small` is prefix-matched by some token of `large`.

    Prefix in both directions, as the title tiebreak does: «برنامه‌نویس» has to reach
    «برنامه‌نویسی», or half the plural and adjective forms in the dataset never match.
    """
    return all(any(t.startswith(s) or s.startswith(t) for t in large) for s in small)


def items_match(user_tokens, record_tokens):
    """Whether two items are the same thing, as far as words can tell.

    **Containment, not overlap.** A shared token is not enough: «طراحی سیستم» and
    «طراحی لباس» share «طراحی» and are unrelated, and letting that count would inflate
    every coverage ratio on the page with matches the user can see are wrong. One side
    has to be fully covered by the other, which is what makes «پایتون» match
    «برنامه‌نویسی پایتون» while the design pair stays apart.
    """
    if not user_tokens or not record_tokens:
        return False
    return _covers(user_tokens, record_tokens) or _covers(record_tokens, user_tokens)


def record_items(field, value):
    """The record's side of the comparison, split into comparable pieces.

    A list column splits on «|». A prose column has nothing to split on, so the whole
    cell is one piece — the user's short phrase is then matched against the sentence,
    which is exactly the containment `items_match` already does.
    """
    value = str(value or "").strip()
    if value in EMPTY_CELLS:
        return []
    if field in PROSE_COLUMNS:
        return [value]
    return [p.strip() for p in value.split("|")
            if p.strip() and p.strip() not in EMPTY_CELLS]


def record_tokens(row):
    """Token sets for one record, per profile field, computed once at engine build.

    Two views of the same column, because matching needs both (see `field_coverage`):
    `items` is one token set per «|»-separated member, and `words` is the union — every
    content word the column contains, whichever member it sits in.

    Doing this per request would mean re-splitting 1116 records × 6 columns on every
    search; done here the ranking loop only reads it.
    """
    view = {}
    for field in PROFILE_FIELDS:
        item_tokens = [tokens(item) for item in record_items(field, row.get(field, ""))]
        view[field] = {"items": item_tokens, "words": set().union(*item_tokens)
                       if item_tokens else set()}
    return view


# ---------- coverage ----------

# A column the record has nothing in; `coverage` hands this to `field_coverage` rather
# than branching, so an empty cell reports every item as missing instead of vanishing.
_EMPTY_COLUMN = {"items": [], "words": set()}

def field_coverage(user_items, column):
    """Which of the user's items this record's column accounts for.

    Two ways to count as matched, and the second one is not a softening — it is what
    makes the number true. Item-to-item containment alone reported that «مکانیک خودرو»
    is absent from «مکانیک‌ها و تکنسین‌های خدمات خودرو», because the record says
    «مهارت مکانیکی» in one member and «برق و انژکتور خودرو» in another and neither
    contains both words. The column is the unit the user is actually describing — «my
    skills» — so an item whose every word appears *somewhere* among that column's
    members counts too.

    The price is a compound item whose words are scattered across unrelated members
    («طراحی سیستم» against a column holding «طراحی لباس» and «سیستم عامل»). That is
    accepted deliberately: under-reporting made the headline ratio wrong on the very
    records the search exists to find, and this is the direction the error should lean.

    Returns the items in the user's own words — not the record's — because the list is
    read back by the person who typed them, and «۴ از ۶ مهارت شما» has to be checkable
    against what they entered.
    """
    matched, missing = [], []
    for item in user_items:
        item_tokens = tokens(item)
        hit = (any(items_match(item_tokens, rt) for rt in column["items"])
               or (bool(item_tokens) and _covers(item_tokens, column["words"])))
        (matched if hit else missing).append(item)
    return matched, missing


def coverage(profile, row_tokens):
    """The whole profile against one record.

    The overall ratio is **item-weighted, not field-weighted**: a profile of six skills
    and one tool is mostly a statement about skills, and averaging the two fields
    equally would let a single matched tool outweigh five missed skills.
    """
    fields, matched_total, item_total = [], 0, 0
    for field, items in profile.items():
        matched, missing = field_coverage(items, row_tokens.get(field, _EMPTY_COLUMN))
        matched_total += len(matched)
        item_total += len(items)
        fields.append({
            "key": field,
            "label": FIELD_LABELS.get(field, field),
            "matched": matched,
            "missing": missing,
            "ratio": len(matched) / len(items) if items else 0.0,
        })
    return fields, (matched_total / item_total if item_total else 0.0)

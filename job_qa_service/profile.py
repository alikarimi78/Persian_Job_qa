import re

from .columns import EMPTY_CELLS, FIELD_LABELS, PROFILE_FIELDS, PROSE_COLUMNS
from .config import PROFILE_TOKEN_MIN
from .text import normalize_text

_SPLIT = re.compile(r"[\s،,;؛/|()\[\]\-–—.]+")


def clean_items(values):
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
    cleaned = {}
    for field in PROFILE_FIELDS:
        items = clean_items((profile or {}).get(field))
        if items:
            cleaned[field] = items
    return cleaned


def profile_query_text(profile):
    return " . ".join(f"{FIELD_LABELS[field]}: " + "، ".join(items)
                      for field, items in profile.items() if items)


def tokens(text):
    return {t for t in _SPLIT.split(text.lower()) if len(t) >= PROFILE_TOKEN_MIN}


def _covers(small, large):
    return all(any(t.startswith(s) or s.startswith(t) for t in large) for s in small)


def items_match(user_tokens, record_tokens):
    if not user_tokens or not record_tokens:
        return False
    return _covers(user_tokens, record_tokens) or _covers(record_tokens, user_tokens)


def record_items(field, value):
    value = str(value or "").strip()
    if value in EMPTY_CELLS:
        return []
    if field in PROSE_COLUMNS:
        return [value]
    return [p.strip() for p in value.split("|")
            if p.strip() and p.strip() not in EMPTY_CELLS]


def record_tokens(row):
    view = {}
    for field in PROFILE_FIELDS:
        item_tokens = [tokens(item) for item in record_items(field, row.get(field, ""))]
        view[field] = {"items": item_tokens, "words": set().union(*item_tokens)
                       if item_tokens else set()}
    return view


_EMPTY_COLUMN = {"items": [], "words": set()}

def field_coverage(user_items, column):
    matched, missing = [], []
    for item in user_items:
        item_tokens = tokens(item)
        hit = (any(items_match(item_tokens, rt) for rt in column["items"])
               or (bool(item_tokens) and _covers(item_tokens, column["words"])))
        (matched if hit else missing).append(item)
    return matched, missing


def coverage(profile, row_tokens):
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

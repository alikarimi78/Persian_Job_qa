import re
from bisect import bisect_left
from typing import NamedTuple

from .columns import EMPTY_CELLS, FIELD_LABELS, PROFILE_FIELDS, PROSE_COLUMNS
from .config import PROFILE_SHORT_MIN, PROFILE_TOKEN_MIN
from .text import normalize_text

_SPLIT = re.compile(r"[\s،,;؛/|()\[\]\-–—.]+")
_ZWNJ = "‌"
# Letters written two ways and marks nobody types, folded on both sides so «موثر» meets «مؤثر».
_FOLD = str.maketrans({"ؤ": "و", "أ": "ا", "إ": "ا", "ٱ": "ا", "ئ": "ی", "ي": "ی", "ى": "ی",
                       "ك": "ک", "ة": "ه", "ۀ": "ه"})
_MARKS = re.compile(r"[ً-ٰٟـ]")
# Words that join rather than mean. Once short words count, «مراقبت از بیمار» must still be two
# words to find, not three.
_STOPWORDS = frozenset({"و", "در", "به", "از", "با", "برای", "را", "که", "یا", "تا", "بر", "این",
                        "آن", "ها", "های", "هایی", "می", "نمی", "هم", "نیز", "خود", "یک", "است"})


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


# `requirements` are alternative sets of words, any one of which the other side must hold for the
# item to count; `words` is what the item offers the other side. `joined` maps each word in the
# requirements that is two typed words run together to the length of the first of them.
#
# A half-space is dropped on both sides, so «روانشناسی» typed without one meets the records'
# «روان‌شناسی». It is dropped rather than read as a space: splitting the records' compounds let
# «برنامه» out of «برنامه‌ریزی» and a prefix then reached every unspaced «برنامه…» a user typed.
# A compound typed with a space — «برنامه نویسی» — is covered by the joined form of each adjacent pair,
# offered as a target and as an alternative requirement. Short words («حل», «دقت») are required only
# when an item has no longer word, which keeps «ساخت و ساز» matching as it did while they were dropped.
class ProfileItem(NamedTuple):
    requirements: tuple
    joined: dict
    words: frozenset


def _words(text):
    folded = _MARKS.sub("", str(text or "").lower().translate(_FOLD)).replace(_ZWNJ, "")
    return [word for word in _SPLIT.split(folded)
            if len(word) >= PROFILE_SHORT_MIN and word not in _STOPWORDS]


def profile_item(text):
    words = _words(text)
    required = frozenset([word for word in words if len(word) >= PROFILE_TOKEN_MIN] or words)
    requirements = [required] if required else []
    joined = {}
    for first, second in zip(words, words[1:]):
        pair = first + second
        joined[pair] = len(first)
        if first in required or second in required:
            requirements.append((required - {first, second}) | {pair})
    return ProfileItem(tuple(requirements), joined, frozenset(words) | set(joined))


# Words held twice: as a set for the exact match and in order for the prefix one, so a lookup in a
# whole column costs a bisect rather than a scan.
class Targets(NamedTuple):
    words: frozenset
    ordered: tuple


def _index(words):
    return Targets(frozenset(words), tuple(sorted(words)))


# A word of PROFILE_TOKEN_MIN letters or more matches by prefix either way, so «برنامه‌نویس» reaches
# «برنامه‌نویسی»; a shorter one only exactly, since a two- or three-letter prefix — «کار» of «کارشناس» —
# matches far too much. A joined word may only be begun by a target reaching past its first typed word
# (`floor`): «برنامهنویسی» is begun by «برنامه‌نویس», but «طراحی لباس» must not stand in for every record
# that merely says «طراحی».
def _found(word, targets, floor=0):
    if word in targets.words:
        return True
    if len(word) < PROFILE_TOKEN_MIN:
        return False
    # Every target that begins with the word sorts directly after it.
    at = bisect_left(targets.ordered, word)
    if at < len(targets.ordered) and targets.ordered[at].startswith(word):
        return True
    # A target the word begins with is one of the word's own prefixes.
    shortest = max(PROFILE_TOKEN_MIN, floor + 1)
    return any(word[:size] in targets.words for size in range(shortest, len(word)))


def _satisfied(item, found):
    return any(all(found(word, item.joined.get(word, 0)) for word in required)
               for required in item.requirements)


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
        items = [profile_item(item) for item in record_items(field, row.get(field, ""))]
        view[field] = {"items": items,
                       "words": _index(set().union(*(item.words for item in items)))}
    return view


_EMPTY_COLUMN = {"items": [], "words": _index(())}


class _UserItem(NamedTuple):
    text: str
    item: ProfileItem
    found: object


# A user item's words are looked up against every record's words hundreds of times per request, and
# the records share most of their phrases, so each item remembers its answers for the request.
def _user_item(text):
    item = profile_item(text)
    targets, answers = _index(item.words), {}

    def found(word, floor=0):
        key = (word, floor)
        if key not in answers:
            answers[key] = _found(word, targets, floor)
        return answers[key]

    return _UserItem(text, item, found)


# The profile read once per request instead of once per record.
def prepare(profile):
    return [(field, [_user_item(text) for text in items]) for field, items in profile.items()]


# An item counts when every word of it appears somewhere in the column — which one record item holding
# them all satisfies too, so that case needs no pass of its own — or when some record item's words all
# sit inside the user's item.
def field_coverage(user_items, column):
    matched, missing = [], []
    words = column["words"]
    for user in user_items:
        hit = (_satisfied(user.item, lambda word, floor: _found(word, words, floor))
               or any(_satisfied(record, user.found) for record in column["items"]))
        (matched if hit else missing).append(user.text)
    return matched, missing


def coverage(prepared, row_tokens):
    fields, matched_total, item_total = [], 0, 0
    for field, items in prepared:
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

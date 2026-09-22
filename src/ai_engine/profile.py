import math
import re
from bisect import bisect_left
from typing import NamedTuple

from .columns import (EMPTY_CELLS, FIELD_LABELS, MATCH_COLUMNS, PROFILE_FIELDS,
                      PROSE_COLUMNS)
from .config import (PROFILE_ELSEWHERE_WEIGHT, PROFILE_IDF_CAP, PROFILE_SHORT_MIN,
                     PROFILE_TOKEN_MIN, PROFILE_WEIGHT_MIN)
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


# The tools column is written the way the vendor writes it — `Python`, `AutoCAD`, `SAP` — while a
# Persian reader names the same tool in Persian letters. Each of these is offered as an alternative
# spelling in both directions, which is the whole of what «پایتون» needs to meet `Python`. Only
# tools people actually type; a transliteration rule would turn every English word into a Persian
# one and match far more than it should.
TOOL_FORMS = {
    "پایتون": "python", "جاوا": "java", "جاوااسکریپت": "javascript", "سیشارپ": "c#",
    "اسکیوال": "sql", "مایاسکیوال": "mysql", "پستگرس": "postgresql", "اوراکل": "oracle",
    "اکسل": "excel", "ورد": "word", "پاورپوینت": "powerpoint", "اوتلوک": "outlook",
    "اکسس": "access", "فتوشاپ": "photoshop", "ایلوستریتور": "illustrator",
    "اتوکد": "autocad", "سالیدورکس": "solidworks", "سولیدورک": "solidworks",
    "کتیا": "catia", "انسیس": "ansys", "متلب": "matlab", "لینوکس": "linux",
    "ویندوز": "windows", "گیت": "git", "داکر": "docker", "کوبرنتیز": "kubernetes",
    "ریاکت": "react", "انگولار": "angular", "اندروید": "android", "لاراول": "laravel",
    "وردپرس": "wordpress", "تبلو": "tableau", "ارکجیایاس": "arcgis", "کورل": "coreldraw",
    "رویت": "revit", "اسکچاپ": "sketchup", "پریمیر": "premiere", "سیانسی": "cnc",
    "پیالسی": "plc", "ایتبس": "etabs", "سپ": "sap",
}


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
    forms = {word: TOOL_FORMS[word] for word in words if word in TOOL_FORMS}
    if forms.keys() & required:
        requirements.append(frozenset(forms.get(word, word) for word in required))
    return ProfileItem(tuple(requirements), joined,
                       frozenset(words) | set(joined) | set(forms.values()))


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


# A record is indexed twice: column by column, which is what the profile field of the same name is
# compared against, and as one flat list of every item it holds, which is where an item that column
# cannot possibly hold is then looked for. The record's whole vocabulary rides along as a gate — a
# record missing one of the item's words holds it in no column — and that one lookup skips most of
# the corpus before the flat list is walked at all.
def record_tokens(row):
    made = {field: [profile_item(text) for text in record_items(field, row.get(field, ""))]
            for field in dict.fromkeys(PROFILE_FIELDS + MATCH_COLUMNS)}
    columns = {field: {"items": made[field],
                       "words": _index(set().union(*(item.words for item in made[field])))}
               for field in PROFILE_FIELDS}
    elsewhere = tuple((field, item) for field in MATCH_COLUMNS for item in made[field])
    return {"columns": columns, "elsewhere": elsewhere,
            "words": _index(set().union(*(item.words for _, item in elsewhere)))}


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
def _in_column(user, column):
    return (_satisfied(user.item, lambda word, floor: _found(word, column["words"], floor))
            or any(_satisfied(record, user.found) for record in column["items"]))


# Which words of the record satisfy one word of the user's item — the three ways `_found` answers
# yes, named rather than counted. Read once per record from its index, each item is then tried by
# set intersection instead of by scanning its own words for every word asked about.
def _matching(word, targets, floor=0):
    found = {word} if word in targets.words else set()
    if len(word) < PROFILE_TOKEN_MIN:
        return found
    at = bisect_left(targets.ordered, word)
    while at < len(targets.ordered) and targets.ordered[at].startswith(word):
        found.add(targets.ordered[at])
        at += 1
    shortest = max(PROFILE_TOKEN_MIN, floor + 1)
    found.update(word[:size] for size in range(shortest, len(word))
                 if word[:size] in targets.words)
    return found


# Outside its own column only the strict direction counts: every word of the user's item inside one
# item of the record. The other direction — a record item covered by the user's — is what lets a bare
# «طراحی» stand in for «طراحی لباس», and across nine columns every record holds some bare word that
# would. A required word the whole record cannot satisfy ends the pass before any item is read.
def _elsewhere(user, tokens):
    for required in user.item.requirements:
        groups = []
        for word in required:
            group = _matching(word, tokens["words"], user.item.joined.get(word, 0))
            if not group:
                break
            groups.append(group)
        else:
            for field, item in tokens["elsewhere"]:
                if all(not item.words.isdisjoint(group) for group in groups):
                    return field
    return None


# Which column of this record holds each item of the profile, or None where none of them does.
def evaluate(prepared, tokens):
    return [[(field if _in_column(user, tokens["columns"].get(field, _EMPTY_COLUMN))
              else _elsewhere(user, tokens))
             for user in items]
            for field, items in prepared]


# What an item is worth is how few records hold it. «گوش دادن فعال» sits in 1046 of the 1120 records
# and separates nothing; «برنامه‌نویسی» sits in 100 and separates well. Unweighted, a profile of
# O*NET's ten basic skills — which every record carries — scored hundreds of records exactly alike
# and left the dense channel to rank them alone.
def weigh(prepared, hits, records):
    counts = [[0] * len(items) for _, items in prepared]
    for row in hits:
        for f, found in enumerate(row):
            for i, where in enumerate(found):
                if where is not None:
                    counts[f][i] += 1
    weights = [[0.0 if count == 0 else min(PROFILE_IDF_CAP, math.log(records / count))
                for count in row] for row in counts]
    return counts, weights


# An item no record in reach holds at all is not a gap in the person — the corpus has no word for it
# — so it is reported apart and left out of both ratios. Counted as missing it read as «۰٪ پوشش» on
# a ranking that was exactly right, which is the complaint this whole pass exists to answer.
def summarize(prepared, hits, counts, weights):
    fields, matched_n, total_n, matched_w, total_w = [], 0, 0, 0.0, 0.0
    for (field, items), found, counted, weighted in zip(prepared, hits, counts, weights):
        matched, missing, unknown, where = [], [], [], {}
        for user, hit, count, weight in zip(items, found, counted, weighted):
            if count == 0:
                unknown.append(user.text)
                continue
            total_n, total_w = total_n + 1, total_w + weight
            if hit is None:
                missing.append(user.text)
                continue
            matched.append(user.text)
            matched_n = matched_n + 1
            # A record holding the item in the column it was typed in is the stronger answer; one
            # holding it somewhere else counts for part of it, which is what keeps a record that
            # merely mentions the word from ranking beside the one the item belongs to.
            matched_w += weight if hit == field else weight * PROFILE_ELSEWHERE_WEIGHT
            if hit != field:
                where[user.text] = hit
        fields.append({
            "key": field,
            "label": FIELD_LABELS.get(field, field),
            "matched": matched,
            "missing": missing,
            "unknown": unknown,
            "found_in": where,
            "ratio": len(matched) / (len(matched) + len(missing)) if matched or missing else 0.0,
        })
    # The ratio the reader sees counts items, so it is the one they can check against the chips; the
    # one that ranks weighs them. A profile of nothing but items every record holds weighs ~0 either
    # way, and falls back on the count rather than dividing by it.
    plain = matched_n / total_n if total_n else 0.0
    weighted = matched_w / total_w if total_w > PROFILE_WEIGHT_MIN else plain
    return fields, plain, weighted


def rank(prepared, tokens_list):
    hits = [evaluate(prepared, tokens) for tokens in tokens_list]
    counts, weights = weigh(prepared, hits, len(tokens_list))
    return [summarize(prepared, row, counts, weights) for row in hits]

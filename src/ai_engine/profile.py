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
_FOLD = str.maketrans({"ؤ": "و", "أ": "ا", "إ": "ا", "ٱ": "ا", "ئ": "ی", "ي": "ی", "ى": "ی",
                       "ك": "ک", "ة": "ه", "ۀ": "ه"})
_MARKS = re.compile(r"[ً-ٰٟـ]")
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


class ProfileItem(NamedTuple):
    requirements: tuple
    joined: dict
    words: frozenset


def _words(text):
    folded = _MARKS.sub("", str(text or "").lower().translate(_FOLD)).replace(_ZWNJ, "")
    return [word for word in _SPLIT.split(folded)
            if len(word) >= PROFILE_SHORT_MIN and word not in _STOPWORDS]


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


class Targets(NamedTuple):
    words: frozenset
    ordered: tuple


def _index(words):
    return Targets(frozenset(words), tuple(sorted(words)))


def _found(word, targets, floor=0):
    if word in targets.words:
        return True
    if len(word) < PROFILE_TOKEN_MIN:
        return False
    at = bisect_left(targets.ordered, word)
    if at < len(targets.ordered) and targets.ordered[at].startswith(word):
        return True
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


def _user_item(text):
    item = profile_item(text)
    targets, answers = _index(item.words), {}

    def found(word, floor=0):
        key = (word, floor)
        if key not in answers:
            answers[key] = _found(word, targets, floor)
        return answers[key]

    return _UserItem(text, item, found)


def prepare(profile):
    return [(field, [_user_item(text) for text in items]) for field, items in profile.items()]


def _in_column(user, column):
    return (_satisfied(user.item, lambda word, floor: _found(word, column["words"], floor))
            or any(_satisfied(record, user.found) for record in column["items"]))


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


def evaluate(prepared, tokens):
    return [[(field if _in_column(user, tokens["columns"].get(field, _EMPTY_COLUMN))
              else _elsewhere(user, tokens))
             for user in items]
            for field, items in prepared]


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
    plain = matched_n / total_n if total_n else 0.0
    weighted = matched_w / total_w if total_w > PROFILE_WEIGHT_MIN else plain
    return fields, plain, weighted


def rank(prepared, tokens_list):
    hits = [evaluate(prepared, tokens) for tokens in tokens_list]
    counts, weights = weigh(prepared, hits, len(tokens_list))
    return [summarize(prepared, row, counts, weights) for row in hits]

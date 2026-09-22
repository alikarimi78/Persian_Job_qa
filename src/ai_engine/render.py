from .columns import (DETAIL_FIELDS, EMPTY_CELLS, FIELD_LABELS, PROSE_COLUMNS)
from .config import PREVIEW_ITEMS, PROFILE_CONTEXT_ITEMS, PROFILE_RECORD_MATCHES
from .messages import (DRAFT_HEADER, DRAFT_QUESTION, PROFILE_COVER_LABEL,
                       PROFILE_ELSEWHERE_LABEL, PROFILE_HEADER, PROFILE_MISSING_LABEL,
                       PROFILE_RECORD_LABEL, PROFILE_UNKNOWN_LABEL)


def build_context(row, fields, include_title=True):
    lines = []
    if include_title:
        lines.append(f"{FIELD_LABELS['job_title']}: {row['job_title']}")
        if row.get("aliases"):
            lines.append(f"{FIELD_LABELS['aliases']}: {row['aliases']}")
    lines += [f"{FIELD_LABELS.get(f, f)}: {row.get(f, '')}" for f in fields if row.get(f, "")]
    return "\n".join(lines)


def template_one(row, fields):
    return f"📌 {row['job_title']}\n\n" + build_context(row, fields, include_title=False)


def template_two(row1, row2, fields):
    return (f"🔗 نقش تلفیقی: {row1['job_title']} + {row2['job_title']}\n\n"
            f"— {row1['job_title']}:\n{build_context(row1, fields, include_title=False)}\n\n"
            f"— {row2['job_title']}:\n{build_context(row2, fields, include_title=False)}")


def render_draft(draft):
    lines = [DRAFT_HEADER, "", f"📌 {FIELD_LABELS['job_title']}: {draft['job_title']}"]
    if draft.get("description"):
        lines.append(f"{FIELD_LABELS['description']}: {draft['description']}")
    lines += ["", DRAFT_QUESTION]
    return "\n".join(lines)


def template_profile(matches):
    lines = [PROFILE_HEADER]
    for match in matches:
        lines += ["", f"📌 {match['job_title']}"]
        for field in match["fields"]:
            if field["matched"]:
                lines.append(f"{field['label']} — {PROFILE_COVER_LABEL}: "
                             + "، ".join(_located(field)))
            if field["missing"]:
                lines.append(f"{field['label']} — {PROFILE_MISSING_LABEL}: "
                             + "، ".join(field["missing"]))
            if field.get("unknown"):
                lines.append(f"{field['label']} — {PROFILE_UNKNOWN_LABEL}: "
                             + "، ".join(field["unknown"]))
    return "\n".join(lines)


# A matched item says where it was found when that is not the column it was typed in — this is what
# lets the answer explain that «برنامه‌نویسی» is covered by the record's duties rather than claim a
# closed skills vocabulary holds it.
def _located(field):
    where = field.get("found_in") or {}
    return [item + (f" ({PROFILE_ELSEWHERE_LABEL} {FIELD_LABELS.get(where[item], where[item])})"
                    if item in where else "")
            for item in field["matched"]]


def profile_context(profile, matches):
    lines = ["پروفایل کاربر:"]
    lines += [f"{FIELD_LABELS.get(f, f)}: " + "، ".join(items)
              for f, items in profile.items()]
    for n, match in enumerate(matches, 1):
        lines += ["", f"شغل {n}: {match['job_title']}"]
        for field in match["fields"]:
            line = (f"{field['label']} — {PROFILE_COVER_LABEL}: "
                    + ("، ".join(_located(field)) or "—")
                    + f" / {PROFILE_MISSING_LABEL}: "
                    + ("، ".join(field["missing"]) or "—"))
            if field.get("unknown"):
                line += f" / {PROFILE_UNKNOWN_LABEL}: " + "، ".join(field["unknown"])
            lines.append(line)
        # The record itself, not only the verdict on it: without this the model can say an item was
        # not covered but never what the job holds instead, which is the one thing worth reading.
        if n > PROFILE_RECORD_MATCHES:
            continue
        lines.append(f"{PROFILE_RECORD_LABEL}:")
        for field in match["detail"]["fields"]:
            if field["key"] == "description" or field["primary"]:
                items = field["items"][:PROFILE_CONTEXT_ITEMS] or [field["value"]]
                lines.append(f"  {field['label']}: " + "، ".join(items))
    return "\n".join(lines)


def field_items(field, value):
    if field in PROSE_COLUMNS:
        return []
    return [p.strip() for p in value.split("|")
            if p.strip() and p.strip() not in EMPTY_CELLS]


# A box folds only when that hides two items or more: six items show all six, since a toggle
# that opens a single item costs the reader the click the item itself would have cost the page.
def preview_count(n):
    return n if n <= PREVIEW_ITEMS + 1 else PREVIEW_ITEMS


def job_detail(row, primary_fields, order=None):
    primary = set(primary_fields)
    order = order or {}
    fields = []
    for key in DETAIL_FIELDS:
        value = str(row.get(key, "") or "").strip()
        if value in EMPTY_CELLS:
            continue
        items = field_items(key, value)
        chosen = order.get(key)
        if chosen and len(chosen) == len(items):
            items = chosen
        if key not in PROSE_COLUMNS and not items:
            continue
        fields.append({
            "key": key,
            "label": FIELD_LABELS.get(key, key),
            "value": "، ".join(items) if items else value,
            "items": items,
            "primary": key in primary,
            "preview": preview_count(len(items)),
        })
    # The description always leads, whatever was asked; then the columns the answer used, then the
    # rest. The client and the PDF both draw the boxes in exactly this order.
    fields.sort(key=lambda f: (f["key"] != "description", not f["primary"]))
    return {"job_title": str(row.get("job_title", "") or "").strip(), "fields": fields}

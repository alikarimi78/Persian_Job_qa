from .columns import (DETAIL_FIELDS, EMPTY_CELLS, FIELD_LABELS, PROSE_COLUMNS)
from .config import PREVIEW_ITEMS
from .messages import (DRAFT_HEADER, DRAFT_QUESTION, PROFILE_COVER_LABEL, PROFILE_HEADER,
                       PROFILE_MISSING_LABEL, RELATED_LABEL)


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


def render_draft(draft, related):
    lines = [DRAFT_HEADER, "", f"📌 {FIELD_LABELS['job_title']}: {draft['job_title']}"]
    if draft.get("description"):
        lines.append(f"{FIELD_LABELS['description']}: {draft['description']}")
    if related:
        lines += ["", f"{RELATED_LABEL}: " + "، ".join(related)]
    lines += ["", DRAFT_QUESTION]
    return "\n".join(lines)


def template_profile(matches):
    lines = [PROFILE_HEADER]
    for match in matches:
        lines += ["", f"📌 {match['job_title']}"]
        for field in match["fields"]:
            if field["matched"]:
                lines.append(f"{field['label']} — {PROFILE_COVER_LABEL}: "
                             + "، ".join(field["matched"]))
            if field["missing"]:
                lines.append(f"{field['label']} — {PROFILE_MISSING_LABEL}: "
                             + "، ".join(field["missing"]))
    return "\n".join(lines)


def profile_context(profile, matches):
    lines = ["پروفایل کاربر:"]
    lines += [f"{FIELD_LABELS.get(f, f)}: " + "، ".join(items)
              for f, items in profile.items()]
    for n, match in enumerate(matches, 1):
        lines += ["", f"شغل {n}: {match['job_title']}"]
        for field in match["fields"]:
            lines.append(
                f"{field['label']} — {PROFILE_COVER_LABEL}: "
                + ("، ".join(field["matched"]) or "—")
                + f" / {PROFILE_MISSING_LABEL}: "
                + ("، ".join(field["missing"]) or "—"))
    return "\n".join(lines)


def field_items(field, value):
    if field in PROSE_COLUMNS:
        return []
    return [p.strip() for p in value.split("|")
            if p.strip() and p.strip() not in EMPTY_CELLS]


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
            "preview": min(PREVIEW_ITEMS, len(items)),
        })
    fields.sort(key=lambda f: not f["primary"])
    return {"job_title": str(row.get("job_title", "") or "").strip(), "fields": fields}

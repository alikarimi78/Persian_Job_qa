# -*- coding: utf-8 -*-
"""Turning a record into text or structure: LLM context blocks, the template answers
used whenever the API gives nothing back, and the per-field `details` payload."""

from .columns import (DETAIL_FIELDS, EMPTY_CELLS, FIELD_LABELS, PROSE_COLUMNS)
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
    """Formats the *offer*: the proposal is summarized to its title and one-line
    description, then the user is asked whether to register it. The full record
    travels in `job_draft`, so a client fills its suggestion form from there
    rather than parsing this text back apart."""
    lines = [DRAFT_HEADER, "", f"📌 {FIELD_LABELS['job_title']}: {draft['job_title']}"]
    if draft.get("description"):
        lines.append(f"{FIELD_LABELS['description']}: {draft['description']}")
    if related:
        lines += ["", f"{RELATED_LABEL}: " + "، ".join(related)]
    lines += ["", DRAFT_QUESTION]
    return "\n".join(lines)


def template_profile(matches):
    """The advanced search's answer when the API gave nothing back.

    It prints what the ranking already knows — the jobs, and which of the user's own
    items each one accounted for — rather than apologizing for the missing analysis.
    That is the same bargain every other fallback here makes: the endpoint answers with
    data instead of failing because the API did.
    """
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
    """What the analysis model is shown: the profile, then each job with the comparison
    already made. The model writes prose about this and never re-does the matching —
    see rule 3 of SYSTEM_PROFILE_ANALYZE."""
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
    """Splits a list column into its members. The three PROSE_COLUMNS have none —
    there a comma is punctuation, so the cell is one piece of text."""
    if field in PROSE_COLUMNS:
        return []
    return [p.strip() for p in value.split("|")
            if p.strip() and p.strip() not in EMPTY_CELLS]


def job_detail(row, primary_fields):
    """The record's own columns, structured for the client to render as one box per
    field beside the generated prose. The answer text stays the answer; this is the
    data it was written from, so a user who asked about tools can still open the
    duties box without another request.

    `primary_fields` are the columns the answer actually used — INTENT_TO_FIELDS for
    a question, DISCOVERY_PRIMARY for a described job — and they are flagged and
    sorted first so the client can show those boxes open and fold the rest away.

    Per field: `items` is a list column split apart (empty for prose), and `value` is
    always display-ready text — prose verbatim, a list joined with «،» so a client
    that ignores `items` never shows the raw «|» separator.
    """
    primary = set(primary_fields)
    fields = []
    for key in DETAIL_FIELDS:
        value = str(row.get(key, "") or "").strip()
        if value in EMPTY_CELLS:
            continue
        items = field_items(key, value)
        if key not in PROSE_COLUMNS and not items:      # a list of nothing but «-»
            continue
        fields.append({
            "key": key,
            "label": FIELD_LABELS.get(key, key),
            "value": "، ".join(items) if items else value,
            "items": items,
            "primary": key in primary,
        })
    fields.sort(key=lambda f: not f["primary"])          # stable: primary first, order kept
    return {"job_title": str(row.get("job_title", "") or "").strip(), "fields": fields}

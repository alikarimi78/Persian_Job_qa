import re

_PASSWORD_SPECIAL_RE = re.compile(r"[^A-Za-z0-9]")


# Shared by every schema that takes a password — the account forms in `routers/accounts`
# and the self-service change in `routers/auth`. `manage/Forms.jsx:PASSWORD_RULES` in the
# frontend repeats these rules; change both.
def validate_password_strength(value: str) -> str:
    if (not re.search(r"[a-z]", value) or not re.search(r"[A-Z]", value)
            or not _PASSWORD_SPECIAL_RE.search(value)):
        raise ValueError("Password must contain an uppercase letter, a lowercase "
                         "letter, and a special character (e.g. @)")
    return value


def blank_to_none(value: str | None) -> str | None:
    if value is None:
        return None
    value = value.strip()
    return value or None

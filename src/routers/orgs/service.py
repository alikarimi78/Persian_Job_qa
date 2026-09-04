import base64
import binascii
import re

from fastapi import HTTPException, status
from prisma import Base64, Prisma

from src.models import OrganizationSummary

MAX_LOGO_BYTES = 512 * 1024

_LOGO_SIGNATURES = {
    "image/png": (b"\x89PNG\r\n\x1a\n",),
    "image/jpeg": (b"\xff\xd8\xff",),
    "image/webp": (b"RIFF",),
    "image/gif": (b"GIF87a", b"GIF89a"),
}

_DATA_URI_RE = re.compile(r"^data:(?P<mime>[\w.+-]+/[\w.+-]+);base64,(?P<payload>.+)$",
                          re.DOTALL)


# The declared mime is checked against the magic bytes, and SVG is refused outright —
# it is served back as a data URI, so an SVG here would be stored XSS.
def decode_logo(data_uri: str) -> tuple[str, bytes]:
    match = _DATA_URI_RE.match(data_uri.strip())
    if not match:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                            "Logo must be a base64 data URI, e.g. data:image/png;base64,...")
    mime = match.group("mime").lower()
    if mime not in _LOGO_SIGNATURES:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                            f"Unsupported logo type {mime}; use "
                            f"{', '.join(sorted(_LOGO_SIGNATURES))}")
    try:
        raw = base64.b64decode(match.group("payload"), validate=True)
    except (binascii.Error, ValueError):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Logo is not valid base64")
    if not raw:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Logo is empty")
    if len(raw) > MAX_LOGO_BYTES:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                            f"Logo is {len(raw) // 1024} KB; the limit is "
                            f"{MAX_LOGO_BYTES // 1024} KB")
    if not raw.startswith(_LOGO_SIGNATURES[mime]):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                            f"Logo content does not look like {mime}")
    return mime, raw


# The only writer of either column, which is what makes `models.has_logo` safe to read
# off the cheap `logo_mime` instead of pulling the blob.
def logo_columns(data_uri: str | None) -> dict[str, object]:
    if not data_uri:
        return {"logo": None, "logo_mime": None}
    mime, raw = decode_logo(data_uri)
    return {"logo": Base64.encode(raw), "logo_mime": mime}


def assert_name_free(db: Prisma, name: str, exclude_id: int | None = None) -> None:
    where: dict = {"name": name}
    if exclude_id is not None:
        where["id"] = {"not": exclude_id}
    if OrganizationSummary.prisma(db).find_first(where=where):
        raise HTTPException(status.HTTP_409_CONFLICT, "Organization name already taken")


def get_organization(db: Prisma, organization_id: int) -> OrganizationSummary:
    org = OrganizationSummary.prisma(db).find_unique(where={"id": organization_id})
    if org is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Organization not found")
    return org

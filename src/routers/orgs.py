import base64
import binascii
import re

from fastapi import APIRouter, Depends, HTTPException, status
from prisma import Base64, Prisma

from ..accounts import assert_manages_organization, get_organization
from ..auth import require_roles, require_super_admin
from ..database import get_db
from ..models import OrganizationSummary, Role, User, has_logo
from ..schemas import (OrganizationIn, OrganizationLogoOut, OrganizationOut,
                       OrganizationUpdateIn)

router = APIRouter(prefix="/orgs", tags=["organizations"])

MAX_LOGO_BYTES = 512 * 1024

_LOGO_SIGNATURES = {
    "image/png": (b"\x89PNG\r\n\x1a\n",),
    "image/jpeg": (b"\xff\xd8\xff",),
    "image/webp": (b"RIFF",),
    "image/gif": (b"GIF87a", b"GIF89a"),
}

_DATA_URI_RE = re.compile(r"^data:(?P<mime>[\w.+-]+/[\w.+-]+);base64,(?P<payload>.+)$",
                          re.DOTALL)


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


@router.post("", response_model=OrganizationOut, status_code=201,
             dependencies=[Depends(require_super_admin)])
def create_organization(body: OrganizationIn, db: Prisma = Depends(get_db)):
    assert_name_free(db, body.name)
    return OrganizationSummary.prisma(db).create(data={
        "name": body.name, "code": body.code, "address": body.address,
        "phone": body.phone, "email": body.email, **logo_columns(body.logo)})


@router.get("", response_model=list[OrganizationOut])
def list_organizations(actor: User = Depends(require_roles(Role.super_admin, Role.org_admin)),
                       db: Prisma = Depends(get_db)):
    where = {} if actor.role == Role.super_admin else {"id": actor.organization_id}
    return OrganizationSummary.prisma(db).find_many(where=where, order={"name": "asc"})


@router.get("/{organization_id}", response_model=OrganizationOut)
def get_one(organization_id: int,
            actor: User = Depends(require_roles(Role.super_admin, Role.org_admin)),
            db: Prisma = Depends(get_db)):
    org = get_organization(db, organization_id)
    assert_manages_organization(actor, organization_id)
    return org


@router.get("/{organization_id}/logo", response_model=OrganizationLogoOut)
def get_logo(organization_id: int,
             actor: User = Depends(require_roles(Role.super_admin, Role.org_admin)),
             db: Prisma = Depends(get_db)):
    get_organization(db, organization_id)
    assert_manages_organization(actor, organization_id)
    org = db.organization.find_unique(where={"id": organization_id})
    if org is None or not has_logo(org):
        return OrganizationLogoOut(logo=None)
    return OrganizationLogoOut(logo=f"data:{org.logo_mime};base64,{org.logo}")


@router.patch("/{organization_id}", response_model=OrganizationOut,
              dependencies=[Depends(require_super_admin)])
def update_organization(organization_id: int, body: OrganizationUpdateIn,
                        db: Prisma = Depends(get_db)):
    get_organization(db, organization_id)
    changes = body.model_dump(exclude_unset=True)

    if "name" in changes:
        if changes["name"] is None:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                                "Organization name cannot be cleared")
        assert_name_free(db, changes["name"], exclude_id=organization_id)
    if "logo" in changes:
        changes.update(logo_columns(changes.pop("logo")))
    if not changes:
        return get_organization(db, organization_id)

    return OrganizationSummary.prisma(db).update(where={"id": organization_id},
                                                 data=changes)


@router.delete("/{organization_id}", status_code=204,
               dependencies=[Depends(require_super_admin)])
def delete_organization(organization_id: int, db: Prisma = Depends(get_db)):
    get_organization(db, organization_id)
    accounts = db.user.count(where={"organization_id": organization_id})
    if accounts:
        raise HTTPException(status.HTTP_409_CONFLICT,
                            f"Organization still has {accounts} account(s); delete them first")
    db.organization.delete(where={"id": organization_id})

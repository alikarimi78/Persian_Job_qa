"""Organizations — the top of the tenancy. Only a super_admin creates one."""

import base64
import binascii
import re

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from ..accounts import assert_manages_organization, get_organization
from ..auth import require_roles, require_super_admin
from ..database import get_db
from ..models import Organization, Role, Unit, User
from ..schemas import (OrganizationIn, OrganizationLogoOut, OrganizationOut,
                       OrganizationUpdateIn)

router = APIRouter(prefix="/orgs", tags=["organizations"])

# 512 KB of image is already generous for a masthead logo — the client downscales the
# one it ships to 240px — and the cap is what keeps a row from becoming a way to store
# arbitrary megabytes in the tenancy table.
MAX_LOGO_BYTES = 512 * 1024

# Only raster formats a browser will draw, and each one is checked against its own magic
# bytes below. SVG is deliberately absent: it is a document, it can carry script, and
# this image is served back to an authenticated page — accepting one would turn the
# logo field into stored XSS.
_LOGO_SIGNATURES = {
    "image/png": (b"\x89PNG\r\n\x1a\n",),
    "image/jpeg": (b"\xff\xd8\xff",),
    "image/webp": (b"RIFF",),
    "image/gif": (b"GIF87a", b"GIF89a"),
}

_DATA_URI_RE = re.compile(r"^data:(?P<mime>[\w.+-]+/[\w.+-]+);base64,(?P<payload>.+)$",
                          re.DOTALL)


def decode_logo(data_uri: str) -> tuple[str, bytes]:
    """`data:image/png;base64,…` → `(mime, bytes)`, or a 422 explaining which part failed.

    The declared mime is not taken on trust: it decides how the image is served back, so
    a caller claiming `image/png` for something else would have this endpoint hand that
    content to a browser under a type of their choosing. Checking the magic bytes costs
    one comparison and makes the stored `logo_mime` a fact rather than a claim."""
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


def apply_logo(org: Organization, data_uri: str | None) -> None:
    """The one place `logo` and `logo_mime` are written, so they cannot drift — the
    `has_logo` property answers for the deferred blob by reading the mime, and that is
    only true while nothing sets one without the other. An empty string clears both."""
    if not data_uri:
        org.logo = None
        org.logo_mime = None
        return
    org.logo_mime, org.logo = decode_logo(data_uri)


def assert_name_free(db: Session, name: str, exclude_id: int | None = None) -> None:
    query = db.query(Organization).filter(Organization.name == name)
    if exclude_id is not None:
        query = query.filter(Organization.id != exclude_id)
    if query.first():
        raise HTTPException(status.HTTP_409_CONFLICT, "Organization name already taken")


@router.post("", response_model=OrganizationOut, status_code=201,
             dependencies=[Depends(require_super_admin)])
def create_organization(body: OrganizationIn, db: Session = Depends(get_db)):
    assert_name_free(db, body.name)
    org = Organization(name=body.name, code=body.code, address=body.address,
                       phone=body.phone, email=body.email)
    apply_logo(org, body.logo)
    db.add(org)
    db.commit()
    db.refresh(org)
    return org


@router.get("", response_model=list[OrganizationOut])
def list_organizations(actor: User = Depends(require_roles(Role.super_admin, Role.org_admin)),
                       db: Session = Depends(get_db)):
    query = db.query(Organization)
    if actor.role is Role.org_admin:
        query = query.filter(Organization.id == actor.organization_id)
    return query.order_by(Organization.name).all()


@router.get("/{organization_id}", response_model=OrganizationOut)
def get_one(organization_id: int,
            actor: User = Depends(require_roles(Role.super_admin, Role.org_admin)),
            db: Session = Depends(get_db)):
    org = get_organization(db, organization_id)
    assert_manages_organization(actor, organization_id)
    return org


@router.get("/{organization_id}/logo", response_model=OrganizationLogoOut)
def get_logo(organization_id: int,
             actor: User = Depends(require_roles(Role.super_admin, Role.org_admin)),
             db: Session = Depends(get_db)):
    """The image, kept out of `OrganizationOut` and fetched per row by whoever draws it.

    It answers with a data URI rather than the bytes because every endpoint here needs
    the Authorization header and an `<img src>` cannot send one — this way the client
    asks for it through the same authenticated fetch as everything else. `null` rather
    than a 404 when there is no logo: an organization without one is not an error, and
    the caller renders a placeholder either way."""
    org = get_organization(db, organization_id)
    assert_manages_organization(actor, organization_id)
    if not org.has_logo:
        return OrganizationLogoOut(logo=None)
    encoded = base64.b64encode(org.logo).decode("ascii")
    return OrganizationLogoOut(logo=f"data:{org.logo_mime};base64,{encoded}")


@router.patch("/{organization_id}", response_model=OrganizationOut,
              dependencies=[Depends(require_super_admin)])
def update_organization(organization_id: int, body: OrganizationUpdateIn,
                        db: Session = Depends(get_db)):
    """Editing is the same authority as creating — super_admin only. An org_admin reads
    its organization and staffs it, but does not get to relabel the tenancy it sits in.

    Only the fields actually sent are touched (`exclude_unset`), so this backs both the
    whole edit dialog and a one-box correction, and a client that has never heard of the
    profile columns still renames exactly as it did before. A field sent as `""` or
    `null` is cleared — which is the only way to remove a logo.

    Nothing structural moves: units keep pointing at the same id, and so do the
    accounts. The only way this fails is the name already being someone else's."""
    org = get_organization(db, organization_id)
    changes = body.model_dump(exclude_unset=True)

    if "name" in changes:
        if changes["name"] is None:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                                "Organization name cannot be cleared")
        assert_name_free(db, changes["name"], exclude_id=organization_id)
    if "logo" in changes:
        apply_logo(org, changes.pop("logo"))
    for field, value in changes.items():
        setattr(org, field, value)

    db.commit()
    db.refresh(org)
    return org


@router.delete("/{organization_id}", status_code=204,
               dependencies=[Depends(require_super_admin)])
def delete_organization(organization_id: int, db: Session = Depends(get_db)):
    """Only an empty organization can go: its units and its admin have to be removed
    first, deliberately. Emptying it is the same work either way — this way each
    account and unit is deleted by someone who looked at it, instead of a cascade
    taking out an entire organization's people on one click."""
    org = get_organization(db, organization_id)
    units = db.query(Unit).filter(Unit.organization_id == organization_id).count()
    if units:
        raise HTTPException(status.HTTP_409_CONFLICT,
                            f"Organization still has {units} unit(s); delete them first")
    accounts = db.query(User).filter(User.organization_id == organization_id).count()
    if accounts:
        raise HTTPException(status.HTTP_409_CONFLICT,
                            f"Organization still has {accounts} account(s); delete them first")
    db.delete(org)
    db.commit()

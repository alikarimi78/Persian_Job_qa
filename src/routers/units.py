"""Units — the level between an organization and its people. Created by the
organization's own admin (or by a super_admin, who must say which organization)."""

from fastapi import APIRouter, Depends, HTTPException, status
from prisma import Prisma
from prisma.types import UnitWhereInput

from ..accounts import assert_manages_organization, assert_manages_unit, get_organization, get_unit
from ..auth import require_roles
from ..database import get_db
from ..models import Role, User
from ..schemas import RenameIn, UnitIn, UnitOut

router = APIRouter(prefix="/units", tags=["units"])

_manager = require_roles(Role.super_admin, Role.org_admin)


@router.post("", response_model=UnitOut, status_code=201)
def create_unit(body: UnitIn, actor: User = Depends(_manager), db: Prisma = Depends(get_db)):
    organization_id = body.organization_id
    if actor.role == Role.org_admin:
        # An org_admin's organization is not theirs to choose; naming a different one
        # is a scope error rather than a silent redirect to their own.
        if organization_id is not None and organization_id != actor.organization_id:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Outside your organization")
        organization_id = actor.organization_id
    elif organization_id is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                            "organization_id is required")

    get_organization(db, organization_id)
    assert_manages_organization(actor, organization_id)
    if db.unit.find_first(where={"organization_id": organization_id, "name": body.name}):
        raise HTTPException(status.HTTP_409_CONFLICT,
                            "This organization already has a unit with that name")

    return db.unit.create(data={"name": body.name, "organization_id": organization_id})


@router.get("", response_model=list[UnitOut])
def list_units(organization_id: int | None = None,
               actor: User = Depends(require_roles(Role.super_admin, Role.org_admin,
                                                   Role.unit_admin)),
               db: Prisma = Depends(get_db)):
    """Scoped to what the caller runs: every unit for a super_admin, the organization's
    units for an org_admin, their own single unit for a unit_admin."""
    where: UnitWhereInput = {}
    if actor.role == Role.org_admin:
        where["organization_id"] = actor.organization_id
    elif actor.role == Role.unit_admin:
        where["id"] = actor.unit_id
    if organization_id is not None:
        # Narrows, never widens: an org_admin asking about another organization gets the
        # intersection, which is empty. Overwriting the key is what the chained
        # `.filter()` calls did too, since both compared the same column for equality.
        where = {"AND": [where, {"organization_id": organization_id}]}
    return db.unit.find_many(where=where, order={"name": "asc"})


@router.get("/{unit_id}", response_model=UnitOut)
def get_one(unit_id: int,
            actor: User = Depends(require_roles(Role.super_admin, Role.org_admin,
                                                Role.unit_admin)),
            db: Prisma = Depends(get_db)):
    unit = get_unit(db, unit_id)
    assert_manages_unit(actor, unit)
    return unit


@router.patch("/{unit_id}", response_model=UnitOut)
def rename_unit(unit_id: int, body: RenameIn, actor: User = Depends(_manager),
                db: Prisma = Depends(get_db)):
    """The same two roles that decide a unit exists decide what it is called. A
    unit_admin is left out for the reason they are left out of creation and deletion:
    they staff the unit, they do not define it.

    The unit does not change organization — `organization_id` is not in `RenameIn` at
    all — so the uniqueness question is the one creation asks, inside this organization.
    """
    unit = get_unit(db, unit_id)
    assert_manages_unit(actor, unit)
    clash = db.unit.find_first(where={"organization_id": unit.organization_id,
                                      "name": body.name,
                                      "id": {"not": unit_id}})
    if clash:
        raise HTTPException(status.HTTP_409_CONFLICT,
                            "This organization already has a unit with that name")
    return db.unit.update(where={"id": unit_id}, data={"name": body.name})


@router.delete("/{unit_id}", status_code=204)
def delete_unit(unit_id: int, actor: User = Depends(_manager), db: Prisma = Depends(get_db)):
    """Only an empty unit can go — its admin included. Accounts are never deleted as a
    side effect of deleting their container: move them (`POST /accounts/{id}/unit`) or
    delete them first, so each one is a decision someone actually made.

    Closed to a unit_admin, like creating a unit: they staff the unit, they do not
    decide whether it exists.
    """
    unit = get_unit(db, unit_id)
    assert_manages_unit(actor, unit)
    accounts = db.user.count(where={"unit_id": unit_id})
    if accounts:
        raise HTTPException(status.HTTP_409_CONFLICT,
                            f"Unit still has {accounts} account(s); move or delete them first")
    db.unit.delete(where={"id": unit_id})

"""Units — the level between an organization and its people. Created by the
organization's own admin (or by a super_admin, who must say which organization)."""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from ..accounts import assert_manages_organization, assert_manages_unit, get_organization, get_unit
from ..auth import require_roles
from ..database import get_db
from ..models import Role, Unit, User
from ..schemas import UnitIn, UnitOut

router = APIRouter(prefix="/units", tags=["units"])

_manager = require_roles(Role.super_admin, Role.org_admin)


@router.post("", response_model=UnitOut, status_code=201)
def create_unit(body: UnitIn, actor: User = Depends(_manager), db: Session = Depends(get_db)):
    organization_id = body.organization_id
    if actor.role is Role.org_admin:
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
    if db.query(Unit).filter(Unit.organization_id == organization_id,
                             Unit.name == body.name).first():
        raise HTTPException(status.HTTP_409_CONFLICT,
                            "This organization already has a unit with that name")

    unit = Unit(name=body.name, organization_id=organization_id)
    db.add(unit)
    db.commit()
    db.refresh(unit)
    return unit


@router.get("", response_model=list[UnitOut])
def list_units(organization_id: int | None = None,
               actor: User = Depends(require_roles(Role.super_admin, Role.org_admin,
                                                   Role.unit_admin)),
               db: Session = Depends(get_db)):
    """Scoped to what the caller runs: every unit for a super_admin, the organization's
    units for an org_admin, their own single unit for a unit_admin."""
    query = db.query(Unit)
    if actor.role is Role.org_admin:
        query = query.filter(Unit.organization_id == actor.organization_id)
    elif actor.role is Role.unit_admin:
        query = query.filter(Unit.id == actor.unit_id)
    if organization_id is not None:
        query = query.filter(Unit.organization_id == organization_id)
    return query.order_by(Unit.name).all()


@router.get("/{unit_id}", response_model=UnitOut)
def get_one(unit_id: int,
            actor: User = Depends(require_roles(Role.super_admin, Role.org_admin,
                                                Role.unit_admin)),
            db: Session = Depends(get_db)):
    unit = get_unit(db, unit_id)
    assert_manages_unit(actor, unit)
    return unit

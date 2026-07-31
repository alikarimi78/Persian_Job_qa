"""Organizations — the top of the tenancy. Only a super_admin creates one."""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from ..accounts import assert_manages_organization, get_organization
from ..auth import require_roles, require_super_admin
from ..database import get_db
from ..models import Organization, Role, User
from ..schemas import OrganizationIn, OrganizationOut

router = APIRouter(prefix="/orgs", tags=["organizations"])


@router.post("", response_model=OrganizationOut, status_code=201,
             dependencies=[Depends(require_super_admin)])
def create_organization(body: OrganizationIn, db: Session = Depends(get_db)):
    if db.query(Organization).filter(Organization.name == body.name).first():
        raise HTTPException(status.HTTP_409_CONFLICT, "Organization name already taken")
    org = Organization(name=body.name)
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

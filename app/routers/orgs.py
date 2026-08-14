"""Organizations — the top of the tenancy. Only a super_admin creates one."""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from ..accounts import assert_manages_organization, get_organization
from ..auth import require_roles, require_super_admin
from ..database import get_db
from ..models import Organization, Role, Unit, User
from ..schemas import OrganizationIn, OrganizationOut, RenameIn

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


@router.patch("/{organization_id}", response_model=OrganizationOut,
              dependencies=[Depends(require_super_admin)])
def rename_organization(organization_id: int, body: RenameIn, db: Session = Depends(get_db)):
    """Renaming is the same authority as creating — super_admin only. An org_admin reads
    its organization and staffs it, but does not get to relabel the tenancy it sits in.

    Nothing else moves with the name: units keep pointing at the same id, and so do the
    accounts. The only way this fails is the name already being someone else's."""
    org = get_organization(db, organization_id)
    clash = db.query(Organization).filter(Organization.name == body.name,
                                          Organization.id != organization_id).first()
    if clash:
        raise HTTPException(status.HTTP_409_CONFLICT, "Organization name already taken")
    org.name = body.name
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

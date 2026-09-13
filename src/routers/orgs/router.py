from fastapi import APIRouter, Depends, HTTPException, status
from prisma import Prisma

from src.database import get_db
from src.engine_manager import manager
from src.models import JobStatus, OrganizationSummary, Role, User, has_logo
from src.permissions import assert_manages_organization
from src.security import require_roles, require_super_admin

from .schemas import (OrganizationIn, OrganizationLogoOut, OrganizationOut,
                      OrganizationRow, OrganizationUpdateIn)
from .service import (assert_name_free, get_organization, logo_columns,
                      with_job_counts)

router = APIRouter(prefix="/orgs", tags=["organizations"])


@router.post("", response_model=OrganizationOut, status_code=201,
             dependencies=[Depends(require_super_admin)])
def create_organization(body: OrganizationIn, db: Prisma = Depends(get_db)):
    assert_name_free(db, body.name)
    return OrganizationSummary.prisma(db).create(data={
        "name": body.name, "code": body.code, "address": body.address,
        "phone": body.phone, "email": body.email, **logo_columns(body.logo)})


@router.get("", response_model=list[OrganizationRow])
def list_organizations(actor: User = Depends(require_roles(Role.super_admin, Role.org_admin)),
                       db: Prisma = Depends(get_db)):
    where = {} if actor.role == Role.super_admin else {"id": actor.organization_id}
    orgs = OrganizationSummary.prisma(db).find_many(where=where, order={"name": "asc"})
    return with_job_counts(db, orgs)


@router.get("/{organization_id}", response_model=OrganizationRow)
def get_one(organization_id: int,
            actor: User = Depends(require_roles(Role.super_admin, Role.org_admin)),
            db: Prisma = Depends(get_db)):
    org = get_organization(db, organization_id)
    assert_manages_organization(actor, organization_id)
    return with_job_counts(db, [org])[0]


# The blob is kept out of every read but this one, which is why the list model is the
# `OrganizationSummary` partial.
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


# Only the fields actually sent are applied: "" clears a field, an absent one is left
# alone, which is how the client leaves `logo` out of a PATCH that never opened the
# picker.
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


# The accounts must be gone first, as they always had to be. The organization's own job
# records go *with* it — no other organization can search them, so there is nobody to
# hand them over to — and they are deleted here, in the organization's own transaction,
# rather than by an `ON DELETE CASCADE` that a reader of the schema would not expect.
# A record meant to outlive the organization is made public before the delete, through
# `PUT /admin/jobs/{id}`.
@router.delete("/{organization_id}", status_code=204,
               dependencies=[Depends(require_super_admin)])
def delete_organization(organization_id: int, db: Prisma = Depends(get_db)):
    get_organization(db, organization_id)
    accounts = db.user.count(where={"organization_id": organization_id})
    if accounts:
        raise HTTPException(status.HTTP_409_CONFLICT,
                            f"Organization still has {accounts} account(s); delete them first")

    in_corpus = db.jobrecord.count(where={"organization_id": organization_id,
                                          "status": JobStatus.approved})
    with db.tx() as tx:
        tx.jobrecord.delete_many(where={"organization_id": organization_id})
        tx.organization.delete(where={"id": organization_id})
    if in_corpus:
        manager.rebuild_async()

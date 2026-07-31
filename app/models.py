import enum
from datetime import datetime

from sqlalchemy import (Boolean, CheckConstraint, DateTime, Enum, ForeignKey, Index, String,
                        Text, UniqueConstraint, func, text)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base


class Role(str, enum.Enum):
    """Who a user is, in a two-level tenancy: a user sits in a unit, a unit sits in
    an organization. Each level is provisioned by the level above it — there is no
    self-registration, so every account exists because someone with authority made it.

    super_admin  everything, including creating organizations and further super admins
    org_admin    one per organization; creates that organization's units and their admins
    unit_admin   one per unit; creates the ordinary users of that unit
    user         uses the system (search, job suggestions); provisions nobody
    """
    user = "user"
    unit_admin = "unit_admin"
    org_admin = "org_admin"
    super_admin = "super_admin"


class JobStatus(str, enum.Enum):
    approved = "approved"
    pending = "pending"
    rejected = "rejected"


class Organization(Base):
    __tablename__ = "organizations"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    units: Mapped[list["Unit"]] = relationship(back_populates="organization")


class Unit(Base):
    __tablename__ = "units"
    # Unit names only have to be unique inside their own organization
    __table_args__ = (UniqueConstraint("organization_id", "name", name="uq_units_org_name"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(128), index=True)
    organization_id: Mapped[int] = mapped_column(ForeignKey("organizations.id"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    organization: Mapped[Organization] = relationship(back_populates="units")


class User(Base):
    __tablename__ = "users"
    __table_args__ = (
        # Which scope column a role carries. Note the last branch: role='user' may have
        # a NULL unit_id, because rows that predate this hierarchy have no unit to point
        # at. New users always get one — `accounts.create_account` requires it — so the
        # slack is for legacy rows only.
        CheckConstraint(
            "(role = 'super_admin' AND organization_id IS NULL AND unit_id IS NULL) OR "
            "(role = 'org_admin'   AND organization_id IS NOT NULL AND unit_id IS NULL) OR "
            "(role = 'unit_admin'  AND organization_id IS NULL AND unit_id IS NOT NULL) OR "
            "(role = 'user'        AND organization_id IS NULL)",
            name="ck_users_scope"),
        # "One admin per organization" and "one admin per unit" as partial unique
        # indexes: ordinary users share a unit_id freely, only the admin row is capped.
        Index("uq_users_org_admin", "organization_id", unique=True,
              postgresql_where=text("role = 'org_admin'")),
        Index("uq_users_unit_admin", "unit_id", unique=True,
              postgresql_where=text("role = 'unit_admin'")),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    # Global, not per-tenant: login is by username alone, so it has to identify one row
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    hashed_password: Mapped[str] = mapped_column(String(128))
    role: Mapped[Role] = mapped_column(Enum(Role), default=Role.user)
    # Blocked accounts keep everything they own (suggestions, their place in a unit) and
    # simply cannot authenticate. Checked on login *and* on every request, so an already
    # issued token dies with the block rather than lasting out its hour.
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("true"))
    # Set for org_admin only; a unit_admin's or user's organization is reached through
    # their unit, so it is never stored twice and cannot drift.
    organization_id: Mapped[int | None] = mapped_column(
        ForeignKey("organizations.id"), nullable=True, index=True)
    unit_id: Mapped[int | None] = mapped_column(
        ForeignKey("units.id"), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    organization: Mapped[Organization | None] = relationship()
    unit: Mapped[Unit | None] = relationship()

    @property
    def scope_organization_id(self) -> int | None:
        """The organization this account belongs to, wherever it is recorded.
        None for a super_admin (who belongs to all of them) and for a legacy user."""
        if self.organization_id is not None:
            return self.organization_id
        return self.unit.organization_id if self.unit is not None else None


class JobRecord(Base):
    __tablename__ = "jobs_info"

    id: Mapped[int] = mapped_column(primary_key=True)
    job_title: Mapped[str] = mapped_column(String(255), index=True)
    aliases: Mapped[str] = mapped_column(Text, default="")
    tools: Mapped[str] = mapped_column(Text, default="")
    skills: Mapped[str] = mapped_column(Text, default="")
    knowledge: Mapped[str] = mapped_column(Text, default="")
    abilities: Mapped[str] = mapped_column(Text, default="")
    work_context: Mapped[str] = mapped_column(Text, default="")
    career_path_next: Mapped[str] = mapped_column(Text, default="")
    description: Mapped[str] = mapped_column(Text, default="")
    responsibilities: Mapped[str] = mapped_column(Text, default="")

    status: Mapped[JobStatus] = mapped_column(Enum(JobStatus), default=JobStatus.pending, index=True)
    # SET NULL, not CASCADE: a job record belongs to the corpus, not to the person who
    # proposed it. Deleting an account drops the attribution and keeps the record —
    # which is also what lets an account be deleted at all once it has suggested one.
    suggested_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    reviewed_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())

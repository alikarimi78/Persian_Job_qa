import enum
from datetime import datetime

from sqlalchemy import String, Text, Enum, ForeignKey, DateTime, func
from sqlalchemy.orm import Mapped, mapped_column

from .database import Base


class Role(str, enum.Enum):
    user = "user"
    admin = "admin"


class JobStatus(str, enum.Enum):
    approved = "approved"
    pending = "pending"
    rejected = "rejected"


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    hashed_password: Mapped[str] = mapped_column(String(128))
    role: Mapped[Role] = mapped_column(Enum(Role), default=Role.user)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class JobRecord(Base):
    __tablename__ = "jobs_info"

    id: Mapped[int] = mapped_column(primary_key=True)
    job_title: Mapped[str] = mapped_column(String(255), index=True)
    aliases: Mapped[str] = mapped_column(Text, default="")
    tools: Mapped[str] = mapped_column(Text, default="")
    skills: Mapped[str] = mapped_column(Text, default="")
    work_context: Mapped[str] = mapped_column(Text, default="")
    career_path_next: Mapped[str] = mapped_column(Text, default="")
    description: Mapped[str] = mapped_column(Text, default="")
    responsibilities: Mapped[str] = mapped_column(Text, default="")

    status: Mapped[JobStatus] = mapped_column(Enum(JobStatus), default=JobStatus.pending, index=True)
    suggested_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    reviewed_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())

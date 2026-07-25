import enum
from datetime import UTC, datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


def utc_now():
    return datetime.now(UTC).replace(tzinfo=None)


class OrgType(enum.Enum):
    PE_FIRM = "PE Firm"
    FAMILY_OFFICE = "Family Office"


class Organisation(Base):
    __tablename__ = "organisation"

    id: Mapped[int] = mapped_column(Integer, index=True, primary_key=True, nullable=False)
    clerk_org_id: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    # Nullable: auto-provisioned orgs (first login via Clerk) only get a type if the
    # Clerk org's public_metadata.type is set; the future admin UI fills the rest.
    type: Mapped[OrgType | None] = mapped_column(SAEnum(OrgType), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)


class Funds(Base):
    __tablename__ = "funds"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True, nullable=False)
    org_id: Mapped[int] = mapped_column(
        Integer, ForeignKey(Organisation.id), index=True, nullable=False
    )
    name: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    strategy: Mapped[str | None] = mapped_column(String(100), nullable=True)
    vintage_year: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(50), nullable=False)


class Users(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True, nullable=False)
    org_id: Mapped[int] = mapped_column(
        Integer, ForeignKey(Organisation.id), index=True, nullable=False
    )
    # Nullable: Clerk's session token carries no name/email, so JIT-provisioned users
    # start with NULLs; the frontend fills them via POST /auth/sync-profile right after
    # login (same two-step pattern as simpero_GOV_AI).
    name: Mapped[str | None] = mapped_column(String(100), nullable=True, index=True)
    email: Mapped[str | None] = mapped_column(String(100), nullable=True, unique=True, index=True)
    role: Mapped[str] = mapped_column(String(50), nullable=False)
    login_method: Mapped[str] = mapped_column(String(50), nullable=False)
    clerk_user_id: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    clerk_org_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(50), nullable=False, default="active")
    deactivated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

import enum
from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, Integer, String
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


def utc_now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


class OrgType(enum.Enum):
    PE_FIRM = "PE Firm"
    FAMILY_OFFICE = "Family Office"


class Organisation(Base):
    __tablename__ = "organisation"

    id: Mapped[int] = mapped_column(
        Integer, index=True, primary_key=True, nullable=False
    )
    clerk_org_id: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    type: Mapped[OrgType] = mapped_column(SAEnum(OrgType), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)


class Funds(Base):
    __tablename__ = "funds"

    id: Mapped[int] = mapped_column(
        Integer, primary_key=True, index=True, nullable=False
    )
    org_id: Mapped[int] = mapped_column(
        Integer, ForeignKey(Organisation.id), index=True, nullable=False
    )
    name: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    vintage_year: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(50), nullable=False)

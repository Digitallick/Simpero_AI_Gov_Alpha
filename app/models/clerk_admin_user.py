import enum
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.organisation import Organisation, utc_now


class AdminType(enum.Enum):
    # Member names are lowercase (unlike OrgType) so SQLAlchemy's default
    # name-based Enum persistence matches the migration's literal
    # sa.Enum("platform", "client", name="admintype") pg labels exactly.
    platform = "platform"
    client = "client"


class ClerkAdminUser(Base):
    """Admin identity + authorization source of truth for the /api/admin
    portal — a tenant table (same org_isolation RLS shape as `users`), kept
    separate from `users` because client admins are admin-only and never get
    a product `users` row. Guards authorize off this table, not the JWT
    org_role (see app/core/admin_dependencies.py)."""

    __tablename__ = "clerk_admin_users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True, nullable=False)
    # The guard lookup key; unique so JIT provisioning (on_conflict_do_nothing)
    # and deactivate() have a single row to target per human.
    clerk_user_id: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    # RLS discriminator — org_isolation policy keys on this, identical to `users`.
    clerk_org_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    org_id: Mapped[int] = mapped_column(
        Integer, ForeignKey(Organisation.id), nullable=False, index=True
    )
    # Denormalized from Clerk (session token carries no email); non-unique so
    # the same human can later also appear in `users` (see plan's BACKLOG).
    email: Mapped[str | None] = mapped_column(String(100), nullable=True, index=True)
    admin_type: Mapped[AdminType] = mapped_column(
        SAEnum(AdminType, name="admintype"), nullable=False
    )
    # Lets an admin be deactivated without deletion (audit continuity). The
    # D3 downgrade-only sync flips this to "inactive"; nothing re-activates it.
    status: Mapped[str] = mapped_column(String(50), nullable=False, default="active")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)
    # Nullable, no backfill: NULL on every pre-existing row means "never
    # touched by the reactivate path" -> the D3 grace window never applies to
    # them. onupdate is column-level so it's applied for free on plain Core
    # update() statements (e.g. AdminUserRepo.deactivate) — pg_insert(...)
    # .on_conflict_do_update() is an INSERT, not an UPDATE, so that path
    # (reactivate_or_create) sets it explicitly instead.
    updated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, onupdate=utc_now)

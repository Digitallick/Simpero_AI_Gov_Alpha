# import uuid
# from datetime import datetime

# from sqlalchemy import DateTime, String, func
# from sqlalchemy.dialects.postgresql import JSONB, UUID
# from sqlalchemy.orm import Mapped, mapped_column

# from app.core.database import Base

# # INSERT-only enforcement is at the database layer:
# #   REVOKE UPDATE, DELETE ON audit_log FROM dd_app;
# # Do NOT add application-level guards here — they can be bypassed by another code path
# # or a future developer and would give false assurance of immutability. DB-level grant
# # revocation is the only reliable mechanism when the runtime role is dd_app.


# class AuditLog(Base):
#     __tablename__ = "audit_log"

#     id: Mapped[uuid.UUID] = mapped_column(
#         UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid()
#     )
#     tenant_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
#     user_id: Mapped[str] = mapped_column(String, nullable=False)
#     action: Mapped[str] = mapped_column(String, nullable=False)
#     resource_type: Mapped[str | None] = mapped_column(String, nullable=True)
#     resource_id: Mapped[str | None] = mapped_column(String, nullable=True)
#     payload: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
#     # No updated_at — audit log rows are write-once by definition.
#     created_at: Mapped[datetime] = mapped_column(
#         DateTime(timezone=True), server_default=func.now(), nullable=False
#     )

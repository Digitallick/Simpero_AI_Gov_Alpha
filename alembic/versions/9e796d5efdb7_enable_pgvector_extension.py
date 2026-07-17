"""enable pgvector extension

Puts the pgvector extension into the migration chain so the schema is
reproducible from code. The extension was already present on the live `simpero`
database, but created out of band (run by hand, not by any migration), so a
fresh environment -- the local sandbox, CI, a new cluster -- would not have it.
That is a latent trap: the day a `chunks` table (or any vector column) is built,
its migration fails everywhere the extension was not hand-created.

CREATE EXTENSION IF NOT EXISTS makes this a no-op on `simpero` (already there,
owned by postgres -- untouched) and effective everywhere else. It is deliberately
independent of any table: nothing uses vector yet (SIM-35 / DS-A7 hybrid indexing
was cancelled), so this only reconciles code with the deployed reality and clears
the way for a future chunks migration to assume the extension exists.

Revision ID: 9e796d5efdb7
Revises: 60a151dd80b0
Create Date: 2026-07-17 14:25:01.678689

"""

from collections.abc import Sequence
from typing import Union

from alembic import op

revision: str = "9e796d5efdb7"
down_revision: str | None = "60a151dd80b0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")


def downgrade() -> None:
    # Drops the extension regardless of who created it. Safe only while nothing
    # depends on it -- Postgres refuses the drop if any vector column exists, so
    # this cannot silently break a chunks table added by a later migration.
    op.execute("DROP EXTENSION IF EXISTS vector")

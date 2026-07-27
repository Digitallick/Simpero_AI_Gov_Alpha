"""merge chunks and corroboration heads

Two migrations were opened against the same parent (920070316626) on separate
branches and never linearized: 6c8bc5907f94 (chunks table, Epic 8) and
e960a3366cf7 (corroboration_events log, FS-A-CORR-1). Both are children of
920070316626, so on staging the revision graph has two heads and
`alembic upgrade head` refuses with "Multiple head revisions are present" --
failing the migration step of every deploy and every PR into staging.

This is an empty merge revision: no schema change, it only rejoins the two
lineages into one head so the graph is linear again. The branches touch
different tables (chunks vs corroboration_events), so there is nothing to
reconcile here -- only to re-converge.

Revision ID: 2de114717b91
Revises: 6c8bc5907f94, e960a3366cf7
Create Date: 2026-07-27 12:37:38.601556

"""

from collections.abc import Sequence

revision: str = "2de114717b91"
down_revision: str | Sequence[str] | None = ("6c8bc5907f94", "e960a3366cf7")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass

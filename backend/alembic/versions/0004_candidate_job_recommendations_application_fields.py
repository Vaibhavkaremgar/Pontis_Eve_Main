"""Add application fields to candidate_job_recommendations

Revision ID: 0004_cjr_application_fields
Revises: 0002_candidate_notification_event_id
Create Date: 2026-01-03 00:00:00.000000
"""
from typing import Sequence, Union
from alembic import op

revision: str = "0004_cjr_application_fields"
down_revision: Union[str, None] = "0002_candidate_notification_event_id"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("""
        ALTER TABLE candidate_job_recommendations
        ADD COLUMN IF NOT EXISTS agency_id  UUID          NULL,
        ADD COLUMN IF NOT EXISTS job_role   TEXT          NULL,
        ADD COLUMN IF NOT EXISTS status     VARCHAR(50)   NULL,
        ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ   NULL
    """)

    # Index for fast per-candidate application lookups
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_cjr_candidate_status
        ON candidate_job_recommendations (candidate_id, status)
        WHERE status IS NOT NULL
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_cjr_candidate_status")
    op.execute("""
        ALTER TABLE candidate_job_recommendations
        DROP COLUMN IF EXISTS agency_id,
        DROP COLUMN IF EXISTS job_role,
        DROP COLUMN IF EXISTS status,
        DROP COLUMN IF EXISTS updated_at
    """)

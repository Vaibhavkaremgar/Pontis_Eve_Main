"""candidate_activity_feed event_id and indexes

Revision ID: 0002_candidate_notification_event_id
Revises: 0001_adam_eve_contract
Create Date: 2026-01-02 00:00:00.000000

"""
from typing import Sequence, Union
from alembic import op

revision: str = "0002_candidate_notification_event_id"
down_revision: Union[str, None] = "0001_adam_eve_contract"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Guard: only apply if candidate_activity_feed exists
    op.execute("""
        DO $$ BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM information_schema.tables
                WHERE table_name = 'candidate_activity_feed'
            ) THEN
                RAISE EXCEPTION 'candidate_activity_feed does not exist';
            END IF;
        END $$
    """)

    op.execute("""
        ALTER TABLE candidate_activity_feed
        ADD COLUMN IF NOT EXISTS event_id UUID NULL
    """)

    op.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS uq_caf_event_id
        ON candidate_activity_feed(event_id)
        WHERE event_id IS NOT NULL
    """)

    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_caf_candidate_type
        ON candidate_activity_feed(candidate_id, activity_type)
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_caf_candidate_type")
    op.execute("DROP INDEX IF EXISTS uq_caf_event_id")
    op.execute("""
        ALTER TABLE candidate_activity_feed
        DROP COLUMN IF EXISTS event_id
    """)

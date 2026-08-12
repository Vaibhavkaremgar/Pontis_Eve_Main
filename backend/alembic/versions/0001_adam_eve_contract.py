"""adam_event_id and eve_outbound_events

Revision ID: 0001_adam_eve_contract
Revises:
Create Date: 2026-01-01 00:00:00.000000

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = "0001_adam_eve_contract"
down_revision: Union[str, None] = "c45801ffb70c"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Add adam_event_id to recruiter_interest_requests (idempotent)
    op.execute("""
        ALTER TABLE recruiter_interest_requests
        ADD COLUMN IF NOT EXISTS adam_event_id UUID NULL
    """)

    # 2. Unique constraint on adam_event_id (idempotent)
    op.execute("""
        DO $$ BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_constraint
                WHERE conname = 'uq_rir_adam_event_id'
            ) THEN
                ALTER TABLE recruiter_interest_requests
                ADD CONSTRAINT uq_rir_adam_event_id UNIQUE (adam_event_id);
            END IF;
        END $$
    """)

    # 3. Create eve_outbound_events with full retry/status fields
    op.execute("""
        CREATE TABLE IF NOT EXISTS eve_outbound_events (
            id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            eve_event_id    UUID NOT NULL,
            adam_event_id   UUID NOT NULL,
            candidate_id    UUID NOT NULL,
            job_id          UUID NOT NULL,
            agency_id       UUID NOT NULL,
            response        TEXT NOT NULL,
            status          VARCHAR(20) NOT NULL DEFAULT 'pending',
            attempt_count   INTEGER NOT NULL DEFAULT 0,
            last_error      TEXT NULL,
            next_retry_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
            delivered_at    TIMESTAMPTZ NULL,
            created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT uq_eoe_eve_event_id UNIQUE (eve_event_id)
        )
    """)

    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_eoe_status_retry
        ON eve_outbound_events (status, next_retry_at)
        WHERE status = 'pending'
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_eoe_status_retry")
    op.execute("DROP TABLE IF EXISTS eve_outbound_events")
    op.execute("""
        ALTER TABLE recruiter_interest_requests
        DROP CONSTRAINT IF EXISTS uq_rir_adam_event_id
    """)
    op.execute("""
        ALTER TABLE recruiter_interest_requests
        DROP COLUMN IF EXISTS adam_event_id
    """)

"""Unique constraint on job_descriptions(ats_type, ats_job_id)

Revision ID: 0003_job_descriptions_ats_unique
Revises: 0001_adam_eve_contract
Create Date: 2026-01-01 00:00:00.000000
"""
from typing import Sequence, Union
from alembic import op

revision: str = "0003_job_descriptions_ats_unique"
down_revision: Union[str, None] = "0001_adam_eve_contract"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("""
        DO $$ BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_constraint
                WHERE conname = 'uq_job_descriptions_ats_type_ats_job_id'
            ) THEN
                ALTER TABLE job_descriptions
                ADD CONSTRAINT uq_job_descriptions_ats_type_ats_job_id
                UNIQUE (ats_type, ats_job_id);
            END IF;
        END $$
    """)


def downgrade() -> None:
    op.execute("""
        ALTER TABLE job_descriptions
        DROP CONSTRAINT IF EXISTS uq_job_descriptions_ats_type_ats_job_id
    """)

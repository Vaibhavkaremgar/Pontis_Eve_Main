"""merge migration heads

Revision ID: 7b1c30901cbf
Revises: 0003_job_descriptions_ats_unique, 0004_cjr_application_fields
Create Date: 2026-08-13 15:42:27.929661

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa


revision: str = '7b1c30901cbf'
down_revision: Union[str, None] = ('0003_job_descriptions_ats_unique', '0004_cjr_application_fields')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass

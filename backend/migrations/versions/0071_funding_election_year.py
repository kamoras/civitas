"""Members: the election their funding figures are from

`senators.funding_election_year` and `representatives.funding_election_year`
record the election the funding window holds (fetch/fec.
select_recent_elections), so the profile can name it. NULL, every row
stored before this, until the member's next scoring run.

Adds nullable columns only. The previous image never reads them and its
writes leave them as they are.

Revision ID: 0071
Revises: 0070 (0070_issue_member_link_stamp)
Create Date: 2026-10-22
"""
from alembic import op
import sqlalchemy as sa


revision = '0071'
down_revision = '0070'
branch_labels = None
depends_on = None


def upgrade() -> None:
    for table in ('senators', 'representatives'):
        with op.batch_alter_table(table, schema=None) as batch_op:
            batch_op.add_column(sa.Column('funding_election_year', sa.Integer(), nullable=True))


def downgrade() -> None:
    for table in ('senators', 'representatives'):
        with op.batch_alter_table(table, schema=None) as batch_op:
            batch_op.drop_column('funding_election_year')

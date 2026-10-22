"""Action Center issues: the rule their members were matched under

`action_issues.member_link_stamp` records the fingerprint of the member
linking rule (action_center.member_link_stamp) a row's related_senators and
related_officials were matched under, and `member_link_texts` the reporting
it read. The refresh matches a row with another stamp again
(relink_issue_members). NULL, every row stored before this, is matched
again on the next refreshes.

Adds nullable columns only. The previous image never reads them and its
inserts leave them NULL, which the next refresh re-matches.

Revision ID: 0070
Revises: 0069 (0069_coverage_caption_checked)
Create Date: 2026-10-21
"""
from alembic import op
import sqlalchemy as sa


revision = '0070'
down_revision = '0069'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table('action_issues', schema=None) as batch_op:
        batch_op.add_column(sa.Column('member_link_stamp', sa.String(length=16), nullable=True))
        batch_op.add_column(sa.Column('member_link_texts', sa.Text(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('action_issues', schema=None) as batch_op:
        batch_op.drop_column('member_link_texts')
        batch_op.drop_column('member_link_stamp')

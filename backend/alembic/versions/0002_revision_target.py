"""Distinguish DNS-policy revisions from client-bound record revisions.

UniFi keeps client local DNS records on the client object, not in the DNS
store, so applying and rolling back a revision needs to know which API it
targets.

Revision ID: 0002
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

target_enum = sa.Enum("dns_policy", "client_record", name="revision_target")


def upgrade() -> None:
    target_enum.create(op.get_bind(), checkfirst=True)
    op.add_column(
        "record_revisions",
        sa.Column(
            "target", target_enum, nullable=False, server_default="dns_policy"
        ),
    )


def downgrade() -> None:
    op.drop_column("record_revisions", "target")
    target_enum.drop(op.get_bind(), checkfirst=True)

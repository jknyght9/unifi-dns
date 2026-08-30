"""Widen flow string columns.

The initial widths were guesses from a 50-row sample. A full 14k-flow pull
overflowed one of them (asyncpg StringDataRightTruncation on varchar(32)).
Nothing here is worth a tight bound, so give every short text column room and
let the collector truncate defensively as a backstop.

Revision ID: 0004
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None

WIDEN = {
    "action": 64, "service": 128, "protocol": 32, "risk": 32,
    "src_mac": 64, "src_ip": 128, "dst_ip": 128,
    "src_network": 255, "src_zone": 255,
    "policy_type": 128, "policy_internal_type": 128, "policy_name": 255,
    "src_name": 512, "dst_name": 512,
}


def upgrade() -> None:
    for col, size in WIDEN.items():
        op.alter_column("flows", col, type_=sa.String(size), existing_nullable=True)


def downgrade() -> None:
    pass

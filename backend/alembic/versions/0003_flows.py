"""Flow telemetry table.

Revision ID: 0003
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "flows",
        sa.Column("id", sa.String(96), primary_key=True),
        sa.Column("ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column("action", sa.String(24)),
        sa.Column("service", sa.String(32)),
        sa.Column("protocol", sa.String(16)),
        sa.Column("risk", sa.String(16)),
        sa.Column("count", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("src_ip", sa.String(64)),
        sa.Column("src_mac", sa.String(32)),
        sa.Column("src_name", sa.String(255)),
        sa.Column("src_network", sa.String(128)),
        sa.Column("src_zone", sa.String(128)),
        sa.Column("dst_ip", sa.String(64)),
        sa.Column("dst_port", sa.Integer()),
        sa.Column("dst_name", sa.String(255)),
        sa.Column("domains", postgresql.ARRAY(sa.Text())),
        sa.Column("policy_type", sa.String(48)),
        sa.Column("policy_internal_type", sa.String(48)),
        sa.Column("policy_name", sa.String(128)),
        sa.Column("bytes_total", sa.BigInteger()),
        sa.Column("sinkholed", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("raw", postgresql.JSONB()),
    )
    for col in ("ts", "action", "service", "src_ip", "src_network",
                "dst_ip", "dst_port", "policy_type", "policy_internal_type", "sinkholed"):
        op.create_index(f"ix_flows_{col}", "flows", [col])
    op.create_index("ix_flows_ts_action", "flows", ["ts", "action"])
    op.create_index("ix_flows_src_ts", "flows", ["src_ip", "ts"])
    op.create_index("ix_flows_domains", "flows", ["domains"], postgresql_using="gin")


def downgrade() -> None:
    op.drop_table("flows")

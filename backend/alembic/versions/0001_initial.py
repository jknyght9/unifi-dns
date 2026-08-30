"""Initial schema: records mirror, changesets, revisions, apexes.

Revision ID: 0001
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

op_enum = sa.Enum("create", "update", "delete", name="revision_op")
status_enum = sa.Enum(
    "pending", "applied", "failed", "partial", name="change_set_status"
)
source_enum = sa.Enum(
    "ui", "import", "reconcile", "rollback", "api", name="change_set_source"
)


def upgrade() -> None:
    op.create_table(
        "apexes",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(255), nullable=False, unique=True),
        sa.Column("description", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_apexes_name", "apexes", ["name"])

    op.create_table(
        "dns_records",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("unifi_id", sa.String(64), unique=True),
        sa.Column("record_type", sa.String(32), nullable=False),
        sa.Column("fqdn", sa.String(512), nullable=False),
        sa.Column("apex", sa.String(255)),
        sa.Column("value", sa.Text(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("ttl_seconds", sa.Integer()),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_dns_records_unifi_id", "dns_records", ["unifi_id"])
    op.create_index("ix_dns_records_record_type", "dns_records", ["record_type"])
    op.create_index("ix_dns_records_fqdn", "dns_records", ["fqdn"])
    op.create_index("ix_dns_records_apex", "dns_records", ["apex"])
    op.create_index("ix_dns_records_apex_fqdn", "dns_records", ["apex", "fqdn"])
    op.create_index("ix_dns_records_live", "dns_records", ["deleted_at", "apex"])

    op.create_table(
        "change_sets",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("applied_at", sa.DateTime(timezone=True)),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("source", source_enum, nullable=False),
        sa.Column("status", status_enum, nullable=False),
        sa.Column("author_subject", sa.String(255)),
        sa.Column("author_name", sa.String(255)),
        sa.Column("author_email", sa.String(320)),
        sa.Column("unifi_admin", sa.String(255)),
        sa.Column(
            "reverts_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("change_sets.id", ondelete="SET NULL"),
        ),
        sa.Column("error", sa.Text()),
    )
    op.create_index("ix_change_sets_created_at", "change_sets", ["created_at"])
    op.create_index("ix_change_sets_status", "change_sets", ["status"])

    op.create_table(
        "record_revisions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "change_set_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("change_sets.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("op", op_enum, nullable=False),
        sa.Column(
            "record_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("dns_records.id", ondelete="SET NULL"),
        ),
        sa.Column("unifi_id", sa.String(64)),
        sa.Column("fqdn", sa.String(512), nullable=False),
        sa.Column("record_type", sa.String(32), nullable=False),
        sa.Column("before", postgresql.JSONB()),
        sa.Column("after", postgresql.JSONB()),
        sa.Column("applied", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("error", sa.Text()),
        sa.UniqueConstraint("change_set_id", "seq", name="uq_revision_seq"),
    )
    op.create_index("ix_record_revisions_change_set_id", "record_revisions", ["change_set_id"])
    op.create_index("ix_record_revisions_unifi_id", "record_revisions", ["unifi_id"])


def downgrade() -> None:
    op.drop_table("record_revisions")
    op.drop_table("change_sets")
    op.drop_table("dns_records")
    op.drop_table("apexes")
    op_enum.drop(op.get_bind(), checkfirst=True)
    status_enum.drop(op.get_bind(), checkfirst=True)
    source_enum.drop(op.get_bind(), checkfirst=True)

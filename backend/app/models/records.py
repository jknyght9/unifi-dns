"""Desired state, and its full change history.

This replaces the git-backed design. One data layer, and versioning becomes a
table design rather than a subprocess: `change_sets` is the append-only audit
log, `record_revisions` holds before/after for every touched record, and a
rollback is just the inverse of a changeset applied as a new changeset.

History is never rewritten. Rolling back commit N produces commit N+1.
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base


def _values(enum_cls: type[enum.Enum]) -> list[str]:
    """Persist enum *values*, not member names.

    SQLAlchemy defaults to storing `.name`, which breaks for any member whose
    name differs from its value. `ChangeSetSource.import_` is exactly that case:
    the trailing underscore avoids the Python keyword, but the database enum
    label is "import". Without this the insert fails with
    `invalid input value for enum change_set_source: "import_"`.
    """
    return [m.value for m in enum_cls]


class Op(str, enum.Enum):
    create = "create"
    update = "update"
    delete = "delete"


class Target(str, enum.Enum):
    """Which UniFi API a revision acts on.

    Client-bound records live on the client object rather than in the DNS
    store, so they need a different apply and rollback path.
    """

    dns_policy = "dns_policy"
    client_record = "client_record"


class ChangeSetStatus(str, enum.Enum):
    pending = "pending"
    applied = "applied"
    failed = "failed"
    partial = "partial"


class ChangeSetSource(str, enum.Enum):
    ui = "ui"
    import_ = "import"
    reconcile = "reconcile"
    rollback = "rollback"
    api = "api"


class DnsRecordRow(Base):
    """Mirror of a record we believe exists on the gateway.

    `unifi_id` is the gateway's UUID. It is stable across updates on the
    Integration v1 API, which is why this design can hold a reference at all.
    Duplicate (fqdn, type) pairs are legal on UniFi (round robin), so there is
    deliberately no unique constraint on them.
    """

    __tablename__ = "dns_records"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    unifi_id: Mapped[str | None] = mapped_column(String(64), unique=True, index=True)

    record_type: Mapped[str] = mapped_column(String(32), index=True)
    fqdn: Mapped[str] = mapped_column(String(512), index=True)
    apex: Mapped[str | None] = mapped_column(String(255), index=True)
    value: Mapped[str] = mapped_column(Text)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    ttl_seconds: Mapped[int | None] = mapped_column()

    #: Canonical Integration v1 object, exactly as the gateway returns it.
    payload: Mapped[dict] = mapped_column(JSONB)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        Index("ix_dns_records_apex_fqdn", "apex", "fqdn"),
        Index("ix_dns_records_live", "deleted_at", "apex"),
    )


class ChangeSet(Base):
    """One user-intended batch of changes. The unit of rollback."""

    __tablename__ = "change_sets"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    applied_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    summary: Mapped[str] = mapped_column(Text)
    source: Mapped[ChangeSetSource] = mapped_column(
        Enum(ChangeSetSource, name="change_set_source",
             values_callable=_values),
        default=ChangeSetSource.ui,
    )
    status: Mapped[ChangeSetStatus] = mapped_column(
        Enum(ChangeSetStatus, name="change_set_status", values_callable=_values),
        default=ChangeSetStatus.pending,
        index=True,
    )

    # Attribution. OIDC supplies the human; the UniFi admin identity behind the
    # API key is recorded separately so both halves of "who did this" survive.
    author_subject: Mapped[str | None] = mapped_column(String(255))
    author_name: Mapped[str | None] = mapped_column(String(255))
    author_email: Mapped[str | None] = mapped_column(String(320))
    unifi_admin: Mapped[str | None] = mapped_column(String(255))

    #: Set when this changeset undoes another one.
    reverts_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("change_sets.id", ondelete="SET NULL")
    )

    error: Mapped[str | None] = mapped_column(Text)

    revisions: Mapped[list["RecordRevision"]] = relationship(
        back_populates="change_set",
        cascade="all, delete-orphan",
        order_by="RecordRevision.seq",
    )


class RecordRevision(Base):
    """Before and after for a single record within a changeset.

    `before` is null on create, `after` is null on delete. Both are the full
    Integration v1 object, so a rollback needs no other source of truth.
    """

    __tablename__ = "record_revisions"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    change_set_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("change_sets.id", ondelete="CASCADE"), index=True
    )
    seq: Mapped[int] = mapped_column()

    op: Mapped[Op] = mapped_column(
        Enum(Op, name="revision_op", values_callable=_values)
    )
    target: Mapped[Target] = mapped_column(
        Enum(Target, name="revision_target", values_callable=_values),
        default=Target.dns_policy,
    )
    record_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("dns_records.id", ondelete="SET NULL")
    )
    unifi_id: Mapped[str | None] = mapped_column(String(64), index=True)
    fqdn: Mapped[str] = mapped_column(String(512))
    record_type: Mapped[str] = mapped_column(String(32))

    before: Mapped[dict | None] = mapped_column(JSONB)
    after: Mapped[dict | None] = mapped_column(JSONB)

    applied: Mapped[bool] = mapped_column(Boolean, default=False)
    error: Mapped[str | None] = mapped_column(Text)

    change_set: Mapped[ChangeSet] = relationship(back_populates="revisions")

    __table_args__ = (UniqueConstraint("change_set_id", "seq", name="uq_revision_seq"),)


class Apex(Base):
    """User-declared apex domain. The basis for zone synthesis."""

    __tablename__ = "apexes"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    description: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

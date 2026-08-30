"""Flow telemetry pulled from the gateway.

The gateway holds roughly five days of flows and serves them only through a
paged endpoint, so this table exists to keep history past that window and to
make aggregation cheap. Rows are keyed on the gateway's own stable flow `id`,
which makes the collector idempotent: re-polling the same page is a no-op.

Note these are *flows*, not individual DNS queries. Each row carries a `count`
of how many connections it represents. See `app/api/routes_stats.py` for what
that does and does not let us claim.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class Flow(Base):
    __tablename__ = "flows"

    id: Mapped[str] = mapped_column(String(96), primary_key=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)

    action: Mapped[str | None] = mapped_column(String(64), index=True)
    service: Mapped[str | None] = mapped_column(String(128), index=True)
    protocol: Mapped[str | None] = mapped_column(String(32))
    risk: Mapped[str | None] = mapped_column(String(32))
    count: Mapped[int] = mapped_column(Integer, default=1)

    src_ip: Mapped[str | None] = mapped_column(String(128), index=True)
    src_mac: Mapped[str | None] = mapped_column(String(64))
    src_name: Mapped[str | None] = mapped_column(String(512))
    src_network: Mapped[str | None] = mapped_column(String(255), index=True)
    src_zone: Mapped[str | None] = mapped_column(String(255))

    dst_ip: Mapped[str | None] = mapped_column(String(128), index=True)
    dst_port: Mapped[int | None] = mapped_column(Integer, index=True)
    dst_name: Mapped[str | None] = mapped_column(String(512))

    #: Domains the gateway associated with the flow. Present on ~64% of flows
    #: overall and ~90% of DNS ones.
    domains: Mapped[list[str] | None] = mapped_column(ARRAY(Text))

    policy_type: Mapped[str | None] = mapped_column(String(128), index=True)
    policy_internal_type: Mapped[str | None] = mapped_column(String(128), index=True)
    policy_name: Mapped[str | None] = mapped_column(String(255))

    bytes_total: Mapped[int | None] = mapped_column(BigInteger)

    #: True when the gateway answered with a null address, which is a blocked
    #: DNS answer rather than a real destination.
    sinkholed: Mapped[bool] = mapped_column(Boolean, default=False, index=True)

    raw: Mapped[dict | None] = mapped_column(JSONB)

    __table_args__ = (
        Index("ix_flows_ts_action", "ts", "action"),
        Index("ix_flows_src_ts", "src_ip", "ts"),
        Index("ix_flows_domains", "domains", postgresql_using="gin"),
    )

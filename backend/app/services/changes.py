"""Changesets: plan, apply, reconcile, roll back.

Every mutation goes through a changeset, so the audit log is not an optional
side effect that a code path can forget to write. Applying is deliberately
sequential: the gateway rejects concurrent writes, and a partial failure must
leave an accurate record of which revisions landed.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from pydantic import TypeAdapter
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.records import (
    ChangeSet,
    ChangeSetSource,
    ChangeSetStatus,
    DnsRecordRow,
    Op,
    RecordRevision,
    Target,
)
from app.schemas.clients import ClientDnsRecord
from app.schemas.unifi import DnsRecord
from app.services.zones import match_apex
from app.unifi.client import UnifiClient
from app.unifi.errors import UnifiError

_adapter: TypeAdapter[DnsRecord] = TypeAdapter(DnsRecord)


def _now() -> datetime:
    return datetime.now(UTC)


@dataclass
class Author:
    subject: str | None = None
    name: str | None = None
    email: str | None = None
    unifi_admin: str | None = None


@dataclass
class PlannedChange:
    op: Op
    record: DnsRecord | None = None
    unifi_id: str | None = None
    before: dict | None = None
    target: Target = Target.dns_policy
    #: For client_record targets: the desired {hostname, enabled} state.
    client_state: dict | None = None
    fqdn: str | None = None


def _summarise(changes: list[PlannedChange]) -> str:
    counts: dict[str, int] = {}
    for c in changes:
        counts[c.op.value] = counts.get(c.op.value, 0) + 1
    return ", ".join(f"{n} {op}" for op, n in sorted(counts.items())) or "no changes"


async def create_changeset(
    session: AsyncSession,
    changes: list[PlannedChange],
    author: Author,
    *,
    source: ChangeSetSource = ChangeSetSource.ui,
    summary: str | None = None,
    reverts_id: uuid.UUID | None = None,
) -> ChangeSet:
    cs = ChangeSet(
        created_at=_now(),
        summary=summary or _summarise(changes),
        source=source,
        status=ChangeSetStatus.pending,
        author_subject=author.subject,
        author_name=author.name,
        author_email=author.email,
        unifi_admin=author.unifi_admin,
        reverts_id=reverts_id,
    )
    for seq, ch in enumerate(changes):
        rec = ch.record
        if ch.target is Target.client_record:
            after = ch.client_state
            fqdn = ch.fqdn or (ch.client_state or {}).get("hostname") or "?"
            rtype = "A_RECORD"
        else:
            after = rec.model_dump(by_alias=True, exclude_none=True, mode="json") if rec else None
            fqdn = rec.fqdn if rec else (ch.before or {}).get("domain", "?")
            rtype = rec.type if rec else (ch.before or {}).get("type", "?")
        cs.revisions.append(
            RecordRevision(
                seq=seq,
                op=ch.op,
                target=ch.target,
                unifi_id=ch.unifi_id or (rec.id if rec else None),
                fqdn=fqdn,
                record_type=rtype,
                before=ch.before,
                after=after,
            )
        )
    session.add(cs)
    await session.flush()
    return cs


async def record_event(
    session: AsyncSession,
    author: Author,
    summary: str,
    *,
    source: ChangeSetSource = ChangeSetSource.ui,
) -> ChangeSet:
    """Log something that happened without mutating a DNS record.

    Adopting a baseline and declaring an apex domain do not change what the
    gateway resolves, but they do change what this app tracks and how it groups
    what it shows. Leaving them out of the history made the audit log quietly
    incomplete: a user who adopted 19 records and declared a domain saw an empty
    History and reasonably concluded it was broken.

    These carry no revisions, so there is nothing to roll back, and the UI does
    not offer it for them.
    """
    cs = ChangeSet(
        created_at=_now(),
        applied_at=_now(),
        summary=summary,
        source=source,
        status=ChangeSetStatus.applied,
        author_subject=author.subject,
        author_name=author.name,
        author_email=author.email,
        unifi_admin=author.unifi_admin,
    )
    session.add(cs)
    await session.flush()
    return cs


async def apply_changeset(
    session: AsyncSession,
    cs: ChangeSet,
    client: UnifiClient,
    apexes: list[str],
) -> ChangeSet:
    """Execute a pending changeset against the gateway, revision by revision.

    Stops at the first failure. Earlier revisions stay applied and are recorded
    as such, so the audit log reflects reality rather than intent.
    """
    failed = False
    for rev in cs.revisions:
        if rev.applied:
            continue
        try:
            if rev.target is Target.client_record:
                state = rev.after or {}
                await client.set_client_record(
                    rev.unifi_id or "", state.get("hostname"), state.get("enabled")
                )
                rev.applied = True
                rev.error = None
                continue

            if rev.op is Op.create:
                record = _adapter.validate_python(rev.after)
                created = await client.create_record(record)
                rev.unifi_id = created.id
                rev.after = created.model_dump(by_alias=True, exclude_none=True, mode="json")
                await _upsert_mirror(session, created, apexes)

            elif rev.op is Op.update:
                record = _adapter.validate_python(rev.after)
                updated = await client.update_record(rev.unifi_id or "", record)
                rev.after = updated.model_dump(by_alias=True, exclude_none=True, mode="json")
                await _upsert_mirror(session, updated, apexes)

            elif rev.op is Op.delete:
                await client.delete_record(rev.unifi_id or "")
                await _soft_delete_mirror(session, rev.unifi_id)

            rev.applied = True
            rev.error = None
        except UnifiError as exc:
            rev.error = str(exc)
            cs.error = f"revision {rev.seq} ({rev.op.value} {rev.fqdn}): {exc}"
            failed = True
            break

    applied_count = sum(1 for r in cs.revisions if r.applied)
    if failed:
        cs.status = ChangeSetStatus.partial if applied_count else ChangeSetStatus.failed
    else:
        cs.status = ChangeSetStatus.applied
        cs.applied_at = _now()
    await session.flush()
    return cs


async def build_rollback(
    session: AsyncSession, cs: ChangeSet
) -> list[PlannedChange]:
    """Inverse of an applied changeset, newest revision undone first.

    A rollback is applied forward as a new changeset. History is never rewritten.
    """
    changes: list[PlannedChange] = []
    for rev in reversed(cs.revisions):
        if not rev.applied:
            continue
        if rev.target is Target.client_record:
            # Restore whatever the client carried before, including "no record".
            changes.append(
                PlannedChange(
                    op=Op.update,
                    target=Target.client_record,
                    unifi_id=rev.unifi_id,
                    client_state=rev.before or {"hostname": None, "enabled": False},
                    before=rev.after,
                    fqdn=rev.fqdn,
                )
            )
            continue
        if rev.op is Op.create:
            changes.append(PlannedChange(op=Op.delete, unifi_id=rev.unifi_id, before=rev.after))
        elif rev.op is Op.delete:
            if rev.before:
                changes.append(
                    PlannedChange(op=Op.create, record=_adapter.validate_python(rev.before))
                )
        elif rev.op is Op.update:
            if rev.before:
                changes.append(
                    PlannedChange(
                        op=Op.update,
                        record=_adapter.validate_python(rev.before),
                        unifi_id=rev.unifi_id,
                        before=rev.after,
                    )
                )
    return changes


@dataclass
class Drift:
    only_on_gateway: list[DnsRecord]
    only_in_mirror: list[DnsRecordRow]
    modified: list[tuple[DnsRecordRow, DnsRecord]]

    @property
    def clean(self) -> bool:
        return not (self.only_on_gateway or self.only_in_mirror or self.modified)


async def detect_drift(session: AsyncSession, client: UnifiClient) -> Drift:
    """Compare the gateway against our mirror.

    Anyone editing records in the native UniFi console shows up here. Without
    this the mirror silently becomes fiction the first time someone does.
    """
    live = await client.list_records()
    live_by_id = {r.id: r for r in live if r.id}

    rows = (
        await session.execute(
            select(DnsRecordRow).where(DnsRecordRow.deleted_at.is_(None))
        )
    ).scalars().all()
    rows_by_id = {r.unifi_id: r for r in rows if r.unifi_id}

    only_gateway = [r for rid, r in live_by_id.items() if rid not in rows_by_id]
    only_mirror = [r for rid, r in rows_by_id.items() if rid not in live_by_id]
    modified = [
        (row, live_by_id[rid])
        for rid, row in rows_by_id.items()
        if rid in live_by_id
        and row.payload != live_by_id[rid].model_dump(
            by_alias=True, exclude_none=True, mode="json"
        )
    ]
    return Drift(only_gateway, only_mirror, modified)


async def sync_mirror(
    session: AsyncSession, client: UnifiClient, apexes: list[str]
) -> int:
    """Adopt gateway state wholesale. Used on first run and to resolve drift."""
    live = await client.list_records()
    live_ids = {r.id for r in live if r.id}
    for record in live:
        await _upsert_mirror(session, record, apexes)
    rows = (
        await session.execute(
            select(DnsRecordRow).where(DnsRecordRow.deleted_at.is_(None))
        )
    ).scalars().all()
    for row in rows:
        if row.unifi_id not in live_ids:
            row.deleted_at = _now()
    await session.flush()
    return len(live)


async def _upsert_mirror(
    session: AsyncSession, record: DnsRecord, apexes: list[str]
) -> DnsRecordRow:
    payload = record.model_dump(by_alias=True, exclude_none=True, mode="json")
    row = (
        await session.execute(
            select(DnsRecordRow).where(DnsRecordRow.unifi_id == record.id)
        )
    ).scalar_one_or_none()
    now = _now()
    if row is None:
        row = DnsRecordRow(
            unifi_id=record.id, created_at=now, updated_at=now
        )
        session.add(row)
    row.record_type = record.type
    row.fqdn = record.fqdn
    row.apex = match_apex(record.fqdn, apexes)
    row.value = record.value
    row.enabled = record.enabled
    row.ttl_seconds = getattr(record, "ttl_seconds", None)
    row.payload = payload
    row.updated_at = now
    row.deleted_at = None
    return row


async def _soft_delete_mirror(session: AsyncSession, unifi_id: str | None) -> None:
    if not unifi_id:
        return
    row = (
        await session.execute(
            select(DnsRecordRow).where(DnsRecordRow.unifi_id == unifi_id)
        )
    ).scalar_one_or_none()
    if row is not None:
        row.deleted_at = _now()

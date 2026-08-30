from __future__ import annotations

import uuid
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field, TypeAdapter
from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.deps import get_author, get_client
from app.config import Settings, get_settings
from app.db import get_session
from app.models.records import (
    Apex,
    DnsRecordRow,
    ChangeSet,
    ChangeSetSource,
    ChangeSetStatus,
    Op,
    Target,
)
from app.schemas.clients import ClientRecordUpdate
from app.schemas.unifi import DnsRecord
from app.services import changes as svc
from app.services.zones import build_zones, suggest_apexes
from app.unifi.client import UnifiClient
from app.unifi.errors import UnifiError

router = APIRouter()
_adapter: TypeAdapter[DnsRecord] = TypeAdapter(DnsRecord)


# ------------------------------------------------------------------ system

@router.get("/system/status")
async def status(
    client: Annotated[UnifiClient, Depends(get_client)],
    session: Annotated[AsyncSession, Depends(get_session)],
):
    try:
        version = await client.application_version()
        admin = await client.whoami()
        reachable = True
        error = None
    except UnifiError as exc:
        version, admin, reachable, error = None, {}, False, exc.as_dict()
    apexes = await _apex_names(session)
    return {
        "unifi_reachable": reachable,
        "unifi_error": error,
        "application_version": version,
        "unifi_admin": admin.get("name"),
        "apexes": apexes,
    }


# ------------------------------------------------------------------- apexes

class ApexIn(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    description: str | None = None


async def _apex_names(session: AsyncSession) -> list[str]:
    rows = (await session.execute(select(Apex).order_by(Apex.name))).scalars().all()
    return [r.name for r in rows]


@router.get("/apexes")
async def list_apexes(session: Annotated[AsyncSession, Depends(get_session)]):
    rows = (await session.execute(select(Apex).order_by(Apex.name))).scalars().all()
    return [{"id": str(r.id), "name": r.name, "description": r.description} for r in rows]


@router.post("/apexes", status_code=201)
async def add_apex(
    body: ApexIn,
    session: Annotated[AsyncSession, Depends(get_session)],
    author: Annotated[svc.Author, Depends(get_author)],
):
    from datetime import UTC, datetime

    name = body.name.rstrip(".").lower()
    existing = (
        await session.execute(select(Apex).where(Apex.name == name))
    ).scalar_one_or_none()
    if existing:
        raise HTTPException(409, f"apex {name!r} already exists")
    row = Apex(name=name, description=body.description, created_at=datetime.now(UTC))
    session.add(row)
    await svc.record_event(session, author, f"declare apex domain {name}")
    await session.commit()
    return {"id": str(row.id), "name": row.name}


@router.delete("/apexes/{apex_id}", status_code=204)
async def remove_apex(
    apex_id: uuid.UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    author: Annotated[svc.Author, Depends(get_author)],
):
    row = await session.get(Apex, apex_id)
    if row is None:
        raise HTTPException(404, "no such apex")
    name = row.name
    await session.delete(row)
    await svc.record_event(session, author, f"remove apex domain {name}")
    await session.commit()


@router.get("/apexes/suggest")
async def suggest(client: Annotated[UnifiClient, Depends(get_client)]):
    """Candidate apexes inferred from live records, for first-run bootstrap.

    Uses the last two labels, which is wrong for multi-label public suffixes
    like `co.uk`, so this is a suggestion the operator confirms and never
    something applied automatically.
    """
    return {"suggestions": suggest_apexes(await client.list_records())}


# ------------------------------------------------------------------ records

@router.get("/records")
async def list_records(
    client: Annotated[UnifiClient, Depends(get_client)],
    session: Annotated[AsyncSession, Depends(get_session)],
    group: Annotated[bool, Query(description="Group into synthesised zones")] = True,
):
    # Two independent sources. Client-bound records are invisible to the DNS
    # API but the gateway resolves them all the same, so a view that omitted
    # them would under-report what the network actually answers for.
    policy_records = await client.list_records()
    client_records = await client.list_client_records()
    combined = [*policy_records, *client_records]
    apexes = await _apex_names(session)

    if not group:
        return {
            "records": [_render(r) for r in combined],
            "total": len(combined),
            "policy_count": len(policy_records),
            "client_count": len(client_records),
        }

    zones = build_zones(combined, apexes)
    return {
        "total": len(combined),
        "policy_count": len(policy_records),
        "client_count": len(client_records),
        "zones": [
            {
                "apex": z.apex,
                "ungrouped": z.is_ungrouped,
                "bare": z.is_bare,
                "count": z.count,
                "records": [_render(e.record, e.label) for e in z.entries],
            }
            for z in zones
        ],
    }


def _render(record: object, label: str | None = None) -> dict:
    """Uniform shape for both record sources.

    `source` tells the UI which one it is: a `client` record is bound to a
    device, so it can be renamed, disabled, or cleared but not deleted like a
    free-standing DNS policy.
    """
    is_client = hasattr(record, "client_id")
    out = {
        "id": record.id,
        "source": "client" if is_client else "policy",
        "type": record.type,
        "fqdn": record.fqdn,
        "label": label,
        "value": record.value,
        "enabled": record.enabled,
        "ttl_seconds": getattr(record, "ttl_seconds", None),
    }
    if is_client:
        out["client_name"] = record.display_name
        out["network_name"] = record.network_name
        out["unstable"] = record.unstable
        out["raw"] = record.model_dump(by_alias=True, mode="json")
    else:
        out["raw"] = record.model_dump(by_alias=True, exclude_none=True, mode="json")
    return out


# --------------------------------------------------- client-bound records

@router.get("/client-records")
async def list_client_records(client: Annotated[UnifiClient, Depends(get_client)]):
    records = await client.list_client_records()
    return {
        "records": [_render(r) for r in records],
        "eligible_clients": [
            c.model_dump() for c in await client.list_eligible_clients()
        ],
    }


@router.put("/client-records/{client_id}")
async def set_client_record(
    client_id: str,
    body: ClientRecordUpdate,
    client: Annotated[UnifiClient, Depends(get_client)],
    session: Annotated[AsyncSession, Depends(get_session)],
    author: Annotated[svc.Author, Depends(get_author)],
):
    """Set, rename, enable, disable, or clear a client's local DNS record.

    Setting a hostname on a client that has none creates the record; there is
    no separate create path, because the record cannot exist without a client.
    """
    current = await client.get_client_record(client_id)
    before = (
        {"hostname": current.local_dns_record, "enabled": current.enabled}
        if current
        else {"hostname": None, "enabled": False}
    )
    clearing = not body.hostname
    after = {
        "hostname": body.hostname,
        # A cleared record cannot stay enabled; carrying the previous value
        # forward is what produced `LocalDnsRecordMissing`.
        "enabled": False if clearing
        else (before["enabled"] if body.enabled is None else body.enabled),
    }
    verb = "clear" if clearing else ("set" if current is None else "update")
    return await _run_changeset(
        session,
        client,
        [
            svc.PlannedChange(
                op=Op.update,
                target=Target.client_record,
                unifi_id=client_id,
                before=before,
                client_state=after,
                fqdn=body.hostname or before["hostname"] or "?",
            )
        ],
        author,
        f"{verb} client DNS record {after['hostname'] or before['hostname']}",
    )


class RecordWrite(BaseModel):
    """A record as submitted by a client, plus an optional note for the audit log."""

    record: DnsRecord
    note: str | None = None


@router.post("/records", status_code=201)
async def create_record(
    body: RecordWrite,
    client: Annotated[UnifiClient, Depends(get_client)],
    session: Annotated[AsyncSession, Depends(get_session)],
    author: Annotated[svc.Author, Depends(get_author)],
):
    return await _run_changeset(
        session,
        client,
        [svc.PlannedChange(op=Op.create, record=body.record)],
        author,
        body.note or f"create {body.record.type} {body.record.fqdn}",
    )


@router.put("/records/{record_id}")
async def update_record(
    record_id: str,
    body: RecordWrite,
    client: Annotated[UnifiClient, Depends(get_client)],
    session: Annotated[AsyncSession, Depends(get_session)],
    author: Annotated[svc.Author, Depends(get_author)],
):
    try:
        before = await client.get_record(record_id)
    except UnifiError as exc:
        raise HTTPException(exc.status, exc.as_dict()) from exc
    return await _run_changeset(
        session,
        client,
        [
            svc.PlannedChange(
                op=Op.update,
                record=body.record,
                unifi_id=record_id,
                before=before.model_dump(by_alias=True, exclude_none=True, mode="json"),
            )
        ],
        author,
        body.note or f"update {body.record.type} {body.record.fqdn}",
    )


@router.delete("/records/{record_id}")
async def delete_record(
    record_id: str,
    client: Annotated[UnifiClient, Depends(get_client)],
    session: Annotated[AsyncSession, Depends(get_session)],
    author: Annotated[svc.Author, Depends(get_author)],
):
    try:
        before = await client.get_record(record_id)
    except UnifiError as exc:
        raise HTTPException(exc.status, exc.as_dict()) from exc
    return await _run_changeset(
        session,
        client,
        [
            svc.PlannedChange(
                op=Op.delete,
                unifi_id=record_id,
                before=before.model_dump(by_alias=True, exclude_none=True, mode="json"),
            )
        ],
        author,
        f"delete {before.type} {before.fqdn}",
    )


class BulkOp(BaseModel):
    op: Literal["create", "update", "delete"]
    record: DnsRecord | None = None
    unifi_id: str | None = None


class BulkIn(BaseModel):
    operations: list[BulkOp]
    note: str | None = None


@router.post("/records/bulk")
async def bulk(
    body: BulkIn,
    client: Annotated[UnifiClient, Depends(get_client)],
    session: Annotated[AsyncSession, Depends(get_session)],
    author: Annotated[svc.Author, Depends(get_author)],
):
    """Bulk import or edit. Applied strictly sequentially: the gateway rejects
    concurrent writes, so fanning these out would fail unpredictably."""
    planned: list[svc.PlannedChange] = []
    for item in body.operations:
        op = Op(item.op)
        before = None
        if op in (Op.update, Op.delete) and item.unifi_id:
            try:
                existing = await client.get_record(item.unifi_id)
                before = existing.model_dump(by_alias=True, exclude_none=True, mode="json")
            except UnifiError:
                before = None
        planned.append(
            svc.PlannedChange(op=op, record=item.record, unifi_id=item.unifi_id, before=before)
        )
    return await _run_changeset(
        session, client, planned, author, body.note or f"bulk: {len(planned)} operations",
        source=ChangeSetSource.import_,
    )


# -------------------------------------------------------------- changesets

@router.get("/changesets")
async def list_changesets(
    session: Annotated[AsyncSession, Depends(get_session)],
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
):
    rows = (
        await session.execute(
            select(ChangeSet)
            .options(selectinload(ChangeSet.revisions))
            .order_by(desc(ChangeSet.created_at))
            .limit(limit)
        )
    ).scalars().all()
    return [_render_changeset(cs) for cs in rows]


@router.get("/changesets/{changeset_id}")
async def get_changeset(
    changeset_id: uuid.UUID, session: Annotated[AsyncSession, Depends(get_session)]
):
    cs = (
        await session.execute(
            select(ChangeSet)
            .options(selectinload(ChangeSet.revisions))
            .where(ChangeSet.id == changeset_id)
        )
    ).scalar_one_or_none()
    if cs is None:
        raise HTTPException(404, "no such changeset")
    return _render_changeset(cs, detail=True)


@router.post("/changesets/{changeset_id}/rollback")
async def rollback(
    changeset_id: uuid.UUID,
    client: Annotated[UnifiClient, Depends(get_client)],
    session: Annotated[AsyncSession, Depends(get_session)],
    author: Annotated[svc.Author, Depends(get_author)],
    dry_run: Annotated[bool, Query(description="Return the plan without applying")] = True,
):
    """Undo a changeset by applying its inverse forward as a new changeset.

    History is append-only: rolling back N produces N+1. Defaults to a dry run
    so the plan can be reviewed before anything touches the gateway.
    """
    cs = (
        await session.execute(
            select(ChangeSet)
            .options(selectinload(ChangeSet.revisions))
            .where(ChangeSet.id == changeset_id)
        )
    ).scalar_one_or_none()
    if cs is None:
        raise HTTPException(404, "no such changeset")
    if cs.status not in (ChangeSetStatus.applied, ChangeSetStatus.partial):
        raise HTTPException(409, f"changeset is {cs.status.value}, nothing to roll back")

    planned = await svc.build_rollback(session, cs)
    if not planned:
        return {"plan": [], "applied": False, "detail": "nothing to undo"}
    if dry_run:
        return {
            "plan": [
                {"op": p.op.value, "unifi_id": p.unifi_id,
                 "fqdn": p.record.fqdn if p.record else None,
                 "type": p.record.type if p.record else None,
                 "value": p.record.value if p.record else None}
                for p in planned
            ],
            "applied": False,
        }
    result = await _run_changeset(
        session, client, planned, author,
        f"rollback of {cs.summary}",
        source=ChangeSetSource.rollback,
        reverts_id=cs.id,
    )
    return result


# ------------------------------------------------------------------- drift

@router.get("/drift")
async def drift(
    client: Annotated[UnifiClient, Depends(get_client)],
    session: Annotated[AsyncSession, Depends(get_session)],
):
    """Differences between the gateway and our mirror.

    Anyone editing in the native UniFi console lands here.
    """
    d = await svc.detect_drift(session, client)

    # A fresh install has an empty mirror, so every record on the gateway looks
    # like drift. It is not: nothing has diverged, the app simply has not
    # started tracking yet. Saying "19 records drifted" to someone who just
    # finished setup is alarming and wrong.
    tracked = (
        await session.execute(
            select(func.count()).select_from(DnsRecordRow)
        )
    ).scalar_one()
    first_run = tracked == 0

    return {
        "clean": d.clean,
        "first_run": first_run,
        "only_on_gateway": [_render(r) for r in d.only_on_gateway],
        "only_in_mirror": [
            {"unifi_id": r.unifi_id, "fqdn": r.fqdn, "type": r.record_type, "value": r.value}
            for r in d.only_in_mirror
        ],
        "modified": [
            {"unifi_id": row.unifi_id, "fqdn": row.fqdn,
             "mirror": row.payload,
             "gateway": live.model_dump(by_alias=True, exclude_none=True, mode="json")}
            for row, live in d.modified
        ],
    }


@router.post("/drift/adopt")
async def adopt(
    client: Annotated[UnifiClient, Depends(get_client)],
    session: Annotated[AsyncSession, Depends(get_session)],
    author: Annotated[svc.Author, Depends(get_author)],
):
    """Accept gateway state as truth and resync the mirror."""
    tracked_before = (
        await session.execute(select(func.count()).select_from(DnsRecordRow))
    ).scalar_one()
    apexes = await _apex_names(session)
    n = await svc.sync_mirror(session, client, apexes)
    verb = "start tracking" if tracked_before == 0 else "adopt gateway state for"
    await svc.record_event(
        session, author, f"{verb} {n} records",
        source=ChangeSetSource.reconcile,
    )
    await session.commit()
    return {"synced": n}


# ----------------------------------------------------------------- helpers

async def _run_changeset(
    session: AsyncSession,
    client: UnifiClient,
    planned: list[svc.PlannedChange],
    author: svc.Author,
    summary: str,
    *,
    source: ChangeSetSource = ChangeSetSource.ui,
    reverts_id: uuid.UUID | None = None,
) -> dict:
    settings: Settings = get_settings()
    apexes = await _apex_names(session) or list(settings.default_apexes)
    cs = await svc.create_changeset(
        session, planned, author, source=source, summary=summary, reverts_id=reverts_id
    )
    cs = await svc.apply_changeset(session, cs, client, apexes)
    await session.commit()
    payload = _render_changeset(cs, detail=True)
    if cs.status in (ChangeSetStatus.failed, ChangeSetStatus.partial):
        raise HTTPException(status_code=502, detail=payload)
    return payload


def _render_changeset(cs: ChangeSet, detail: bool = False) -> dict:
    out = {
        "id": str(cs.id),
        "created_at": cs.created_at.isoformat(),
        "applied_at": cs.applied_at.isoformat() if cs.applied_at else None,
        "summary": cs.summary,
        "status": cs.status.value,
        "source": cs.source.value,
        "author": {
            "name": cs.author_name,
            "email": cs.author_email,
            "unifi_admin": cs.unifi_admin,
        },
        "reverts_id": str(cs.reverts_id) if cs.reverts_id else None,
        "error": cs.error,
        "revision_count": len(cs.revisions),
    }
    if detail:
        out["revisions"] = [
            {
                "seq": r.seq, "op": r.op.value, "fqdn": r.fqdn, "type": r.record_type,
                "unifi_id": r.unifi_id, "applied": r.applied, "error": r.error,
                "before": r.before, "after": r.after,
            }
            for r in cs.revisions
        ]
    return out

"""Import records from an external resolver.

Preview then apply, deliberately in two steps. Bulk-importing DNS onto a live
gateway is the kind of thing you want to read before it happens, especially when
the source has entries the target cannot represent.
"""

from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_author, get_client
from app.api.routes import _run_changeset
from app.db import get_session
from app.models.records import ChangeSetSource, Op
from app.services import changes as svc
from app.services.migrate import (
    ImportedRecord,
    fetch_pihole,
    parse_cnames,
    parse_hosts,
)
from app.unifi.client import UnifiClient
from app.unifi.errors import UnifiError

router = APIRouter(prefix="/migrate", tags=["migrate"])


class PiholeSource(BaseModel):
    mode: Literal["pihole"] = "pihole"
    url: str = Field(examples=["http://pi.hole"])
    password: str | None = Field(default=None, repr=False)
    token: str | None = Field(default=None, repr=False)
    verify_tls: bool = False


class TextSource(BaseModel):
    mode: Literal["text"] = "text"
    hosts_text: str = ""
    cname_text: str = ""


class PreviewIn(BaseModel):
    source: PiholeSource | TextSource
    ttl: int = 300


class ApplyIn(BaseModel):
    records: list[dict]
    ttl: int = 300
    note: str | None = None
    #: Records whose name and type already exist with a different value.
    overwrite_conflicts: bool = False


async def _gather(src: PiholeSource | TextSource) -> tuple[list[ImportedRecord], list[dict], str]:
    if isinstance(src, PiholeSource):
        try:
            res = await fetch_pihole(src.url, src.password, src.token, src.verify_tls)
        except Exception as exc:  # noqa: BLE001 - surface the reason verbatim
            raise HTTPException(502, f"could not read Pi-hole: {exc}") from exc
        return res.records, res.skipped, res.source
    hosts = parse_hosts(src.hosts_text)
    cnames = parse_cnames(src.cname_text)
    return (
        [*hosts.records, *cnames.records],
        [*hosts.skipped, *cnames.skipped],
        "pasted text",
    )


@router.post("/preview")
async def preview(
    body: PreviewIn, client: Annotated[UnifiClient, Depends(get_client)]
):
    """Classify every source record against what the gateway already has."""
    imported, skipped, source = await _gather(body.source)

    existing = await client.list_records()
    client_bound = await client.list_client_records()
    by_key: dict[tuple[str, str], list[str]] = {}
    for r in existing:
        by_key.setdefault((r.fqdn.lower(), r.type), []).append(r.value)
    client_names = {r.fqdn.lower() for r in client_bound}

    kind_to_type = {"A": "A_RECORD", "AAAA": "AAAA_RECORD", "CNAME": "CNAME_RECORD"}
    new, duplicate, conflict, shadowed = [], [], [], []
    seen: set[tuple[str, str, str]] = set()

    for rec in imported:
        rtype = kind_to_type[rec.kind]
        key = (rec.fqdn.lower(), rtype)
        dedup = (*key, rec.value)
        if dedup in seen:
            continue
        seen.add(dedup)
        try:
            payload = rec.to_unifi(body.ttl).model_dump(
                by_alias=True, exclude_none=True, mode="json"
            )
        except Exception as exc:  # noqa: BLE001 - bad address, bad name
            skipped.append({"text": f"{rec.fqdn} -> {rec.value}", "why": str(exc)[:120]})
            continue
        item = {
            "fqdn": rec.fqdn, "kind": rec.kind, "value": rec.value,
            "source": rec.source, "payload": payload,
        }
        if rec.fqdn.lower() in client_names:
            # A client-bound record with the same name already answers for this.
            # Creating a policy record too would give the gateway two answers.
            shadowed.append(item)
        elif key not in by_key:
            new.append(item)
        elif rec.value in by_key[key]:
            duplicate.append(item)
        else:
            item["existing"] = by_key[key]
            conflict.append(item)

    return {
        "source": source,
        "counts": {
            "imported": len(imported), "new": len(new), "duplicate": len(duplicate),
            "conflict": len(conflict), "shadowed": len(shadowed), "skipped": len(skipped),
        },
        "new": new, "duplicate": duplicate, "conflict": conflict,
        "shadowed": shadowed, "skipped": skipped,
    }


class RenameIn(BaseModel):
    """Rewrite every record under one apex to sit under another."""

    from_apex: str
    to_apex: str
    include_bare: bool = False


@router.post("/rename/preview")
async def rename_preview(
    body: RenameIn, client: Annotated[UnifiClient, Depends(get_client)]
):
    """Plan a domain move, e.g. old.example -> new.example.internal.

    Records are *added* under the new apex rather than edited in place, so both
    names resolve during the transition. Removing the old ones is a separate,
    deliberate step once everything has been repointed.

    Client-bound records are reported but not rewritten here: they live on the
    device object, and renaming one changes what that device publishes, which
    deserves its own confirmation.
    """
    src = body.from_apex.rstrip(".").lower()
    dst = body.to_apex.rstrip(".").lower()
    if not src or not dst or src == dst:
        raise HTTPException(400, "from_apex and to_apex must differ and be non-empty")

    policy = await client.list_records()
    bound = await client.list_client_records()
    existing = {(r.fqdn.lower(), r.type) for r in policy}

    def under(fqdn: str) -> bool:
        f = fqdn.rstrip(".").lower()
        return f == src or f.endswith("." + src)

    def moved(fqdn: str) -> str:
        f = fqdn.rstrip(".").lower()
        return dst if f == src else f[: -(len(src) + 1)] + "." + dst

    plan, skipped, already = [], [], []
    for rec in policy:
        if not under(rec.fqdn):
            continue
        new_fqdn = moved(rec.fqdn)
        payload = rec.model_dump(by_alias=True, exclude_none=True, mode="json")
        payload.pop("id", None)
        payload.pop("metadata", None)
        if rec.type == "SRV_RECORD":
            # SRV stores its label split apart, so only the zone part moves.
            payload["domain"] = moved(rec.domain)
        else:
            payload["domain"] = new_fqdn
        # A CNAME pointing inside the old apex should follow it across.
        if rec.type == "CNAME_RECORD" and under(payload.get("targetDomain", "")):
            payload["targetDomain"] = moved(payload["targetDomain"])
        item = {
            "old_fqdn": rec.fqdn, "new_fqdn": new_fqdn, "type": rec.type,
            "value": rec.value, "old_id": rec.id, "payload": payload,
        }
        (already if (new_fqdn, rec.type) in existing else plan).append(item)

    for rec in bound:
        if under(rec.fqdn):
            skipped.append({
                "fqdn": rec.fqdn, "why": "client-bound record; rename it on the device",
                "client": rec.display_name, "client_id": rec.client_id,
                "suggested": moved(rec.fqdn),
            })

    return {
        "from_apex": src, "to_apex": dst,
        "counts": {"move": len(plan), "already": len(already), "client_bound": len(skipped)},
        "plan": plan, "already_exists": already, "client_bound": skipped,
        "note": (
            "Applying adds the new names. The originals stay until you remove them, "
            "so both resolve while you repoint clients and services."
        ),
    }


@router.post("/rename/apply")
async def rename_apply(
    body: ApplyIn,
    client: Annotated[UnifiClient, Depends(get_client)],
    session: Annotated[AsyncSession, Depends(get_session)],
    author: Annotated[svc.Author, Depends(get_author)],
):
    """Create the renamed records. Identical to import, named for the audit log."""
    return await apply(body, client, session, author)


@router.post("/remove")
async def remove(
    body: ApplyIn,
    client: Annotated[UnifiClient, Depends(get_client)],
    session: Annotated[AsyncSession, Depends(get_session)],
    author: Annotated[svc.Author, Depends(get_author)],
):
    """Delete records by UniFi id, as one changeset.

    The cleanup half of a domain move: run it once the new names are proven.
    """
    ids = [r.get("id") for r in body.records if r.get("id")]
    if not ids:
        raise HTTPException(400, "no record ids supplied")
    planned = []
    for rid in ids:
        try:
            before = await client.get_record(rid)
        except UnifiError:
            continue
        planned.append(svc.PlannedChange(
            op=Op.delete, unifi_id=rid,
            before=before.model_dump(by_alias=True, exclude_none=True, mode="json"),
        ))
    if not planned:
        raise HTTPException(404, "none of those records still exist")
    return await _run_changeset(
        session, client, planned, author,
        body.note or f"remove {len(planned)} records",
        source=ChangeSetSource.ui,
    )


@router.post("/apply")
async def apply(
    body: ApplyIn,
    client: Annotated[UnifiClient, Depends(get_client)],
    session: Annotated[AsyncSession, Depends(get_session)],
    author: Annotated[svc.Author, Depends(get_author)],
):
    """Create the selected records as a single changeset, so it rolls back as one."""
    if not body.records:
        raise HTTPException(400, "no records selected")
    from pydantic import TypeAdapter

    from app.schemas.unifi import DnsRecord

    adapter: TypeAdapter[DnsRecord] = TypeAdapter(DnsRecord)
    planned = [
        svc.PlannedChange(op=Op.create, record=adapter.validate_python(payload))
        for payload in body.records
    ]
    return await _run_changeset(
        session, client, planned, author,
        body.note or f"import {len(planned)} records from external resolver",
        source=ChangeSetSource.import_,
    )

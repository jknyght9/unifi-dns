"""Gateway DNS dashboard.

**What this data is.** UniFi flow records, not a DNS query log. Each row is a
connection flow carrying a `count`, so "1,000 flows" is not "1,000 queries".
Roughly 64% of flows carry a domain, rising to ~90% for DNS-classified ones.
The gateway retains about five days; this app keeps whatever it has collected
since it was first run.

**What that means for the numbers.** Blocked-versus-allowed, per-client
attribution, per-policy attribution, and top blocked domains are all solid and
directly comparable to what Pi-hole shows. Absolute query volume is not: the
gateway aggregates, so totals here are lower than a real query log would report.
Every response therefore carries a `caveat` field rather than leaving the reader
to assume parity.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy import Integer, and_, cast, desc, distinct, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_client
from app.db import get_session
from app.models.flows import Flow
from app.unifi.client import UnifiClient

router = APIRouter(prefix="/stats", tags=["stats"])

CAVEAT = (
    "UniFi records aggregated connection flows, not individual DNS queries. "
    "Proportions and attribution are reliable; absolute counts are lower than a "
    "true query log."
)

#: A DNS-filtering block: the ad blocking policy acted, or the gateway answered
#: with a null address. Deliberately excludes REGION_BLOCKING and firewall drops,
#: which are inbound perimeter events and have nothing to do with DNS. Counting
#: those as "queries blocked" inflates the number by an order of magnitude and
#: makes the dashboard lie.
DNS_BLOCK = or_(Flow.sinkholed.is_(True), Flow.policy_internal_type == "AD_BLOCKING")

#: LAN-originated traffic. Inbound WAN sources are not clients.
LAN_SRC = or_(
    Flow.src_ip.like("10.%"), Flow.src_ip.like("192.168.%"), Flow.src_ip.like("172.16.%")
)

DOH_IPS = {
    "1.1.1.1", "1.0.0.1", "8.8.8.8", "8.8.4.4", "9.9.9.9", "149.112.112.112",
    "208.67.222.222", "208.67.220.220", "94.140.14.14", "94.140.15.15",
}


def _since(hours: int) -> datetime:
    return datetime.now(UTC) - timedelta(hours=hours)


async def _gateway_ips(client: UnifiClient) -> set[str]:
    """The gateway answers on .1 of every VLAN, not just its management address.

    Treating only one of those as "the gateway" makes every other VLAN's clients
    look like they are bypassing it.
    """
    ips: set[str] = set()
    for n in await client.get_networks():
        subnet = getattr(n, "ip_subnet", None) or (n.model_extra or {}).get("ip_subnet")
        if subnet:
            ips.add(str(subnet).split("/")[0])
    return ips


@router.get("/summary")
async def summary(
    session: Annotated[AsyncSession, Depends(get_session)],
    hours: Annotated[int, Query(ge=1, le=24 * 30)] = 24,
):
    since = _since(hours)
    base = select(Flow).where(Flow.ts >= since).subquery()

    totals = (await session.execute(
        select(
            func.count().label("flows"),
            func.coalesce(func.sum(base.c.count), 0).label("events"),
            func.count(distinct(base.c.src_ip)).label("clients"),
        ).select_from(base)
    )).one()

    blocked = (await session.execute(
        select(func.count(), func.coalesce(func.sum(base.c.count), 0))
        .select_from(base).where(base.c.action == "blocked")
    )).one()

    sinkholed = (await session.execute(
        select(func.coalesce(func.sum(base.c.count), 0))
        .select_from(base).where(base.c.sinkholed.is_(True))
    )).scalar_one()

    # The headline pair: DNS traffic seen, and how much of it filtering stopped.
    dns_seen = (await session.execute(
        select(func.coalesce(func.sum(Flow.count), 0)).where(and_(
            Flow.ts >= since, LAN_SRC,
            or_(Flow.service == "DNS", Flow.dst_port == 53, Flow.sinkholed.is_(True)),
        ))
    )).scalar_one()

    dns_blocked = (await session.execute(
        select(func.coalesce(func.sum(Flow.count), 0))
        .where(and_(Flow.ts >= since, LAN_SRC, DNS_BLOCK))
    )).scalar_one()

    perimeter = (await session.execute(
        select(func.coalesce(func.sum(Flow.count), 0)).where(and_(
            Flow.ts >= since, Flow.action == "blocked",
            Flow.policy_internal_type.notin_(["AD_BLOCKING"]),
            Flow.sinkholed.is_(False),
        ))
    )).scalar_one()

    dns_flows = (await session.execute(
        select(func.count()).select_from(base)
        .where(or_(base.c.service == "DNS", base.c.dst_port == 53))
    )).scalar_one()

    lan_clients = (await session.execute(
        select(func.count(distinct(Flow.src_ip)))
        .where(and_(Flow.ts >= since, LAN_SRC))
    )).scalar_one()

    by_policy = (await session.execute(
        select(base.c.policy_internal_type, func.coalesce(func.sum(base.c.count), 0))
        .select_from(base).where(base.c.action == "blocked")
        .group_by(base.c.policy_internal_type)
        .order_by(desc(func.sum(base.c.count)))
    )).all()

    oldest = (await session.execute(select(func.min(Flow.ts)))).scalar_one_or_none()
    newest = (await session.execute(select(func.max(Flow.ts)))).scalar_one_or_none()

    events = int(totals.events or 0)
    blocked_events = int(blocked[1] or 0)
    seen, dnsblk = int(dns_seen or 0), int(dns_blocked or 0)
    return {
        "window_hours": hours,
        # Headline numbers, DNS only.
        "dns_seen": seen,
        "dns_blocked": dnsblk,
        "dns_block_rate": round(dnsblk / seen * 100, 1) if seen else 0.0,
        # Context, everything the gateway saw.
        "perimeter_blocked": int(perimeter or 0),
        "flows": int(totals.flows or 0),
        "events": events,
        "blocked_events": blocked_events,
        "block_rate": round(blocked_events / events * 100, 1) if events else 0.0,
        "sinkholed_events": int(sinkholed or 0),
        "dns_flows": int(dns_flows or 0),
        "clients": int(lan_clients or 0),
        "all_sources": int(totals.clients or 0),
        "by_policy": [
            {"policy": p or "unattributed", "events": int(n)} for p, n in by_policy
        ],
        "coverage": {
            "oldest": oldest.isoformat() if oldest else None,
            "newest": newest.isoformat() if newest else None,
        },
        "caveat": CAVEAT,
    }


@router.get("/domains")
async def domains(
    session: Annotated[AsyncSession, Depends(get_session)],
    hours: Annotated[int, Query(ge=1, le=24 * 30)] = 24,
    limit: Annotated[int, Query(ge=1, le=200)] = 25,
    blocked: Annotated[bool | None, Query()] = None,
):
    """Top domains, unnested from the per-flow domain array."""
    dom = func.unnest(Flow.domains).label("domain")
    # LAN sources only: inbound WAN scan traffic carries reverse-DNS names that
    # would otherwise dominate the list and mean nothing to the operator.
    conds = [Flow.ts >= _since(hours), Flow.domains.isnot(None), LAN_SRC]
    if blocked is True:
        conds.append(DNS_BLOCK)
    elif blocked is False:
        conds.append(and_(Flow.action != "blocked", Flow.sinkholed.is_(False)))

    sub = select(dom, Flow.count.label("c")).where(and_(*conds)).subquery()
    rows = (await session.execute(
        select(sub.c.domain, func.sum(sub.c.c).label("events"))
        .group_by(sub.c.domain).order_by(desc("events")).limit(limit)
    )).all()
    return {
        "hours": hours,
        "blocked": blocked,
        "domains": [{"domain": d, "events": int(n)} for d, n in rows],
        "caveat": CAVEAT,
    }


@router.get("/clients")
async def clients(
    session: Annotated[AsyncSession, Depends(get_session)],
    hours: Annotated[int, Query(ge=1, le=24 * 30)] = 24,
    limit: Annotated[int, Query(ge=1, le=200)] = 25,
):
    since = _since(hours)
    blocked_case = func.sum(cast(DNS_BLOCK, Integer) * Flow.count)
    rows = (await session.execute(
        select(
            Flow.src_ip, func.max(Flow.src_name), func.max(Flow.src_mac),
            func.max(Flow.src_network),
            func.sum(Flow.count).label("events"),
            blocked_case.label("blocked"),
        )
        .where(and_(Flow.ts >= since, Flow.src_ip.isnot(None), LAN_SRC))
        .group_by(Flow.src_ip).order_by(desc("blocked"), desc("events")).limit(limit)
    )).all()
    out = []
    for ip, name, mac, net, events, blk in rows:
        ev, b = int(events or 0), int(blk or 0)
        out.append({
            "ip": ip, "name": name or ip, "mac": mac, "network": net,
            "events": ev, "blocked": b,
            "block_rate": round(b / ev * 100, 1) if ev else 0.0,
        })
    return {"hours": hours, "clients": out, "caveat": CAVEAT}


@router.get("/timeline")
async def timeline(
    session: Annotated[AsyncSession, Depends(get_session)],
    hours: Annotated[int, Query(ge=1, le=24 * 30)] = 24,
):
    bucket = func.date_trunc("hour", Flow.ts).label("bucket")
    rows = (await session.execute(
        select(
            bucket,
            func.sum(Flow.count).label("events"),
            func.sum(cast(DNS_BLOCK, Integer) * Flow.count).label("blocked"),
        ).where(and_(Flow.ts >= _since(hours), LAN_SRC))
        .group_by(bucket).order_by(bucket)
    )).all()
    return {
        "hours": hours,
        "buckets": [
            {"t": b.isoformat(), "events": int(e or 0), "blocked": int(x or 0)}
            for b, e, x in rows
        ],
    }


@router.get("/bypass")
async def bypass(
    session: Annotated[AsyncSession, Depends(get_session)],
    client: Annotated[UnifiClient, Depends(get_client)],
    hours: Annotated[int, Query(ge=1, le=24 * 30)] = 168,
):
    """Clients sending DNS somewhere other than the gateway.

    Every control on the DNS Settings page is irrelevant to these devices,
    because they never ask the gateway anything.

    Sinkholed flows are excluded deliberately: a destination of 127.0.0.1 is the
    gateway answering with a null address, which is filtering working, not a
    device escaping it. Counting those inverts the meaning of the whole page.
    """
    gw = await _gateway_ips(client)
    since = _since(hours)

    is_dns = or_(
        Flow.dst_port == 53,
        Flow.dst_port == 853,
        and_(Flow.dst_port == 443, Flow.dst_ip.in_(DOH_IPS)),
    )
    rows = (await session.execute(
        select(
            Flow.src_ip, func.max(Flow.src_name), func.max(Flow.src_mac),
            func.max(Flow.src_network), Flow.dst_ip, Flow.dst_port,
            func.sum(Flow.count).label("events"),
        )
        .where(and_(
            Flow.ts >= since, is_dns,
            Flow.sinkholed.is_(False),
            Flow.src_ip.isnot(None),
            Flow.dst_ip.notin_(gw or {"__none__"}),
            Flow.src_ip.notin_(gw or {"__none__"}),
            or_(Flow.src_ip.like("10.%"), Flow.src_ip.like("192.168.%"),
                Flow.src_ip.like("172.16.%")),
        ))
        .group_by(Flow.src_ip, Flow.dst_ip, Flow.dst_port)
        .order_by(desc("events"))
    )).all()

    devices: dict[str, dict] = {}
    for ip, name, mac, net, dst, port, events in rows:
        d = devices.setdefault(ip, {
            "ip": ip, "name": name or ip, "mac": mac, "network": net,
            "events": 0, "methods": [],
        })
        kind = {53: "plaintext :53", 853: "DoT :853", 443: "DoH :443"}.get(port, str(port))
        d["events"] += int(events or 0)
        d["methods"].append({"kind": kind, "dest": dst, "events": int(events or 0)})

    ordered = sorted(devices.values(), key=lambda d: -d["events"])
    return {
        "hours": hours,
        "gateway_ips": sorted(gw),
        "device_count": len(ordered),
        "devices": ordered,
    }


@router.post("/collect")
async def collect_now(client: Annotated[UnifiClient, Depends(get_client)]):
    """Force a poll instead of waiting for the interval."""
    from app.services.collector import collect_once

    return {"stored": await collect_once(client)}

"""Background collector for gateway flow telemetry.

The gateway keeps about five days of flows and only serves them paged. Polling
them into Postgres gives history beyond that window and makes the dashboard a
few indexed queries instead of ~70 HTTP round trips per page load.

Idempotent by construction: rows are keyed on the gateway's own flow `id`, so
re-polling overlapping windows inserts nothing new.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime

from sqlalchemy.dialects.postgresql import insert

from app.db import SessionLocal
from app.models.flows import Flow
from app.unifi.client import UnifiClient
from app.unifi.errors import UnifiError

log = logging.getLogger(__name__)

#: A DNS answer of 0.0.0.0 or loopback is a block, not a destination.
SINKHOLE_IPS = {"127.0.0.1", "0.0.0.0", "::1", "::"}


def _clip(v: object, n: int) -> str | None:
    """Column widths were sized from a sample; the wild data is wider.

    The schema now has room, but a stray oversized value should degrade to a
    truncated string rather than abort a whole 14k-row batch.
    """
    if v is None:
        return None
    s = str(v)
    return s if len(s) <= n else s[:n]


def _row(f: dict) -> dict:
    src = f.get("source") or {}
    dst = f.get("destination") or {}
    policies = f.get("policies") or []
    # Prefer a real policy decision over the conntrack bookkeeping entry, which
    # is attached to almost everything and says nothing about why.
    pol = next((p for p in policies if p.get("internal_type") != "CONNTRACK"), None)
    pol = pol or (policies[0] if policies else {})
    ts = f.get("time") or f.get("flow_end_time") or 0
    return {
        "id": f["id"],
        "ts": datetime.fromtimestamp(ts / 1000, UTC) if ts else datetime.now(UTC),
        "action": _clip(f.get("action"), 64),
        "service": _clip(f.get("service"), 128),
        "protocol": _clip(f.get("protocol"), 32),
        "risk": _clip(f.get("risk"), 32),
        "count": f.get("count") or 1,
        "src_ip": _clip(src.get("ip"), 128),
        "src_mac": _clip(src.get("mac"), 64),
        "src_name": _clip(src.get("client_name") or src.get("host_name"), 512),
        "src_network": _clip(src.get("network_name"), 255),
        "src_zone": _clip(src.get("zone_name"), 255),
        "dst_ip": _clip(dst.get("ip"), 128),
        "dst_port": dst.get("port") if isinstance(dst.get("port"), int) else None,
        "dst_name": _clip(dst.get("client_name") or dst.get("host_name"), 512),
        "domains": dst.get("domains") or src.get("domains") or None,
        "policy_type": _clip(pol.get("type"), 128),
        "policy_internal_type": _clip(pol.get("internal_type"), 128),
        "policy_name": _clip(pol.get("name"), 255),
        "bytes_total": (f.get("traffic_data") or {}).get("bytes_total"),
        "sinkholed": dst.get("ip") in SINKHOLE_IPS,
        "raw": None,
    }


async def collect_once(client: UnifiClient) -> int:
    """Pull all available flows and store the ones we have not seen."""
    flows = await client.fetch_flows()
    if not flows:
        return 0
    rows = [_row(f) for f in flows if f.get("id")]
    stored = 0
    async with SessionLocal() as session:
        for chunk_start in range(0, len(rows), 500):
            chunk = rows[chunk_start : chunk_start + 500]
            stmt = insert(Flow).values(chunk).on_conflict_do_nothing(index_elements=["id"])
            result = await session.execute(stmt)
            stored += result.rowcount or 0
        await session.commit()
    return stored


async def run_collector(client: UnifiClient, interval_seconds: int = 300) -> None:
    """Poll forever. Failures are logged and retried; they never kill the task."""
    while True:
        try:
            n = await collect_once(client)
            if n:
                log.info("collector stored %d new flows", n)
        except UnifiError as exc:
            log.warning("collector: gateway error, will retry: %s", exc)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - a collector must not take the app down
            log.exception("collector: unexpected error, will retry")
        await asyncio.sleep(interval_seconds)

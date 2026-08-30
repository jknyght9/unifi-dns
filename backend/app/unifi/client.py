"""Async client for the UniFi Network Integration v1 DNS API.

Scope note: Integration v1 only. The legacy `/v2/api/site/{site}/static-dns`
surface is a view over the same store but has no working update (edits are
delete + create, so record IDs churn). Since Network 10.1 ships v1, supporting
both would buy nothing and cost the caller stable IDs.

Writes are serialised behind a lock. The gateway does not tolerate concurrent
mutations, and DNSControl's provider documents the same constraint.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Literal

import httpx
from pydantic import TypeAdapter, ValidationError

from app.schemas.clients import ClientDnsRecord, EligibleClient
from app.schemas.settings import (
    ContentFilterProfile,
    DohSettings,
    IpsSettings,
    NetworkDns,
    TrafficFlowSettings,
)
from app.schemas.unifi import DnsRecord, RecordPage
from app.unifi.errors import (
    UnifiAuthError,
    UnifiError,
    UnifiErrorBody,
    UnifiNotFound,
)

log = logging.getLogger(__name__)

#: Server-side page ceiling. Larger values are silently clamped.
PAGE_LIMIT = 200

_record_adapter: TypeAdapter[DnsRecord] = TypeAdapter(DnsRecord)


class UnifiClient:
    def __init__(
        self,
        host: str,
        api_key: str,
        *,
        site: str = "default",
        verify_tls: bool = False,
        timeout: float = 20.0,
    ) -> None:
        self._host = host.rstrip("/")
        self._api_key = api_key
        self._site_ref = site
        self._site_id: str | None = None
        self._write_lock = asyncio.Lock()
        self._http = httpx.AsyncClient(
            base_url=f"{self._host}/proxy/network",
            headers={"X-API-Key": api_key, "Accept": "application/json"},
            verify=verify_tls,
            timeout=timeout,
        )

    async def aclose(self) -> None:
        await self._http.aclose()

    async def __aenter__(self) -> "UnifiClient":
        return self

    async def __aexit__(self, *_exc: object) -> None:
        await self.aclose()

    # ---------------------------------------------------------------- plumbing

    async def _request(
        self,
        method: Literal["GET", "POST", "PUT", "DELETE"],
        path: str,
        *,
        json: Any = None,
    ) -> Any:
        resp = await self._http.request(method, path, json=json)
        if resp.status_code >= 300:
            raise self._to_error(resp)
        if not resp.content:
            return None
        return resp.json()

    @staticmethod
    def _to_error(resp: httpx.Response) -> UnifiError:
        body: UnifiErrorBody | None = None
        try:
            body = UnifiErrorBody.model_validate(resp.json())
        except (ValueError, ValidationError):
            pass
        cls: type[UnifiError] = UnifiError
        if resp.status_code in (401, 403):
            cls = UnifiAuthError
        elif resp.status_code == 404:
            cls = UnifiNotFound
        return cls(resp.status_code, body, resp.text[:500])

    # -------------------------------------------------------------------- site

    async def site_id(self) -> str:
        """Resolve and cache the site UUID.

        The DNS endpoints take the UUID, not the human-readable `default`.
        """
        if self._site_id is not None:
            return self._site_id
        # The sites list is tiny, so match client-side rather than using the
        # filter DSL. Note `.like()` on that DSL returns 200 with zero rows
        # instead of erroring, so it is not safe to lean on generally.
        field = "id" if _looks_like_uuid(self._site_ref) else "internalReference"
        data = await self._request(
            "GET", "/integration/v1/sites", json=None
        ) or {}
        for site in data.get("data", []):
            if site.get(field) == self._site_ref:
                self._site_id = site["id"]
                return self._site_id
        available = [s.get("internalReference") for s in data.get("data", [])]
        raise UnifiError(
            404, None, f"no site matching {field}={self._site_ref!r}; have {available}"
        )

    async def whoami(self) -> dict:
        """Identity behind the API key.

        Note this returns full admin detail, which is how the app attributes
        changes without maintaining its own user table.
        """
        data = await self._request("GET", "/api/self")
        rows = data.get("data") if isinstance(data, dict) else None
        return (rows or [{}])[0]

    async def application_version(self) -> str | None:
        data = await self._request("GET", "/integration/v1/info")
        return (data or {}).get("applicationVersion")

    # ----------------------------------------------------------------- records

    async def _dns_path(self, record_id: str | None = None) -> str:
        base = f"/integration/v1/sites/{await self.site_id()}/dns/policies"
        return f"{base}/{record_id}" if record_id else base

    async def list_records(self) -> list[DnsRecord]:
        """Every record, following pagination to the end."""
        out: list[DnsRecord] = []
        offset = 0
        path = await self._dns_path()
        while True:
            page = RecordPage.model_validate(
                await self._request(
                    "GET", f"{path}?offset={offset}&limit={PAGE_LIMIT}"
                )
            )
            out.extend(page.data)
            offset += page.count
            if page.count == 0 or offset >= page.total_count:
                break
        return out

    async def get_record(self, record_id: str) -> DnsRecord:
        return _record_adapter.validate_python(
            await self._request("GET", await self._dns_path(record_id))
        )

    async def create_record(self, record: DnsRecord) -> DnsRecord:
        async with self._write_lock:
            created = await self._request(
                "POST", await self._dns_path(), json=record.write_payload()
            )
        return _record_adapter.validate_python(created)

    async def update_record(self, record_id: str, record: DnsRecord) -> DnsRecord:
        """Native update. The ID lives in the path; sending it in the body is a 400."""
        async with self._write_lock:
            updated = await self._request(
                "PUT", await self._dns_path(record_id), json=record.write_payload()
            )
        return _record_adapter.validate_python(updated)

    async def delete_record(self, record_id: str) -> None:
        async with self._write_lock:
            await self._request("DELETE", await self._dns_path(record_id))


    # ------------------------------------------------- client-bound records

    async def _users(self) -> list[dict]:
        data = await self._request("GET", f"/api/s/{self._site_ref}/rest/user")
        return (data or {}).get("data", [])

    async def list_client_records(self) -> list[ClientDnsRecord]:
        """Local DNS records attached to client devices.

        These live on the client object, not in the DNS store, and are invisible
        to the DNS API. The gateway resolves them regardless.
        """
        return [
            ClientDnsRecord.model_validate(u)
            for u in await self._users()
            if u.get("local_dns_record")
        ]

    async def list_eligible_clients(self) -> list[EligibleClient]:
        """Clients with a fixed IP that carry no local DNS record yet."""
        out = []
        for u in await self._users():
            if u.get("local_dns_record") or not u.get("use_fixedip"):
                continue
            out.append(
                EligibleClient(
                    client_id=u["_id"],
                    name=u.get("name") or u.get("hostname") or u.get("mac", "?"),
                    mac=u.get("mac"),
                    fixed_ip=u.get("fixed_ip"),
                    network_name=u.get("last_connection_network_name"),
                )
            )
        return sorted(out, key=lambda c: c.name.lower())

    async def get_client_record(self, client_id: str) -> ClientDnsRecord | None:
        for u in await self._users():
            if u.get("_id") == client_id:
                return (
                    ClientDnsRecord.model_validate(u)
                    if u.get("local_dns_record")
                    else None
                )
        raise UnifiNotFound(404, None, f"no client {client_id}")

    async def set_client_record(
        self, client_id: str, hostname: str | None, enabled: bool | None
    ) -> None:
        """Set, rename, toggle, or clear a client's local DNS record.

        A partial body is accepted; the whole client object is not required.
        Passing `hostname=None` clears the record.
        """
        body: dict[str, object] = {}
        if hostname:
            body["local_dns_record"] = hostname.strip().lower()
            if enabled is not None:
                body["local_dns_record_enabled"] = enabled
        else:
            # Clearing. The enabled flag is forced off and the caller's value is
            # deliberately ignored: an empty hostname with the record still
            # enabled is rejected with `api.err.LocalDnsRecordMissing`, and the
            # caller usually passes the record's *previous* enabled state, which
            # is exactly the value that breaks it.
            body["local_dns_record"] = ""
            body["local_dns_record_enabled"] = False
        async with self._write_lock:
            await self._request(
                "PUT", f"/api/s/{self._site_ref}/rest/user/{client_id}", json=body
            )


    # ---------------------------------------------------------------- flows

    async def fetch_flows(self, max_pages: int = 90, page_size: int = 200) -> list[dict]:
        """Every flow the gateway currently holds, roughly five days' worth.

        Pagination keys go in the POST body and are camelCase. Query-string
        equivalents and snake_case variants are accepted and silently ignored,
        which reads as "pagination is broken" rather than "wrong key".
        """
        out: list[dict] = []
        seen: set[str] = set()
        page = 0
        while page < max_pages:
            data = await self._request(
                "POST",
                f"/v2/api/site/{self._site_ref}/traffic-flows",
                json={"pageNumber": page, "pageSize": page_size},
            )
            batch = (data or {}).get("data", [])
            if not batch:
                break
            for row in batch:
                fid = row.get("id")
                if fid and fid not in seen:
                    seen.add(fid)
                    out.append(row)
            if not (data or {}).get("has_next"):
                break
            page += 1
        return out

    # ------------------------------------------------------------- settings

    async def _setting(self, key: str) -> dict:
        data = await self._request("GET", f"/api/s/{self._site_ref}/rest/setting/{key}")
        rows = (data or {}).get("data", [])
        return rows[0] if rows else {}

    async def _write_setting(self, key: str, body: dict) -> dict:
        """Partial writes are accepted; the whole document is not required.

        `POST set/setting/{key}` is used rather than `PUT rest/setting/{key}/{id}`
        because it does not need the document `_id`. Both were verified working.
        """
        async with self._write_lock:
            data = await self._request(
                "POST", f"/api/s/{self._site_ref}/set/setting/{key}", json={**body, "key": key}
            )
        rows = (data or {}).get("data", [])
        return rows[0] if rows else {}

    async def get_doh(self) -> DohSettings:
        return DohSettings.model_validate(await self._setting("doh"))

    async def set_doh(self, body: dict) -> DohSettings:
        return DohSettings.model_validate(await self._write_setting("doh", body))

    async def get_ips(self) -> IpsSettings:
        return IpsSettings.model_validate(await self._setting("ips"))

    async def set_ips(self, body: dict) -> IpsSettings:
        return IpsSettings.model_validate(await self._write_setting("ips", body))

    async def get_traffic_flow(self) -> TrafficFlowSettings:
        return TrafficFlowSettings.model_validate(await self._setting("traffic_flow"))

    async def set_traffic_flow(self, body: dict) -> TrafficFlowSettings:
        return TrafficFlowSettings.model_validate(
            await self._write_setting("traffic_flow", body)
        )

    async def get_content_filters(self) -> list[ContentFilterProfile]:
        data = await self._request(
            "GET", f"/v2/api/site/{self._site_ref}/content-filtering"
        )
        return [ContentFilterProfile.model_validate(p) for p in (data or [])]

    async def update_content_filter(self, profile_id: str, patch: dict) -> ContentFilterProfile:
        """Update one content-filtering profile.

        The full profile object must be sent; a partial body is not merged. Only
        `PUT {base}/{id}` is accepted -- PUT on the collection and POST on the
        item both return 405.

        This is the real write path for custom block and allow lists. The
        `dns_filters` array on the `ips` setting looks like it should work and
        does not: every write form returns rc:ok and silently persists nothing.
        """
        current = None
        for p in await self.get_content_filters():
            if p.id == profile_id:
                current = p
                break
        if current is None:
            raise UnifiNotFound(404, None, f"no content filter profile {profile_id}")
        body = {**current.model_dump(by_alias=True, exclude_none=True), **patch}
        async with self._write_lock:
            await self._request(
                "PUT", f"/v2/api/site/{self._site_ref}/content-filtering/{profile_id}",
                json=body,
            )
        for p in await self.get_content_filters():
            if p.id == profile_id:
                return p
        raise UnifiNotFound(404, None, profile_id)

    async def content_filter_categories(self) -> list[str]:
        data = await self._request(
            "GET", f"/v2/api/site/{self._site_ref}/content-filtering/categories"
        )
        return list(data or [])

    async def get_networks(self) -> list[NetworkDns]:
        data = await self._request("GET", f"/api/s/{self._site_ref}/rest/networkconf")
        out = []
        for n in (data or {}).get("data", []):
            if n.get("purpose") == "wan":
                continue
            out.append(
                NetworkDns.model_validate(
                    {
                        **n,
                        "servers": [
                            n[f"dhcpd_dns_{i}"]
                            for i in (1, 2, 3, 4)
                            if n.get(f"dhcpd_dns_{i}")
                        ],
                        "domain_name": n.get("domain_name") or "",
                    }
                )
            )
        return out

    async def set_network_dns(
        self,
        network_id: str,
        enabled: bool,
        servers: list[str],
        domain_name: str | None = None,
    ) -> None:
        """Set the resolver a network hands out, and optionally its search domain.

        A partial body is accepted, so untouched fields are left alone. Passing
        `domain_name=None` leaves the search domain as it was; passing "" clears it.
        """
        body: dict[str, object] = {"dhcpd_dns_enabled": enabled}
        for i in (1, 2, 3, 4):
            body[f"dhcpd_dns_{i}"] = servers[i - 1] if enabled and len(servers) >= i else ""
        if domain_name is not None:
            body["domain_name"] = domain_name.strip().rstrip(".").lower()
        async with self._write_lock:
            await self._request(
                "PUT", f"/api/s/{self._site_ref}/rest/networkconf/{network_id}", json=body
            )


def _looks_like_uuid(value: str) -> bool:
    parts = value.split("-")
    return len(parts) == 5 and all(
        len(p) == n and all(c in "0123456789abcdefABCDEF" for c in p)
        for p, n in zip(parts, (8, 4, 4, 4, 12))
    )

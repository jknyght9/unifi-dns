"""Import local DNS records from an external resolver. Pi-hole first.

Two ways in, because Pi-hole deployments differ and the API changed shape
between v5 and v6:

  - **Live fetch** from the Pi-hole API. v6 uses a session from
    `POST /api/auth`; v5 uses an `auth` token on `admin/api.php`.
  - **Paste or upload** the underlying files, which is the reliable fallback
    when the API is unreachable, the password is unknown, or Pi-hole is already
    switched off:
      `/etc/pihole/custom.list`                 hosts format, A records
      `/etc/dnsmasq.d/05-pihole-custom-cname.conf`  `cname=alias,target`

Everything converges on the same normalised list, so the preview and apply paths
do not care which source produced it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from ipaddress import ip_address

import httpx

from app.schemas.unifi import ARecord, AAAARecord, CNAMERecord, DnsRecord


@dataclass
class ImportedRecord:
    fqdn: str
    kind: str  # "A" | "AAAA" | "CNAME"
    value: str
    source: str = ""

    def to_unifi(self, ttl: int = 300) -> DnsRecord:
        if self.kind == "A":
            return ARecord(domain=self.fqdn, ipv4Address=self.value, ttlSeconds=ttl)
        if self.kind == "AAAA":
            return AAAARecord(domain=self.fqdn, ipv6Address=self.value, ttlSeconds=ttl)
        return CNAMERecord(domain=self.fqdn, targetDomain=self.value, ttlSeconds=ttl)


@dataclass
class ImportResult:
    records: list[ImportedRecord] = field(default_factory=list)
    skipped: list[dict] = field(default_factory=list)
    source: str = ""


_HOST_RE = re.compile(r"^\s*([0-9a-fA-F:.]+)\s+(.+?)\s*$")
_CNAME_RE = re.compile(r"^\s*cname\s*=\s*([^,]+)\s*,\s*([^,\s]+)")


def parse_hosts(text: str) -> ImportResult:
    """Parse `custom.list` / `/etc/hosts` format: `<ip> <name> [name...]`."""
    out = ImportResult(source="hosts")
    for lineno, raw in enumerate(text.splitlines(), 1):
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        m = _HOST_RE.match(line)
        if not m:
            out.skipped.append({"line": lineno, "text": raw.strip(), "why": "unparseable"})
            continue
        addr, names = m.group(1), m.group(2).split()
        try:
            ip = ip_address(addr)
        except ValueError:
            out.skipped.append({"line": lineno, "text": raw.strip(), "why": "bad IP"})
            continue
        kind = "A" if ip.version == 4 else "AAAA"
        for name in names:
            name = name.rstrip(".").lower()
            if not name or name in ("localhost",):
                continue
            out.records.append(ImportedRecord(name, kind, str(ip), "custom.list"))
    return out


def parse_cnames(text: str) -> ImportResult:
    """Parse dnsmasq `cname=alias,target` lines.

    Pi-hole allows a TTL as a third field; UniFi has no equivalent on the import
    path, so it is dropped and the record takes the chosen default.
    """
    out = ImportResult(source="cname")
    for lineno, raw in enumerate(text.splitlines(), 1):
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        m = _CNAME_RE.match(line)
        if not m:
            out.skipped.append({"line": lineno, "text": raw.strip(), "why": "unparseable"})
            continue
        alias, target = m.group(1).strip().rstrip("."), m.group(2).strip().rstrip(".")
        for a in alias.split(","):
            a = a.strip().lower()
            if a:
                out.records.append(ImportedRecord(a, "CNAME", target.lower(), "cname conf"))
    return out


async def fetch_pihole(
    base_url: str, password: str | None, token: str | None, verify_tls: bool = False
) -> ImportResult:
    """Pull custom DNS and CNAME entries from a live Pi-hole.

    Tries the v6 session API first, then falls back to the v5 token API. The
    error from whichever attempt got furthest is surfaced, since a v6 box with a
    wrong password and a v5 box with no token fail very differently.
    """
    base = base_url.rstrip("/")
    async with httpx.AsyncClient(verify=verify_tls, timeout=20, follow_redirects=True) as c:
        if password:
            try:
                return await _fetch_v6(c, base, password)
            except Exception as exc:  # noqa: BLE001 - fall through to v5
                v6_error = str(exc)
        else:
            v6_error = "no password supplied for the v6 API"
        if token:
            return await _fetch_v5(c, base, token)
        raise RuntimeError(
            f"could not read Pi-hole. v6 attempt: {v6_error}. "
            "No v5 API token supplied either."
        )


async def _fetch_v6(c: httpx.AsyncClient, base: str, password: str) -> ImportResult:
    auth = await c.post(f"{base}/api/auth", json={"password": password})
    auth.raise_for_status()
    sid = (auth.json().get("session") or {}).get("sid")
    if not sid:
        raise RuntimeError("Pi-hole v6 accepted the request but returned no session id")
    headers = {"sid": sid}
    out = ImportResult(source="pihole-v6")
    hosts = await c.get(f"{base}/api/config/dns/hosts", headers=headers)
    hosts.raise_for_status()
    for entry in _dig(hosts.json(), "hosts"):
        out.records.extend(parse_hosts(entry).records)
    cn = await c.get(f"{base}/api/config/dns/cnameRecords", headers=headers)
    if cn.status_code == 200:
        for entry in _dig(cn.json(), "cnameRecords"):
            parts = [p.strip() for p in str(entry).split(",")]
            if len(parts) >= 2:
                out.records.append(
                    ImportedRecord(parts[0].lower(), "CNAME", parts[1].lower(), "pihole-v6")
                )
    try:
        await c.delete(f"{base}/api/auth", headers=headers)  # release the session
    except Exception:  # noqa: BLE001 - best effort
        pass
    return out


async def _fetch_v5(c: httpx.AsyncClient, base: str, token: str) -> ImportResult:
    out = ImportResult(source="pihole-v5")
    r = await c.get(f"{base}/admin/api.php",
                    params={"customdns": "", "action": "get", "auth": token})
    r.raise_for_status()
    for row in (r.json() or {}).get("data", []):
        if len(row) >= 2:
            out.records.append(ImportedRecord(str(row[1]).lower(), _kind(row[0]), str(row[0]), "pihole-v5"))
    r = await c.get(f"{base}/admin/api.php",
                    params={"customcname": "", "action": "get", "auth": token})
    if r.status_code == 200:
        for row in (r.json() or {}).get("data", []):
            if len(row) >= 2:
                out.records.append(
                    ImportedRecord(str(row[0]).lower(), "CNAME", str(row[1]).lower(), "pihole-v5")
                )
    if not out.records:
        raise RuntimeError("Pi-hole v5 API returned no records; check the auth token")
    return out


def _kind(addr: str) -> str:
    try:
        return "A" if ip_address(addr).version == 4 else "AAAA"
    except ValueError:
        return "A"


def _dig(payload: object, key: str) -> list[str]:
    """The v6 config API nests values under config.dns.<key>; older builds flatten it."""
    if isinstance(payload, dict):
        if key in payload and isinstance(payload[key], list):
            return [str(x) for x in payload[key]]
        for v in payload.values():
            found = _dig(v, key)
            if found:
                return found
    return []

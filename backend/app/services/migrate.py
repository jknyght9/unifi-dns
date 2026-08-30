"""Import local DNS records from an external resolver.

Supports Pi-hole and Technitium, plus a paste path that accepts the underlying
files. The paste path matters more than it looks: it works when the source
server is already switched off, when its credentials are lost, and for any
resolver not explicitly supported, since almost all of them export RFC 1035
zone files.

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

from app.schemas.unifi import (
    AAAARecord,
    ARecord,
    CNAMERecord,
    DnsRecord,
    MXRecord,
    SRVRecord,
    TXTRecord,
    split_srv_fqdn,
)


@dataclass
class ImportedRecord:
    fqdn: str
    kind: str  # one of SUPPORTED
    value: str
    source: str = ""
    #: MX and SRV priority. SRV also uses weight and port.
    priority: int = 10
    weight: int = 0
    port: int = 0

    def to_unifi(self, ttl: int = 300) -> DnsRecord:
        """Build the Integration v1 record for this entry.

        Every supported type is handled explicitly. An earlier version fell
        through to CNAME for anything that was not A or AAAA, which was
        harmless while Pi-hole was the only source (it produces nothing else)
        and silently corrupted MX, TXT and SRV as soon as zone files arrived.
        """
        if self.kind == "A":
            return ARecord(domain=self.fqdn, ipv4Address=self.value, ttlSeconds=ttl)
        if self.kind == "AAAA":
            return AAAARecord(domain=self.fqdn, ipv6Address=self.value, ttlSeconds=ttl)
        if self.kind == "CNAME":
            return CNAMERecord(domain=self.fqdn, targetDomain=self.value, ttlSeconds=ttl)
        if self.kind == "MX":
            # UniFi rejects ttlSeconds on MX, TXT and SRV; the model omits it.
            return MXRecord(domain=self.fqdn, mailServerDomain=self.value,
                            priority=self.priority)
        if self.kind == "TXT":
            return TXTRecord(domain=self.fqdn, text=self.value)
        if self.kind == "SRV":
            service, protocol, zone = split_srv_fqdn(self.fqdn)
            return SRVRecord(domain=zone, service=service, protocol=protocol,
                             serverDomain=self.value, priority=self.priority,
                             weight=self.weight, port=self.port)
        raise ValueError(f"unsupported record type {self.kind!r}")


#: Record types this app can represent on UniFi. Everything else in a zone file
#: (SOA, NS, DNSKEY, RRSIG, NSEC, CAA and friends) is reported as skipped rather
#: than silently dropped, so the operator can see what did not come across.
SUPPORTED = ("A", "AAAA", "CNAME", "MX", "TXT", "SRV")


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


# --------------------------------------------------------------- Technitium

async def fetch_technitium(
    base_url: str,
    token: str | None = None,
    username: str | None = None,
    password: str | None = None,
    verify_tls: bool = False,
) -> ImportResult:
    """Pull every user-created zone from a Technitium DNS server.

    Authenticates with an API token if given, otherwise logs in with username
    and password to obtain a session token. Tokens go in an `Authorization:
    Bearer` header, which v15 requires; older builds also accept `?token=`, and
    both are sent so one client works across versions.

    Zones flagged `internal` are skipped. Those are the built-in reverse and
    localhost zones every Technitium install has, and importing them would fill
    UniFi with records nobody asked for.
    """
    base = base_url.rstrip("/")
    out = ImportResult(source="technitium")

    async with httpx.AsyncClient(verify=verify_tls, timeout=30, follow_redirects=True) as c:
        if not token:
            if not (username and password):
                raise RuntimeError("supply an API token, or a username and password")
            r = await c.get(f"{base}/api/user/login",
                            params={"user": username, "pass": password, "includeInfo": "false"})
            r.raise_for_status()
            body = r.json()
            token = body.get("token") or (body.get("response") or {}).get("token")
            if not token:
                raise RuntimeError(
                    f"login succeeded but returned no token: {str(body)[:160]}")

        headers = {"Authorization": f"Bearer {token}"}

        async def get(path: str, **params):
            r = await c.get(f"{base}{path}",
                            params={**params, "token": token}, headers=headers)
            r.raise_for_status()
            return r.json()

        listing = await get("/api/zones/list")
        zones = (listing.get("response") or {}).get("zones", [])
        if not zones:
            raise RuntimeError("authenticated, but the server reports no zones")

        for z in zones:
            name = (z.get("name") or "").strip()
            if not name or z.get("internal") or z.get("disabled"):
                continue
            if name.endswith((".arpa", ".arpa.")):
                continue
            data = await get("/api/zones/records/get",
                             domain=name, zone=name, listZone="true")
            for rec in (data.get("response") or {}).get("records", []):
                _absorb_technitium_record(out, rec)
    return out


def _absorb_technitium_record(out: ImportResult, rec: dict) -> None:
    """Translate one Technitium record into the common shape.

    Technitium nests type-specific values under `rData` with per-type key names,
    so each supported type is mapped explicitly rather than guessed at.
    """
    rtype = (rec.get("type") or "").upper()
    name = (rec.get("name") or "").rstrip(".").lower()
    rdata = rec.get("rData") or {}
    label = f"{rtype} {name or '@'}"

    if rec.get("disabled"):
        out.skipped.append({"text": label, "why": "disabled on the source server"})
        return
    if rtype not in SUPPORTED:
        out.skipped.append({"text": label, "why": f"{rtype} records are not supported by UniFi"})
        return
    if not name:
        out.skipped.append({"text": label, "why": "record has no name"})
        return

    if rtype in ("A", "AAAA"):
        value = rdata.get("ipAddress")
    elif rtype == "CNAME":
        value = rdata.get("cname")
    elif rtype == "MX":
        value = rdata.get("exchange")
    elif rtype == "TXT":
        value = rdata.get("text") or " ".join(rdata.get("splitText") or [])
    else:  # SRV
        value = rdata.get("target")

    if not value:
        out.skipped.append({"text": label, "why": f"no value found in rData for {rtype}"})
        return

    out.records.append(
        ImportedRecord(
            name,
            rtype,
            str(value) if rtype == "TXT" else str(value).rstrip(".").lower(),
            "technitium",
            priority=int(rdata.get("preference") or rdata.get("priority") or 10),
            weight=int(rdata.get("weight") or 0),
            port=int(rdata.get("port") or 0),
        )
    )


# ------------------------------------------------------- RFC 1035 zone files

_ZONE_TOKEN = re.compile(r'"[^"]*"|\S+')
_CLASSES = {"IN", "CH", "HS", "CS"}


def _strip_comment(raw: str) -> str:
    """Remove a trailing `;` comment, ignoring semicolons inside quoted text.

    TXT records routinely contain semicolons (DKIM and SPF values especially),
    so a naive split on `;` corrupts them.
    """
    out, in_quotes = [], False
    for ch in raw:
        if ch == '"':
            in_quotes = not in_quotes
        elif ch == ";" and not in_quotes:
            break
        out.append(ch)
    return "".join(out).rstrip()


def parse_zone_file(text: str, origin: str = "") -> ImportResult:
    """Parse an RFC 1035 zone file.

    Covers Technitium's zone export, BIND, PowerDNS and anything else that
    speaks the standard format. Handles `$ORIGIN`, `$TTL`, `@`, relative names,
    an omitted name inheriting the previous record's, and parenthesised
    multi-line records.
    """
    out = ImportResult(source="zone file")
    current_origin = origin.rstrip(".").lower()
    last_name = ""
    buffer, depth, start_line = "", 0, 0

    for lineno, raw in enumerate(text.splitlines(), 1):
        line = _strip_comment(raw)
        if not line and depth == 0:
            continue
        if depth == 0:
            start_line = lineno
        depth += line.count("(") - line.count(")")
        buffer = f"{buffer} {line}".strip() if buffer else line
        if depth > 0:
            continue
        entry, buffer = buffer.replace("(", " ").replace(")", " ").strip(), ""
        if not entry:
            continue

        if entry.upper().startswith("$ORIGIN"):
            current_origin = entry.split()[1].rstrip(".").lower()
            continue
        if entry.upper().startswith("$TTL"):
            continue
        if entry.startswith("$"):
            out.skipped.append({"line": start_line, "text": entry,
                                "why": "unsupported directive"})
            continue

        tokens = [t for t in _ZONE_TOKEN.findall(entry) if t]
        if not tokens:
            continue

        # An entry starting with whitespace inherits the previous owner name.
        if entry[:1].isspace() or re.match(r"^\d+$", tokens[0]) or tokens[0].upper() in _CLASSES:
            name = last_name
        else:
            name = tokens.pop(0)
            last_name = name

        while tokens and (tokens[0].isdigit() or tokens[0].upper() in _CLASSES):
            tokens.pop(0)
        if not tokens:
            out.skipped.append({"line": start_line, "text": entry, "why": "no record type"})
            continue

        rtype = tokens.pop(0).upper()
        fqdn = _qualify(name, current_origin)
        if not fqdn:
            out.skipped.append({"line": start_line, "text": entry,
                                "why": "relative name with no $ORIGIN; set one above"})
            continue
        if rtype not in SUPPORTED:
            out.skipped.append({"line": start_line, "text": f"{rtype} {fqdn}",
                                "why": f"{rtype} records are not supported by UniFi"})
            continue
        if not tokens:
            out.skipped.append({"line": start_line, "text": entry, "why": "no value"})
            continue

        priority, weight, port = 10, 0, 0
        if rtype in ("A", "AAAA"):
            value = tokens[0]
        elif rtype == "CNAME":
            value = _qualify(tokens[0], current_origin)
        elif rtype == "MX":
            value = _qualify(tokens[-1], current_origin)
            if len(tokens) >= 2 and tokens[0].isdigit():
                priority = int(tokens[0])
        elif rtype == "TXT":
            value = " ".join(t.strip('"') for t in tokens)
        else:  # SRV: priority weight port target
            value = _qualify(tokens[-1], current_origin)
            nums = [t for t in tokens[:-1] if t.isdigit()]
            if len(nums) >= 3:
                priority, weight, port = int(nums[0]), int(nums[1]), int(nums[2])

        if not value:
            out.skipped.append({"line": start_line, "text": entry, "why": "unresolvable value"})
            continue
        out.records.append(
            ImportedRecord(fqdn, rtype, value, "zone file",
                           priority=priority, weight=weight, port=port)
        )

    return out


def _qualify(name: str, origin: str) -> str:
    """Resolve a possibly-relative zone-file name against the current origin."""
    n = name.strip().lower()
    if n in ("@", ""):
        return origin
    if n.endswith("."):
        return n.rstrip(".")
    return f"{n}.{origin}" if origin else ""

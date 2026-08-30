#!/usr/bin/env python3
"""A stand-in UniFi gateway, for development and screenshots.

Serves every endpoint `unifi-dns` touches, with plausible fake data, so the app
can be run and demonstrated without hardware and without putting a real
network's device names and addresses on screen.

    python demo/mock_unifi.py                 # listens on :8443
    UNIFI_HOST=http://localhost:8443 UNIFI_API_KEY=demo \
      uvicorn app.main:app --app-dir backend

State is in memory: restart to reset. Writes behave like the real thing where
that matters, including the constraints that are easy to get wrong:

  - `ttlSeconds` is rejected on MX/TXT/SRV
  - `id` in a PUT body is rejected
  - clearing a client record while it is still enabled is rejected
  - `ips.dns_filters` and `ips.ad_blocking_*` accept writes and persist nothing
"""

from __future__ import annotations

import json
import re
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

SITE_ID = "00000000-0000-0000-0000-000000000000"
ADDR = ("0.0.0.0", 8443)

TTL_CAPABLE = {"A_RECORD", "AAAA_RECORD", "CNAME_RECORD"}


def rid() -> str:
    return str(uuid.uuid4())


def _a(domain, ip, ttl=300):
    return {"type": "A_RECORD", "id": rid(), "enabled": True,
            "metadata": {"origin": "USER_DEFINED"}, "domain": domain,
            "ipv4Address": ip, "ttlSeconds": ttl}


RECORDS = [
    _a("nas.example.internal", "10.10.0.10"),
    _a("plex.example.internal", "10.10.0.10"),
    _a("sonarr.example.internal", "10.10.0.10"),
    _a("radarr.example.internal", "10.10.0.10"),
    _a("traefik.example.internal", "10.10.0.10"),
    _a("grafana.example.internal", "10.10.0.11"),
    _a("prometheus.example.internal", "10.10.0.11"),
    _a("printer.example.internal", "10.10.0.30"),
    _a("kvm.lab.example", "10.10.30.20"),
    _a("build.lab.example", "10.10.30.21"),
    {"type": "CNAME_RECORD", "id": rid(), "enabled": True,
     "metadata": {"origin": "USER_DEFINED"}, "domain": "git.example.internal",
     "targetDomain": "nas.example.internal", "ttlSeconds": 300},
    {"type": "TXT_RECORD", "id": rid(), "enabled": True,
     "metadata": {"origin": "USER_DEFINED"}, "domain": "example.internal",
     "text": "v=spf1 -all"},
    _a("legacy.oldname.lan", "10.10.0.10"),
]

NETWORKS = [
    {"_id": "net-home", "name": "Home", "purpose": "corporate", "vlan": 10,
     "ip_subnet": "10.10.0.1/24", "domain_name": "example.internal",
     "dhcpd_dns_enabled": True, "dhcpd_dns_1": "10.10.0.1", "dhcpd_dns_2": "1.1.1.1"},
    {"_id": "net-iot", "name": "IoT", "purpose": "corporate", "vlan": 40,
     "ip_subnet": "10.10.40.1/24", "domain_name": "iot.example.internal",
     "dhcpd_dns_enabled": False},
    {"_id": "net-cam", "name": "Cameras", "purpose": "corporate", "vlan": 30,
     "ip_subnet": "10.10.30.1/24", "domain_name": "cam.example.local",
     "dhcpd_dns_enabled": False},
    {"_id": "net-guest", "name": "Guest", "purpose": "guest", "vlan": 20,
     "ip_subnet": "10.10.20.1/24", "domain_name": "", "dhcpd_dns_enabled": False},
    {"_id": "net-wan", "name": "Internet", "purpose": "wan"},
]

USERS = [
    {"_id": "cli-ha", "mac": "aa:bb:cc:00:00:01", "name": "home-automation",
     "hostname": "home-automation", "fixed_ip": "10.10.0.20", "use_fixedip": True,
     "local_dns_record": "ha.example.internal", "local_dns_record_enabled": True,
     "last_connection_network_name": "Home", "oui": "Example"},
    {"_id": "cli-cam", "mac": "aa:bb:cc:00:00:02", "name": "doorbell",
     "hostname": "doorbell", "fixed_ip": "10.10.30.40", "use_fixedip": True,
     "local_dns_record": "doorbell.cam.example.local",
     "local_dns_record_enabled": False,
     "last_connection_network_name": "Cameras", "oui": "Example"},
    {"_id": "cli-nas", "mac": "aa:bb:cc:00:00:03", "name": "nas",
     "hostname": "nas", "fixed_ip": "10.10.0.10", "use_fixedip": True,
     "last_connection_network_name": "Home", "oui": "Example"},
]

SETTINGS = {
    "doh": {"_id": "s-doh", "key": "doh", "state": "auto",
            "server_names": ["cloudflare"], "custom_servers": []},
    "traffic_flow": {"_id": "s-tf", "key": "traffic_flow",
                     "gateway_dns_enabled": True, "enabled_allowed_traffic": True,
                     "unifi_services_enabled": True,
                     "unifi_device_management_enabled": False},
    "ips": {"_id": "s-ips", "key": "ips", "ad_blocking_enabled": True,
            "dns_filtering": True,
            "ad_blocking_configurations": [{"network_id": "net-home"},
                                           {"network_id": "net-iot"}],
            "dns_filters": [
                {"network_id": "net-home", "filter": "none", "blocked_tld": [],
                 "blocked_sites": [], "allowed_sites": [], "name": "",
                 "description": "", "version": "v4"},
                {"network_id": "net-iot", "filter": "none", "blocked_tld": [],
                 "blocked_sites": [], "allowed_sites": [], "name": "",
                 "description": "", "version": "v4"}],
            "advanced_filtering_preference": "disabled",
            "content_filtering_blocking_page_enabled": True},
}

CONTENT_FILTERS = [
    {"_id": "cf-home", "name": "Home", "enabled": True,
     "categories": ["ADVERTISEMENT"], "allow_list": [], "block_list": [],
     "client_macs": [], "network_ids": ["net-home"], "safe_search": [],
     "schedule": {"mode": "ALWAYS", "repeat_on_days": [], "time_all_day": False}},
    {"_id": "cf-iot", "name": "IoT", "enabled": True,
     "categories": ["ADVERTISEMENT"], "allow_list": [], "block_list": [],
     "client_macs": [], "network_ids": ["net-iot"], "safe_search": [],
     "schedule": {"mode": "ALWAYS", "repeat_on_days": [], "time_all_day": False}},
]

CATEGORIES = ["ADVERTISEMENT", "ADULT", "GAMBLING", "MALWARE", "PHISHING",
              "SOCIAL_NETWORKS", "STREAMING", "CRYPTOMINING", "DNS_TUNNELING",
              "HATE_SPEECH_AND_EXTREMISM", "ARTIFICIAL_INTELLIGENCE", "GAMING"]


def _flows() -> list[dict]:
    """A week of believable activity.

    Spread across real hours with a diurnal shape, because a dashboard rendered
    from flows all stamped within one minute looks broken rather than quiet.
    """
    import math
    import time

    now = int(time.time() * 1000)
    hour = 3_600_000
    out, n = [], 0

    def flow(src_ip, src_name, net, dst_ip, port, domains, action, policy, count, ts):
        nonlocal n
        n += 1
        pol = ([{"type": "AD_BLOCKING", "internal_type": "AD_BLOCKING", "name": net}]
               if policy == "ad" else
               [{"type": "PROTECTION", "internal_type": "REGION_BLOCKING",
                 "name": "Region Blocking"}] if policy == "region" else
               [{"type": "FIREWALL", "internal_type": "CONNTRACK"}])
        out.append({
            "id": f"cache-{n:06d}", "action": action, "count": count,
            "service": "DNS" if port == 53 else "HTTPS",
            "protocol": "UDP" if port == 53 else "TCP", "risk": "low",
            "time": ts, "flow_start_time": ts - 1200, "flow_end_time": ts,
            "duration_milliseconds": 1200, "direction": "outgoing",
            "next_ai": [], "policies": pol,
            "traffic_data": {"bytes_total": 300 * count, "bytes_tx": 180,
                             "bytes_rx": 120, "packets_total": 4},
            "source": {"ip": src_ip, "mac": f"aa:bb:cc:11:22:{n % 100:02d}",
                       "client_name": src_name, "host_name": src_name,
                       "network_name": net, "zone_name": "Internal",
                       "client_oui": "Example", "port": 40000 + n},
            "destination": {"ip": dst_ip, "port": port, "domains": domains,
                            "zone_name": "Gateway" if dst_ip.endswith(".0.1") else "WAN"},
        })

    BLOCKED = ["ads.example-adnetwork.com", "telemetry.example-tv.net",
               "metrics.example-app.io", "tracker.example-cdn.com",
               "beacon.example-analytics.com", "pixel.example-ads.net"]
    ALLOWED = ["github.com", "api.example-service.com", "cdn.example-media.net",
               "updates.example-os.org", "mail.example.com", "example.internal"]

    for h in range(168):                       # seven days back, hour by hour
        ts = now - h * hour
        # Busier in the evening, quieter overnight.
        of_day = (h % 24)
        busy = 0.35 + 0.65 * max(0.0, math.sin((of_day - 6) / 24 * math.pi * 2) * 0.5 + 0.5)
        for i, dom in enumerate(BLOCKED):
            c = int((7 - i) * busy * 1.6)
            if c:
                flow("10.10.40.24", "smart-tv", "IoT", "127.0.0.1", 53, [dom],
                     "blocked", "ad", c, ts - i * 90_000)
        for i, dom in enumerate(ALLOWED):
            c = int((6 - i) * busy * 2.4)
            if c:
                flow("10.10.0.25", "workstation", "Home", "10.10.0.1", 53, [dom],
                     "allowed", "conntrack", c, ts - i * 70_000)
        if h % 3 == 0:
            flow("10.10.0.31", "laptop", "Home", "1.1.1.1", 443,
                 ["one.one.one.one"], "allowed", "conntrack", 2, ts - 300_000)
        if h % 8 == 0:
            flow("10.10.30.40", "doorbell", "Cameras", "9.9.9.9", 53, [],
                 "allowed", "conntrack", 3, ts - 600_000)
        if h % 2 == 0:
            flow("203.0.113.77", "scan-source", "Home", "10.10.0.1", 443, [],
                 "blocked", "region", 9, ts - 900_000)
    return out


FLOWS = _flows()


def err(code: str, message: str) -> dict:
    return {"statusCode": 400, "statusName": "BAD_REQUEST", "code": code,
            "message": message, "requestId": rid()}


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):  # quieter than the default
        print(f"  mock  {self.command:6} {self.path.split('?')[0]}")

    # ---------------------------------------------------------------- helpers
    def send(self, payload, status=200):
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def body(self) -> dict:
        n = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(n) or "{}") if n else {}

    @property
    def route(self) -> str:
        return self.path.split("?")[0]

    # ------------------------------------------------------------------- verbs
    def do_GET(self):
        p = self.route
        if p.endswith("/integration/v1/info"):
            return self.send({"applicationVersion": "10.6.101"})
        if p.endswith("/integration/v1/sites"):
            return self.send({"offset": 0, "limit": 25, "count": 1, "totalCount": 1,
                              "data": [{"id": SITE_ID, "internalReference": "default",
                                        "name": "Default"}]})
        if "/dns/policies" in p:
            m = re.search(r"/dns/policies/([0-9a-f-]{36})$", p)
            if m:
                r = next((x for x in RECORDS if x["id"] == m.group(1)), None)
                return self.send(r) if r else self.send(err("not-found", "no such record"), 404)
            return self.send({"offset": 0, "limit": 200, "count": len(RECORDS),
                              "totalCount": len(RECORDS), "data": RECORDS})
        if p.endswith("/api/self"):
            return self.send({"data": [{"name": "Example Admin", "is_owner": True,
                                        "is_super": True, "admin_id": "admin-1"}]})
        if p.endswith("/rest/user"):
            return self.send({"data": USERS})
        if p.endswith("/rest/networkconf"):
            return self.send({"data": NETWORKS})
        if "/rest/setting/" in p:
            return self.send({"meta": {"rc": "ok"},
                              "data": [SETTINGS[p.rsplit("/", 1)[1]]]})
        if p.endswith("/rest/setting"):
            return self.send({"meta": {"rc": "ok"}, "data": list(SETTINGS.values())})
        if p.endswith("/content-filtering/categories"):
            return self.send(CATEGORIES)
        if p.endswith("/content-filtering"):
            return self.send(CONTENT_FILTERS)
        return self.send(err("not-found", f"no route {p}"), 404)

    def do_POST(self):
        p, b = self.route, self.body()
        if p.endswith("/traffic-flows"):
            size = int(b.get("pageSize") or 50)
            page = int(b.get("pageNumber") or 0)
            chunk = FLOWS[page * size:(page + 1) * size]
            total_pages = max(1, -(-len(FLOWS) // size))
            return self.send({"data": chunk, "has_next": page + 1 < total_pages,
                              "or_more": False, "page_number": page,
                              "total_element_count": len(FLOWS),
                              "total_page_count": total_pages})
        if p.endswith("/dns/policies"):
            if "id" in b:
                return self.send(err("api.request.unknown-property",
                                     "Unknown request body property '$.id'"), 400)
            if b.get("type") not in TTL_CAPABLE and "ttlSeconds" in b:
                return self.send(err("api.request.unknown-property",
                                     "Unknown request body property '$.ttlSeconds'"), 400)
            rec = {**b, "id": rid(), "metadata": {"origin": "USER_DEFINED"}}
            RECORDS.append(rec)
            return self.send(rec, 201)
        if "/set/setting/" in p:
            key = p.rsplit("/", 1)[1]
            cur = SETTINGS.get(key, {})
            # ips.dns_filters and ips.ad_blocking_* are read-only mirrors on real
            # hardware: the write reports success and nothing changes.
            writable = {k: v for k, v in b.items()
                        if not (key == "ips" and k in
                                ("dns_filters", "ad_blocking_enabled",
                                 "ad_blocking_configurations"))}
            cur.update(writable)
            return self.send({"meta": {"rc": "ok"}, "data": [cur]})
        return self.send(err("not-found", f"no route {p}"), 404)

    def do_PUT(self):
        p, b = self.route, self.body()
        m = re.search(r"/dns/policies/([0-9a-f-]{36})$", p)
        if m:
            if "id" in b:
                return self.send(err("api.request.unknown-property",
                                     "Unknown request body property '$.id'"), 400)
            for i, r in enumerate(RECORDS):
                if r["id"] == m.group(1):
                    RECORDS[i] = {**b, "id": r["id"],
                                  "metadata": {"origin": "USER_DEFINED"}}
                    return self.send(RECORDS[i])
            return self.send(err("not-found", "no such record"), 404)
        m = re.search(r"/rest/user/([\w-]+)$", p)
        if m:
            for u in USERS:
                if u["_id"] == m.group(1):
                    if (b.get("local_dns_record") == ""
                            and b.get("local_dns_record_enabled") is True):
                        return self.send({"meta": {"rc": "error",
                                                   "msg": "api.err.LocalDnsRecordMissing"},
                                          "data": []}, 400)
                    u.update(b)
                    return self.send({"meta": {"rc": "ok"}, "data": [u]})
            return self.send(err("not-found", "no such client"), 404)
        m = re.search(r"/rest/networkconf/([\w-]+)$", p)
        if m:
            for nw in NETWORKS:
                if nw["_id"] == m.group(1):
                    nw.update(b)
                    return self.send({"meta": {"rc": "ok"}, "data": [nw]})
            return self.send(err("not-found", "no such network"), 404)
        m = re.search(r"/content-filtering/([\w-]+)$", p)
        if m:
            for i, cf in enumerate(CONTENT_FILTERS):
                if cf["_id"] == m.group(1):
                    if b.get("categories") == []:
                        return self.send(err("api.request.error",
                                             "categories must not be empty"), 400)
                    CONTENT_FILTERS[i] = {**cf, **b, "_id": cf["_id"]}
                    return self.send(CONTENT_FILTERS[i])
            return self.send(err("not-found", "no such profile"), 404)
        return self.send(err("not-found", f"no route {p}"), 404)

    def do_DELETE(self):
        m = re.search(r"/dns/policies/([0-9a-f-]{36})$", self.route)
        if m:
            for i, r in enumerate(RECORDS):
                if r["id"] == m.group(1):
                    RECORDS.pop(i)
                    return self.send({})
            return self.send(err("not-found", "no such record"), 404)
        return self.send(err("not-found", "no route"), 404)


if __name__ == "__main__":
    print(f"mock UniFi gateway on http://{ADDR[0]}:{ADDR[1]}")
    print(f"  {len(RECORDS)} records, {len(USERS)} clients, {len(FLOWS)} flows")
    print("  UNIFI_HOST=http://localhost:8443 UNIFI_API_KEY=demo")
    ThreadingHTTPServer(ADDR, Handler).serve_forever()

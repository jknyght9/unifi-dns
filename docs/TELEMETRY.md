# Flow telemetry

The Gateway DNS dashboard is built on UniFi's flow logging. This page describes
what that data actually contains, so the numbers on the dashboard can be read
correctly.

## Enabling it

**Settings > CyberSecure > Traffic Logging > Flow Logging > Additional Flows >
Gateway DNS.**

Corresponds to `traffic_flow.gateway_dns_enabled` in the settings API, and can be
toggled from the app's DNS Settings page. Most UniFi gateways support Traffic
Flows; Ubiquiti excludes UDR, UDR7, UDR 5G Max, Express, Express 7, base UDM,
UCG-Ultra and UXG-Lite.

## Reading it

```
POST /proxy/network/v2/api/site/{site}/traffic-flows
{ "pageNumber": 0, "pageSize": 200 }
```

Pagination keys go in the **body**, camelCase. Query-string equivalents and
snake_case variants are accepted and silently ignored, which presents as
"pagination is broken" rather than "wrong parameter name". Walk until
`has_next` is false, deduplicating on the stable `id`.

## What a DNS flow contains

```jsonc
{
  "service": "DNS", "protocol": "UDP", "action": "allowed",
  "id": "cache-...", "time": 1788093622142, "count": 1,
  "source": {
    "client_name": "workstation", "ip": "10.10.0.25",
    "mac": "aa:bb:cc:dd:ee:ff", "network_name": "Home",
    "zone_name": "Internal", "client_fingerprint": { "os_name": 24 }
  },
  "destination": {
    "domains": ["example.com"],       // the queried name
    "ip": "10.10.0.1", "port": 53
  },
  "policies": [ { "type": "AD_BLOCKING", "name": "Home" } ]
}
```

Queried domain, source identity down to OS fingerprint, network and firewall
zone, allow/block verdict, and **which policy acted**. In those respects it is
richer than a conventional DNS query log, which typically has no zone, no policy
attribution and no device fingerprint.

## What it is not

**These are aggregated connection flows, not individual queries.** Each row
carries a `count`. A flow table showing 1,000 rows does not mean 1,000 lookups
occurred, and totals here are lower than a true query log would report.

Roughly 64% of all flows carry a domain, rising to about 90% of DNS-classified
ones. Retention on the gateway is about five days.

**Therefore:** proportions, per-client attribution and per-policy attribution are
reliable. Absolute query volume is not, and is not comparable to Pi-hole's
counters. Every stats endpoint returns a `caveat` field saying so.

## Two distinctions the dashboard makes

**DNS filtering is separated from perimeter blocking.** A gateway blocks far more
inbound scan traffic (`REGION_BLOCKING`) than it does DNS lookups. Counting both
as "blocked" inflates the headline by an order of magnitude and means nothing to
someone asking what their ad blocking caught. The dashboard headlines
`AD_BLOCKING` plus sinkholed answers, and reports perimeter drops separately.

**Sinkholed answers are filtering working, not leaking.** A DNS flow with
destination `127.0.0.1` is the gateway answering with a null address. Counting
those as devices bypassing the resolver inverts their meaning, so bypass
detection excludes them.

## Bypass detection

A client is bypassing the resolver when it sends DNS to something that is not the
gateway. The gateway answers on **`.1` of every VLAN**, not only its management
address, so all of those count as "using the gateway". Treating one address as
the gateway makes every other VLAN's clients look like they are escaping it.

Three categories are counted: plaintext `:53` to a non-gateway address,
DNS-over-TLS on `:853`, and DNS-over-HTTPS on `:443` to a known public resolver.

DoH is the hardest to catch, by design: it is meant to be indistinguishable from
ordinary HTTPS. Detection here relies on known resolver addresses and reverse
names, so treat the DoH numbers as a floor rather than a complete picture.

## Related work

[`jmasarweh/unifi-log-insight`](https://github.com/jmasarweh/unifi-log-insight)
is a syslog receiver for UniFi routers with PostgreSQL storage, GeoIP and
threat-intel enrichment, and a React UI. It covers the observability side in far
more depth than this project does and is worth running alongside if flow
analysis rather than DNS management is the goal.

UniFi can also export flows in CEF to an external SIEM
(Integration > System Logging / SIEM), which is the better route if this data
needs to reach something larger.

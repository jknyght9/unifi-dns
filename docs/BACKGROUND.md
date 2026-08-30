# Background

## Why this exists

UniFi gateways gained local DNS records in Network 8.2, and the feature works.
The management interface does not keep up with it.

Records are stored as a **flat, unordered list**. There is no zone concept in the
data model, so the console has nothing to group by. In practice that means:

- no way to view or filter records by domain
- no sort, no search
- no bulk import, export, or edit
- no change history, and no way to undo a mistake
- no visibility of records created on a client device, which the DNS API does not
  return at all

This is workable at ten records and hostile at fifty. Everything in this project
follows from imposing the structure the storage model never had.

## On replacing Pi-hole

A UniFi gateway can plausibly replace Pi-hole for filtering. It runs dnsmasq
inline on the router, so there is no extra device in the resolution path and no
separate box to fail. Ad blocking, content filtering by category, custom
allow/block lists, and encrypted upstream DNS are all built in.

What is genuinely lost is **observability**. UniFi has no query log in the
Pi-hole sense. The flow telemetry this project reads is a partial substitute: it
gives blocked-versus-allowed, per-client attribution and per-policy attribution,
but it aggregates, so absolute query counts are not comparable. See
[TELEMETRY.md](TELEMETRY.md).

Also lost: blocklist subscription by URL, regex rules, and Pi-hole's group model.
Custom lists exist on UniFi but are entered per content-filtering profile and hit
a request payload ceiling well below the size of large public blocklists.

## What tends to bite people

**Clients must actually use the gateway.** Local records and filtering apply only
to devices that ask the gateway. Anything with a hardcoded resolver, DNS-over-TLS
enabled, or browser DoH switched on bypasses all of it silently. The dashboard's
bypass view exists because this is the most common reason a working
configuration appears not to work.

**DHCP resolvers are a set, not a failover chain.** Handing out a filtering
resolver alongside a public one does not give redundancy. Clients query whichever
they like, so some lookups bypass filtering at random. This is the single most
common misconfiguration and the app flags it.

**`.local` belongs to mDNS.** Reserved by RFC 6762; clients divert those lookups
to multicast rather than the gateway. Use `.internal`, which ICANN reserved
permanently for private use in board resolution 2024.07.29.06, or `home.arpa`
from RFC 8375.

## Prior art

Several tools write UniFi DNS records; none present them as zones.

| Project | Scope |
|---|---|
| [DNSControl](https://docs.dnscontrol.org/provider/unifi) | Declarative zone files pushed to UniFi. The closest existing answer to zone management, and a good fit if you want DNS in version control instead of a UI. |
| [external-dns-unifi-webhook](https://github.com/kashalls/external-dns-unifi-webhook) | ExternalDNS provider, for Kubernetes-driven records |
| [unifi-bulk-dns](https://github.com/denisvinciguerra/unifi-bulk-dns) | Bulk creation of A and CNAME records |
| [udm-python-tools](https://github.com/SpaceTerran/udm-python-tools) | CLI for records and firewall policy |

DNSControl in particular solves a real part of this problem well. This project
exists for the case where a UI, an audit trail, and filtering visibility are
wanted alongside the records themselves.

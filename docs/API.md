# UniFi DNS API reference

**Verified against live hardware 2026-08-29.** UDM Pro `example-udm` at
`192.0.2.1`, Network application **10.5.67**, UniFi OS **5.1.26**
(`UDMPRO.al324.v5.1.26.0bc0fe4.260715.2320`), site UUID
`00000000-0000-0000-0000-000000000000`.

Every behaviour below was confirmed by a create/read/update/delete round trip;
full request and response transcript in `probe-out/apitest.json`. Initial shapes
came from the DNSControl `providers/unifi` and ExternalDNS UniFi webhook sources.

**Bottom line: build on Integration v1 only.** It is present, complete, and
supports native updates. Legacy v2 is a view over the same store and is not
needed on this console.

## Authentication

Single header. No login flow, no CSRF token, no session cookie.

```
X-API-Key: <token>
Accept: application/json
Content-Type: application/json   # only when sending a body
```

Generate the token in the UniFi console: **Settings and Logs > Integrations > Create New API Key** (copy it once, it is not shown again).

The gateway uses a self signed certificate, so local clients need TLS
verification disabled (`curl -k`, Go `InsecureSkipVerify`, Python
`verify=False`).

## Base URLs

| Access | Base |
|---|---|
| Local | `https://192.0.2.1/proxy/network` |
| Cloud | `https://api.ui.com/v1/connector/consoles/{consoleID}/proxy/network` |

Everything below is relative to the base. Build the container for local
access; cloud access is a config flag for later.

---

## Integration API v1 (preferred)

Requires Network 10.1+. Feature detect with
`GET /integration/v1/sites/{siteId}/dns/policies?limit=1` and fall back
to legacy on failure. This is what DNSControl's `auto` mode does.

### Sites

```
GET /integration/v1/sites
GET /integration/v1/sites?filter=internalReference.eq('default')
```

Verified response on this console:

Response:
```json
{ "offset":0, "limit":25, "count":1, "totalCount":1,
  "data":[ { "id":"00000000-0000-0000-0000-000000000000",
             "internalReference":"default", "name":"Default" } ] }
```

You must resolve the site **UUID** here. The DNS policy endpoints take the
UUID, not the human `default` string.

**Filter DSL:** `field.eq('value')`. Single quotes wrap the value; an embedded
quote is escaped by doubling it. URL encode the whole expression.

Verified on `dns/policies`:

| Expression | Result |
|---|---|
| `type.eq('A_RECORD')` | works |
| `domain.eq('probe-a.claude.invalid')` | works, exact match only |
| `enabled.eq(false)` | works, bare boolean, no quotes |
| `domain.like('%claude.invalid')` | **returns 200 with 0 results, silently** |

`.like()` is the trap. It does not error, it returns an empty page. Never build
substring search on it. Load the full set and filter client side, which is
cheap at any realistic record count.

### List records

```
GET /integration/v1/sites/{siteId}/dns/policies?offset=0&limit=200
```

Page size caps at 200. Response is the same envelope as sites:
`{offset, limit, count, totalCount, data:[...]}`. Loop until
`offset >= totalCount` or `count == 0`.

### Read / create / update / delete

```
GET    /integration/v1/sites/{siteId}/dns/policies/{id}   # 200, single object
POST   /integration/v1/sites/{siteId}/dns/policies        # 201, returns created object
PUT    /integration/v1/sites/{siteId}/dns/policies/{id}   # 200, returns updated object
DELETE /integration/v1/sites/{siteId}/dns/policies/{id}   # 200
```

All four verified. `PUT` is a genuine native update: it returns the mutated
object with the **same `id`**, so IDs are stable across edits and optimistic UI
is safe. Create and update both echo the full record back, so no refetch is
needed after a write.

The record ID goes in the **path only**. Including `id` in the body returns
`400 api.request.unknown-property: Unknown request body property '$.id'`.

### Record object (polymorphic)

```jsonc
{
  "id": "<uuid>",                 // response only
  "type": "A_RECORD",             // A_RECORD AAAA_RECORD CNAME_RECORD
                                  // MX_RECORD TXT_RECORD SRV_RECORD
  "enabled": true,
  "domain": "nas.home.arpa",      // FQDN. For SRV this is the bare zone.
  "metadata": { "origin": "USER_DEFINED" },   // read only
  "ttlSeconds": 300,              // A/AAAA/CNAME ONLY

  // exactly one of these, by type:
  "ipv4Address": "192.0.2.20",       // A
  "ipv6Address": "fd00::20",         // AAAA
  "targetDomain": "nas.home.arpa",   // CNAME
  "mailServerDomain": "mx.example",  // MX
  "text": "v=spf1 -all",             // TXT
  "serverDomain": "sip.example",     // SRV

  "priority": 10,      // MX, SRV
  "weight": 5,         // SRV
  "port": 5060,        // SRV
  "service": "_sip",   // SRV, keeps leading underscore
  "protocol": "_tcp"   // SRV, keeps leading underscore
}
```

### Verified behaviour

| Case | Result |
|---|---|
| `ttlSeconds` on A/AAAA/CNAME | accepted |
| `ttlSeconds` on MX/TXT/SRV | **400** `Unknown request body property '$.ttlSeconds'` |
| `ttlSeconds: 1` | accepted, no floor enforced |
| Wildcard **A** `*.wild.example.com` | **accepted (201)** |
| Wildcard **CNAME** `*.probe.example.com` | **400** `domain must be valid domain` |
| Duplicate FQDN + type, different value | **accepted**, two rows, distinct IDs |
| `enabled: false` | accepted, record stored and returned |
| `ipv4Address: "not-an-ip"` | **400** `ipv4Address must be valid IP format` |
| `NS` record type | not offered on v1; legacy only |

**Traps:**
- Wildcard support is asymmetric. A records take a wildcard, CNAMEs do not.
  A wildcard A pointed at a reverse proxy covers most of what people want
  wildcard CNAMEs for, so this is less limiting than it first reads.
- **Duplicates are permitted.** Two A records on the same name is round robin,
  not an error. The UI must key on `id`, never on `domain + type`, and should
  visually group same-name rows rather than treat the second one as a mistake.
- Server side validation is real (IP format, domain format), so surface the
  API's `message` field directly rather than reimplementing validation.
- SRV splits `_sip._tcp.example.com` into `service` + `protocol` + `domain`.
  Recombine for display, split for write.

### Error envelope

```json
{ "statusCode": 400, "statusName": "BAD_REQUEST",
  "code": "api.request.unknown-property",
  "message": "Unknown request body property '$.ttlSeconds'",
  "timestamp": "2026-08-29T14:01:20.923137839Z",
  "requestPath": "/integration/v1/sites/{uuid}/dns/policies",
  "requestId": "c8be4b1c-906f-4e37-9da5-a16e70b2cee7" }
```

Consistent and machine readable. Map `code` to a UI hint, show `message`
verbatim, log `requestId`.

---

## Legacy v2 API (present but not needed here)

**Confirmed: both APIs are views over the same underlying store.** A record
created through v1 appears immediately in the legacy listing. The IDs differ
between them: v1 uses UUIDs (`c3c87451-2aa1-...`), legacy uses Mongo ObjectIDs
(`0123456789abcdef01234567`). Pick one API and stay on it; never mix IDs.


```
GET    /v2/api/site/{site}/static-dns          # site is "default", not a UUID
POST   /v2/api/site/{site}/static-dns
DELETE /v2/api/site/{site}/static-dns/{id}
```

There is **no reliable PUT**. DNSControl implements update as delete + create,
which means record IDs are not stable across an edit. Any UI built on the legacy
path must refetch after every write and must not cache IDs across a mutation.

Returns a bare JSON array (no envelope, no pagination).

### Record object (flat)

```jsonc
{
  "_id": "6712...",
  "enabled": true,
  "key": "nas.home.arpa",     // FQDN
  "record_type": "A",         // A AAAA CNAME MX TXT SRV NS
  "value": "192.0.2.20",
  "ttl": 300,                 // 0 = default (300)
  "port": 0,                  // SRV
  "priority": 0,              // MX, SRV
  "weight": 0                 // SRV
}
```

**Per-type field whitelist.** UniFi rejects extra fields, so send only:

| Type | Allowed fields |
|---|---|
| A, AAAA, CNAME, NS | enabled, key, record_type, value, ttl |
| MX | enabled, key, record_type, value, priority |
| TXT | enabled, key, record_type, value |
| SRV | enabled, key, record_type, value, priority, weight, port |

Trailing dots are stripped from CNAME/MX/SRV/NS targets before sending.

---

## Records live in TWO places, not one

Corrected 2026-08-30 after a record created in the console failed to appear.

A local DNS record on a UniFi gateway can live in either of two unrelated
collections, and **the DNS API only shows one of them**.

### 1. The DNS store

`integration/v1/sites/{siteId}/dns/policies`. Free-standing records, created in
Settings > Routing > DNS. Everything above this section describes these.

### 2. On the client object

The "Local DNS Record" toggle on a **Client Devices** page writes to the client
document instead:

```
GET /proxy/network/api/s/{site}/rest/user
PUT /proxy/network/api/s/{site}/rest/user/{clientId}
```

```jsonc
{
  "_id": "0123456789abcdef01234567",
  "mac": "aa:bb:cc:dd:ee:ff",
  "hostname": "homeautomation",
  "fixed_ip": "198.51.100.188",
  "use_fixedip": true,
  "local_dns_record": "ha.example.lan",        // <-- the record
  "local_dns_record_enabled": true
}
```

The gateway resolves these normally (`dig @gateway ha.example.lan` answers), but
they appear in **neither** `dns/policies` **nor** legacy `static-dns`. A tool
reading only the DNS API under-reports what the network actually answers for.

**Clearing a record has an ordering trap.** An empty `local_dns_record` while
`local_dns_record_enabled` is still `true` is rejected:

```
400 {"meta":{"rc":"error","msg":"api.err.LocalDnsRecordMissing"}}
```

Both fields must be sent together, with the flag off:

```jsonc
{ "local_dns_record": "", "local_dns_record_enabled": false }   // 200
{ "local_dns_record": "", "local_dns_record_enabled": true }    // 400
```

The trap is that a caller carrying the record's *previous* enabled state forward
sends exactly the failing combination, and only for records that were enabled.
Disabled ones clear fine, so the bug looks intermittent.

**Verified behaviour:**

| Case | Result |
|---|---|
| `PUT` with only `{"local_dns_record": "..."}` | 200, partial body accepted |
| Empty hostname with `enabled: true` | **400** `api.err.LocalDnsRecordMissing` |
| Empty hostname with `enabled: false` | 200, record cleared |
| Response envelope | `{"meta": {"rc": "ok"}, "data": [ ... ]}` |
| Record visible in `dns/policies` | **no** |
| Record visible in legacy `static-dns` | **no** |
| Gateway resolves it | **yes** |

**Consequences:**

- These are bound to a device. There is no create or delete, only set, rename,
  enable, disable, and clear on an existing client. Setting `local_dns_record`
  on a client that has none is how one is created.
- Without `use_fixedip`, the record points at a DHCP address that can move.
  Worth surfacing as a warning rather than treating as equivalent.
- Rollback needs to know which collection a change targeted, which is why
  `record_revisions.target` exists.

## Every record write restarts the resolver

Measured 2026-08-30 by querying the gateway every 0.4s while creating a record:

```
. = answered, X = failed, | = record created
........|....................XXX...............X
```

A create causes roughly **1 to 2 seconds of DNS unavailability** about four
seconds later, and a delete does the same. dnsmasq is reloaded on every change
to the record set, and it does not answer while reloading.

Consequences:

- **Bulk import is not free.** N records written sequentially means N reloads.
  A fifty-record import can leave DNS intermittently unavailable for a minute or
  more. Warn before large imports, and prefer running them at a quiet time.
- Do not verify a record with `dig` immediately after writing it. Allow ten
  seconds; an instant query can hit the reload window and look like failure when
  the record is fine.

## Single-label (bare) hostnames

`plex` with no domain is **accepted** (`201`), as is a single-label CNAME
pointing at an FQDN. UniFi does not require a dotted name.

Whether you need one is a different question. DHCP hands out a per-network
search domain (`domain_name` on `networkconf`), and clients that honour it
resolve `plex` by appending it automatically. On this console those differ per
VLAN, so a bare record is the only form that resolves identically everywhere:

| Network | Search domain |
|---|---|
| Home | `example.lan` |
| IoT | `iot.example.lan` |
| Lab | `lab.example` |
| Cameras | `sec.example.local` |
| Management | `mgmt.example.local` |
| Guest | `guest.example.local` |
| Voice, Lab-Storage, Lab-VM | *(none set)* |

## Settings write paths

Verified 2026-08-30. These do not follow one convention, and one of them is a
trap.

| Setting | Read | Write | Notes |
|---|---|---|---|
| DNS Shield | `GET rest/setting/doh` | `POST set/setting/doh` | `state` is validated (400 on bad value). `server_names` is **not** validated at all. |
| Ad blocking | `GET rest/setting/ips` | `POST set/setting/ips` | `ad_blocking_enabled`, `ad_blocking_configurations` both persist. |
| Flow logging | `GET rest/setting/traffic_flow` | `POST set/setting/traffic_flow` | `gateway_dns_enabled` persists. |
| Content filtering | `GET v2/.../content-filtering` | `PUT v2/.../content-filtering/{id}` | Full profile object required. |
| DHCP resolver | `GET rest/networkconf` | `PUT rest/networkconf/{id}` | `dhcpd_dns_1..4` plus `dhcpd_dns_enabled`. |
| **`ips.dns_filters`** | `GET rest/setting/ips` | **none that works** | See below. |

### The `ips` settings document is largely a read-only mirror

Three fields on `rest/setting/ips` look like the controls for filtering and ad
blocking. **None of them are writable.** Every write form returns
`200 {"meta":{"rc":"ok"}}` and persists nothing:

| Field | Writable | Where the real control lives |
|---|---|---|
| `dns_filters[]` | **no** | content-filtering profile `block_list` / `allow_list` |
| `ad_blocking_enabled` | **no** | profile `enabled` |
| `ad_blocking_configurations[]` | **no** | `ADVERTISEMENT` in profile `categories` |

Verified individually by writing a changed value and re-reading.

### Ad blocking is a content-filtering category

There is no standalone ad blocking API. What the UniFi console labels
**Filter Scope: Ad Block** is the `ADVERTISEMENT` category on that network's
content-filtering profile. The console shows a display label; the API returns
the identifier. They are the same setting.

Effective state is therefore `ADVERTISEMENT in categories AND profile.enabled`,
not category membership alone.

**Turning it off has a wrinkle.** `categories` is rejected when empty
(`must not be empty`). A profile whose only category is `ADVERTISEMENT` cannot
be un-blocked by removing the category; set `enabled: false` instead and leave
the category in place.

### The `ips.dns_filters` trap

`dns_filters` carries a per-network `filter` level plus `blocked_sites`,
`allowed_sites`, and `blocked_tld`. It reads like the custom blocklist API.
It is not writable.

All three write forms return `200 {"meta":{"rc":"ok"}}` and persist nothing:

- `POST set/setting/ips` with just `dns_filters`
- `PUT rest/setting/ips/{id}` with the full document
- the same with a non-`none` `filter` level set

There is no error. The response echoes a success envelope, a subsequent GET
shows the old values, and a naive integration reports that it saved.

It is a **read-only legacy projection**. The values actually live on the v2
content-filtering profiles, where `block_list` and `allow_list` are the working
equivalents of `blocked_sites` and `allowed_sites`.

### Content filtering, the working path

```
GET v2/api/site/{site}/content-filtering             list profiles
GET v2/api/site/{site}/content-filtering/categories  116 category identifiers
PUT v2/api/site/{site}/content-filtering/{id}        update one profile
```

`PUT` on the collection and `POST` on an item both return 405. The `PUT` body
must be the complete profile; a partial patch is not merged.

Profiles carry `categories`, `block_list`, `allow_list`, `client_macs`,
`network_ids`, `safe_search`, and a `schedule`. The last three are supported by
the API and unused by default.

## Behaviour that shapes the app

1. **No zones.** Storage is a flat list. Zone grouping is a client side
   construct: match each record's FQDN against a user maintained domain list,
   longest suffix wins, remainder becomes the label. DNSControl does exactly
   this. Anything unmatched lands in an "ungrouped" bucket.
2. **No change history.** UniFi keeps none. If we want an audit log, the
   container owns it.
3. **No query log.** Nothing to read. Do not design a page for it.
4. **No concurrency.** Requests must be sequential. Serialize writes behind a
   single worker; do not fan out bulk imports.
5. **Legacy edits churn IDs.** Delete + create. Plan the optimistic UI around
   a refetch, not an in place patch, when running on legacy.
6. **Unbounded path cardinality.** The ExternalDNS project hit a real problem
   labelling metrics by request path, because the record UUID is in the path.
   If we export metrics, label by operation, never by path.

## Answered

| Question | Answer |
|---|---|
| Network version | 10.5.67, UniFi OS 5.1.26 |
| Integration v1 present? | Yes, fully functional |
| Filter DSL on `dns/policies`? | `.eq()` yes, `.like()` silently empty |
| Native `PUT`? | Yes, ID stable, returns updated object |
| Current record count | 0 on both APIs |
| Legacy needed? | No, drop it from v1 scope |
| Do the two APIs share a store? | Yes, different ID schemes |

## Still open

- Whether the console enforces any ceiling on total record count. Untested;
  probe with a bulk insert before promising unlimited import.
- Whether `enabled: false` records are actually withheld from dnsmasq, or just
  flagged in the UI. Needs a `dig` against the gateway with a disabled record.
- Whether per-client hostnames set on the Client Devices page surface through
  `dns/policies` or live in a separate collection. Zero records currently, so
  untestable until one is set.

# Setup

## Creating a UniFi API key

From the UniFi console's main page, open **Settings and Logs**, then
**Integrations**, then **Create New API Key**.

A few things that are easy to get wrong:

- This lives at the **UniFi OS** level, alongside the console's own settings.
  It is not inside the UniFi Network application, so looking there is a dead
  end.
- **Ubiquiti has moved this more than once.** Older versions put it under
  *Settings > Admins & Users* against your admin account. If neither path
  matches what you see, type "API" into the console's search box, which
  outlasts the reshuffling.
- **The key is shown once.** Copy it immediately; there is no way to read it
  back later, only to delete it and make another.
- Give it a name that says where it is used, for example `unifi-dns`. When you
  later wonder whether a key is still needed, the name is all you have.

### What the key grants

Everything. There is no scoping, no read-only option, and no per-feature
permission. Any valid API key can read the settings endpoint, which returns the
gateway's SSH password in plaintext.

Treat it as a root credential for the gateway. See [SECURITY.md](SECURITY.md)
for the detail and for what this project does to contain it.

### Verifying the key before you start the app

Worth doing, because a bad key produces the same symptom as a wrong host, and
this tells the two apart:

```bash
curl -sk -H "X-API-Key: YOUR_KEY" \
  https://192.168.1.1/proxy/network/integration/v1/info
```

Expected:

```json
{"applicationVersion": "10.6.101"}
```

| Response | Meaning |
|---|---|
| `{"applicationVersion": "..."}` | Key and host are both correct |
| `{"error":{"code":401,...}}` | Key is wrong, revoked, or has a stray space |
| Connection refused or timeout | Wrong `UNIFI_HOST`, or the gateway is unreachable |
| HTML instead of JSON | Reached something that is not a UniFi gateway |

`-k` is needed because gateways use a self-signed certificate. The app does the
same by default via `UNIFI_VERIFY_TLS=false`.

### Requirements

- UniFi Network **10.1 or later**, for the Integration v1 API. Verified against
  10.6.101.
- A gateway that supports local DNS records: UDM, UDM Pro, UDM SE, UXG, or a
  Cloud Gateway. The USG 3 does not.

## Rotating or revoking

Delete the key in the same Integrations screen. Revocation takes effect
immediately: the app's next call returns 401 and the UI shows
"Gateway Unreachable" with the error, rather than failing silently.

To rotate, create the new key first, update `.env`, then
`docker compose up -d` and delete the old one once the header shows
"Gateway Online" again.

## Common problems

**"Gateway Unreachable" straight after setup.** Run the curl check above. If it
returns the version, the key and host are fine and the problem is between the
container and the gateway, most often a `UNIFI_HOST` pointing at a name the
container cannot resolve. Use an IP address.

**Every record shows as Ungrouped.** Expected before you declare an apex domain.
Go to **Apex domains**; the page suggests candidates inferred from records
already on the gateway.

**Drift reports every record on a new install.** Also expected. The app has not
taken a baseline yet, which is why the page says "Not tracking yet" and offers
**Start tracking** rather than presenting it as drift.

**Records exist on the gateway but the app does not list them.** Check whether
they are set on a *client device* rather than in the DNS store. Both are shown,
labelled in the **Stored in** column. If something resolves but appears in
neither, it is worth an issue.

**DNS briefly stops resolving during a bulk import.** Expected. The gateway
reloads dnsmasq on every record write, costing one to two seconds each. Bulk
operations warn before running and estimate the duration.


## Importing from another resolver

**Migrate > Import from resolver.** Three sources, all previewed before anything
is written.

**Pi-hole.** v6 uses the web password, v5 uses an API token from
*Settings > API*. Both are tried, so supply whichever you have.

**Technitium.** Needs the server address including its port (`:5380` by
default) and either an API token from *Administration > API Tokens*, or a
username and password to sign in with. Every user-created zone is read.
Technitium's built-in reverse and localhost zones are skipped, along with
disabled records.

**Paste files.** Accepts Pi-hole's `custom.list` and CNAME config, and any
RFC 1035 zone file. The zone-file box takes a Technitium export
(*Zones > Export*), a BIND or PowerDNS zone, or anything else in the standard
format. Set an origin only if the file has no `$ORIGIN` line.

This path is the fallback worth remembering: it works when the source server is
already off, when its credentials are gone, and for resolvers with no direct
support.

### What does not come across

UniFi stores A, AAAA, CNAME, MX, TXT and SRV. Anything else in the source
(SOA, NS, PTR, CAA, DNSKEY, RRSIG) is listed as skipped with the reason, rather
than dropped silently. Records disabled on the source server are skipped too.

The preview sorts everything into **New**, **Already present**, **Conflicting**
(same name and type, different value, so importing adds a second answer rather
than replacing) and **Shadowed** (a client device already publishes that name).
Only New is selected by default.

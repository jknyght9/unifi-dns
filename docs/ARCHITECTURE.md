# Architecture

```
React + TypeScript  ->  FastAPI  ->  UniFi Integration v1 API
      (nginx)              |
                      PostgreSQL
```

Three containers. One data layer. No message queue, no cache, no second
database.

## Why these pieces

**FastAPI.** The UniFi DNS record is polymorphic on a `type` discriminator, and
the API is strict in ways that are easy to get wrong: `ttlSeconds` is rejected
outright on three of the six record types, `id` in a `PUT` body is a 400, and
SRV splits its label into three fields. Pydantic's discriminated unions express
those rules in the model, so the class of mistake the gateway punishes is caught
before a request is built.

**PostgreSQL, for everything.** Configuration history and flow telemetry live in
the same database. Splitting them across a versioned file store and a
time-series store was considered and rejected: two backup stories and two
consistency models for one small application is a poor trade.

**React.** The heavier screens are dense tables and aggregates. That ecosystem is
deeper for the table and charting work, and the record model maps cleanly onto
discriminated unions in TypeScript, mirroring the backend.

**Integration v1 only.** The legacy `/v2/api/site/{site}/static-dns` surface is a
view over the same store, but it has no working update: an edit is a delete plus
a create, so record IDs churn. Since Network 10.1 ships v1, supporting both would
cost stable IDs and buy nothing.

## Changesets

Every mutation goes through a changeset. This is not an audit feature bolted on
afterwards; it is the write path, so no code path can forget to record what it
did.

| Table | Holds |
|---|---|
| `dns_records` | Mirror of what the gateway is believed to hold |
| `change_sets` | Append-only: who, when, why, source, status |
| `record_revisions` | Before and after JSON for each record touched |

`before` is null on a create, `after` is null on a delete, and both are complete
Integration v1 objects. A rollback therefore needs no other source of truth: it
reads the revisions, inverts them, and applies the result **forward as a new
changeset**. History is never rewritten, so rolling back change N produces
change N+1.

`record_revisions.target` distinguishes the two places a record can live, because
applying and reverting them hit different APIs.

### Drift

The mirror is not authoritative, and the app does not pretend otherwise. A
reconcile compares the gateway against `dns_records` and reports three
categories: on the gateway but not in the mirror, in the mirror but not on the
gateway, and present in both but different.

Without this the mirror becomes fiction the first time someone edits a record in
the UniFi console. With it, that edit is visible and can be adopted or reverted.

## Zone synthesis

UniFi has no zone concept. Records are a flat list of FQDNs, which is why the
native console cannot group or filter by domain.

Grouping is imposed client-side: each record's name is matched against a list of
operator-declared apex domains, longest suffix wins, and matching is on **label
boundaries** so `notexample.com` does not match apex `example.com`. Records are
sorted into one of three buckets:

- the apex they sit under
- **Bare hostnames**, for single-label names like `plex`
- **Ungrouped**, for names that matched no declared apex

Ungrouped is always returned even when empty. An absent group is
indistinguishable from a missing feature, and "nothing is unmatched" is worth
seeing rather than inferring.

## Two record sources

A local DNS record on a UniFi gateway can live in either of two unrelated
collections:

| Source | API | Created from |
|---|---|---|
| DNS store | `integration/v1/.../dns/policies` | Settings > Routing > DNS |
| Client-bound | `api/s/{site}/rest/user` → `local_dns_record` | A client device's page |

The second is invisible to the DNS API, and the gateway resolves it anyway. A
tool reading only the DNS API under-reports what the network answers for, so
both are read and merged into one view, labelled by source.

Client-bound records are bound to a device. They can be renamed, disabled, or
cleared, but not deleted independently, and the UI reflects that.

## Writes are serialised

The gateway rejects concurrent mutations, so every write goes through a single
lock in the client.

It also **reloads dnsmasq on each change**, costing roughly one to two seconds of
DNS unavailability. That makes bulk operations a user-visible event rather than a
background detail, so import, rename, and bulk delete all warn with an estimated
duration before running.

## Telemetry collection

A background task polls `traffic-flows` and stores rows keyed on the gateway's
own flow `id`, which makes re-polling idempotent. The gateway retains about five
days; the table keeps whatever has been collected since first run.

See [TELEMETRY.md](TELEMETRY.md) for what that data can and cannot support.

## Authentication

Two credentials with two different jobs, deliberately not shared:

- **OIDC, or a trusted forward-auth header, identifies the human.**
- **The UniFi API key is a service credential** the app uses on the gateway.

Keeping them separate is a security requirement rather than a nicety: the UniFi
API key is root-equivalent on the gateway (see [SECURITY.md](SECURITY.md)), and
must never double as a login.

Enforcement is conditional. With an auth backend configured, unauthenticated
`/api/*` requests get a 401. With none configured the app runs open and warns at
startup, so it cannot silently ship either locked out or wide open.

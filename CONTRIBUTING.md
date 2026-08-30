# Contributing

## Running without a UniFi gateway

`demo/mock_unifi.py` serves canned responses for every endpoint the app uses, so
you can develop against realistic data with no hardware:

```bash
python demo/mock_unifi.py &            # listens on :8443
UNIFI_HOST=http://localhost:8443 UNIFI_API_KEY=demo \
  uvicorn app.main:app --app-dir backend --reload
```

## Running against real hardware

```bash
cp .env.example .env     # set UNIFI_API_KEY and SESSION_SECRET
docker compose up -d
```

`./verify.sh` drives a full lifecycle against the gateway: create, update,
rollback, drift check, delete. It only ever touches `*.claude.invalid`, so it is
safe to run on a live console.

## Ground rules

**Verify against the API, do not assume.** Several UniFi endpoints return
`200 {"rc":"ok"}` and persist nothing. `ips.dns_filters`, `ips.ad_blocking_enabled`
and `ips.ad_blocking_configurations` are all read-only mirrors that report
success on write. If you add a write path, round-trip it: write a changed value,
read it back, and confirm.

**Record what the hardware actually does.** `docs/API.md` documents verified
behaviour with the version it was verified against. Add to it when you learn
something; correct it when it turns out wrong.

**Everything that changes state is recorded.** Two kinds of entry exist and both
matter:

- A change that touches a DNS record carries revisions with before/after JSON.
  Rollback depends on those, so a write path that skips them cannot be undone.
- A change that only affects what the app tracks or how it groups records
  (adopting a baseline, declaring an apex domain) is recorded with zero
  revisions and no rollback.

The second case was originally left out, on the reasoning that it did not touch
the gateway. That was wrong in practice: the first two things a new user does
are adopt and declare an apex, so History was empty at exactly the moment
someone checks whether it works. If you add an endpoint that changes state, ask
which kind it is, not whether it counts.

**Writes are serialised.** The gateway rejects concurrent mutations, and each
write reloads dnsmasq with roughly a second of DNS unavailability. Do not fan
out writes.

## Style

Match the surrounding code. Comments explain *why*, particularly where the code
works around a UniFi quirk, since those look like mistakes otherwise.

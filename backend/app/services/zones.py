"""Zone synthesis.

UniFi has no zone concept. Records are a flat list of FQDNs, which is why the
native UI cannot group or filter by domain. Grouping is therefore ours to
impose: match each record's FQDN against a declared apex list, longest suffix
wins, and anything unmatched lands in a visible `ungrouped` bucket rather than
being silently dropped.

Matching is on label boundaries. `notexample.com` must not match apex
`example.com`, and a bare suffix comparison would say it does.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.schemas.unifi import DnsRecord

UNGROUPED = "__ungrouped__"
#: Single-label names like `plex`. These are intentional shortcuts, not records
#: that failed to match an apex, so they get their own group rather than being
#: mixed in with genuine orphans.
BARE = "__bare__"


def match_apex(fqdn: str, apexes: list[str]) -> str | None:
    """Longest apex that `fqdn` sits under, or None."""
    name = fqdn.rstrip(".").lower()
    best: str | None = None
    for apex in apexes:
        a = apex.rstrip(".").lower()
        if not a:
            continue
        if name == a or name.endswith("." + a):
            if best is None or len(a) > len(best):
                best = a
    return best


def relative_label(fqdn: str, apex: str) -> str:
    """`www.example.com` under `example.com` -> `www`; the apex itself -> `@`."""
    name = fqdn.rstrip(".").lower()
    a = apex.rstrip(".").lower()
    if name == a:
        return "@"
    return name[: -(len(a) + 1)]


@dataclass
class ZoneEntry:
    record: DnsRecord
    label: str

    @property
    def fqdn(self) -> str:
        return self.record.fqdn


@dataclass
class Zone:
    apex: str
    entries: list[ZoneEntry] = field(default_factory=list)

    @property
    def count(self) -> int:
        return len(self.entries)

    @property
    def is_ungrouped(self) -> bool:
        return self.apex == UNGROUPED

    @property
    def is_bare(self) -> bool:
        return self.apex == BARE


def build_zones(records: list[DnsRecord], apexes: list[str]) -> list[Zone]:
    """Group records into zones. Every record appears exactly once."""
    zones: dict[str, Zone] = {a.rstrip(".").lower(): Zone(a.rstrip(".").lower()) for a in apexes}
    ungrouped = Zone(UNGROUPED)
    bare = Zone(BARE)

    for rec in records:
        apex = match_apex(rec.fqdn, apexes)
        if apex is not None:
            zones[apex].entries.append(ZoneEntry(rec, relative_label(rec.fqdn, apex)))
        elif "." not in rec.fqdn.rstrip("."):
            bare.entries.append(ZoneEntry(rec, rec.fqdn))
        else:
            ungrouped.entries.append(ZoneEntry(rec, rec.fqdn))

    for z in zones.values():
        # Apex first, then alphabetical by label, then by type for stable ordering
        # of round-robin duplicates.
        z.entries.sort(key=lambda e: (e.label != "@", e.label, e.record.type, e.record.value))

    for z in (bare, ungrouped):
        z.entries.sort(key=lambda e: (e.label, e.record.type, e.record.value))

    out = sorted(zones.values(), key=lambda z: z.apex)
    if bare.entries:
        out.append(bare)
    # Always returned, even when empty. An absent group is indistinguishable
    # from a missing feature, and "nothing is unmatched" is itself useful to
    # see rather than infer.
    out.append(ungrouped)
    return out


def suggest_apexes(records: list[DnsRecord], min_count: int = 1) -> list[str]:
    """Infer candidate apexes from existing records, for first-run bootstrap.

    Uses the last two labels, which is right for `example.com` and `home.arpa`
    and wrong for multi-label public suffixes like `co.uk`. Treated as a
    suggestion the operator confirms, never applied automatically.
    """
    counts: dict[str, int] = {}
    for rec in records:
        labels = rec.fqdn.rstrip(".").lower().split(".")
        if len(labels) < 2:
            continue
        apex = ".".join(labels[-2:])
        counts[apex] = counts.get(apex, 0) + 1
    return sorted([a for a, n in counts.items() if n >= min_count])

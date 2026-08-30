"""DNS and DNS-privacy settings spread across three different UniFi APIs.

These do not live together. Reading the full picture means touching:

  - `api/s/{site}/rest/setting/doh`          DNS Shield (DoH upstream)
  - `api/s/{site}/rest/setting/ips`          ad blocking + per-network DNS filters
  - `api/s/{site}/rest/setting/traffic_flow` flow logging, including Gateway DNS
  - `v2/api/site/{site}/content-filtering`   category/allow/block profiles
  - `api/s/{site}/rest/networkconf`          which resolver DHCP hands out

The last one decides whether any of the others take effect: a client pointed at
a public resolver bypasses every control on this page.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

#: `state` is validated server-side; a bogus value returns 400. Both of these
#: were observed on live hardware. Note the "on" value is spelled `auto`.
DOH_STATE_OFF = "off"
DOH_STATE_AUTO = "auto"
DOH_STATES = (DOH_STATE_OFF, DOH_STATE_AUTO)

#: Provider identifiers confirmed accepted and used by the console. Unlike
#: `state`, `server_names` is NOT validated: any string is stored verbatim and
#: only shows up as broken resolution once DNS Shield is active. Anything not
#: in this set should be treated as unverified and flagged in the UI.
KNOWN_DOH_PROVIDERS = ("cloudflare", "google")


class DohSettings(BaseModel):
    """DNS Shield: encrypted DNS to a chosen upstream.

    Caution: `server_names` is stored verbatim with no server-side validation.
    An unrecognised identifier is accepted and persisted, and only shows up as
    broken resolution once the feature is enabled.
    """

    model_config = ConfigDict(extra="allow")

    id: str | None = Field(default=None, alias="_id")
    state: str = DOH_STATE_OFF
    server_names: list[str] = Field(default_factory=list)
    custom_servers: list[dict] = Field(default_factory=list)


class DnsFilter(BaseModel):
    """Per-network DNS filtering, including the custom block/allow lists.

    `blocked_sites` is the closest thing UniFi has to a subscribable blocklist:
    it is a writable array, so a curated list can be pushed here per network.
    """

    model_config = ConfigDict(extra="allow")

    network_id: str
    filter: str = "none"
    blocked_tld: list[str] = Field(default_factory=list)
    blocked_sites: list[str] = Field(default_factory=list)
    allowed_sites: list[str] = Field(default_factory=list)
    name: str = ""
    description: str = ""
    version: str = "v4"


class AdBlockConfig(BaseModel):
    model_config = ConfigDict(extra="allow")
    network_id: str


class IpsSettings(BaseModel):
    """The `ips` document carries ad blocking and DNS filtering, not just IPS."""

    model_config = ConfigDict(extra="allow")

    id: str | None = Field(default=None, alias="_id")
    ad_blocking_enabled: bool = False
    dns_filtering: bool = False
    ad_blocking_configurations: list[AdBlockConfig] = Field(default_factory=list)
    dns_filters: list[DnsFilter] = Field(default_factory=list)
    advanced_filtering_preference: str = "disabled"
    content_filtering_blocking_page_enabled: bool = False


class TrafficFlowSettings(BaseModel):
    """Flow logging. `gateway_dns_enabled` is what produces DNS query telemetry."""

    model_config = ConfigDict(extra="allow")

    id: str | None = Field(default=None, alias="_id")
    gateway_dns_enabled: bool = False
    enabled_allowed_traffic: bool = False
    unifi_services_enabled: bool = False
    unifi_device_management_enabled: bool = False


class ContentFilterSchedule(BaseModel):
    model_config = ConfigDict(extra="allow")
    mode: str = "ALWAYS"
    repeat_on_days: list[str] = Field(default_factory=list)
    time_all_day: bool = False


class ContentFilterProfile(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: str | None = Field(default=None, alias="_id")
    name: str = ""
    enabled: bool = False
    categories: list[str] = Field(default_factory=list)
    allow_list: list[str] = Field(default_factory=list)
    block_list: list[str] = Field(default_factory=list)
    client_macs: list[str] = Field(default_factory=list)
    network_ids: list[str] = Field(default_factory=list)
    safe_search: list[str] = Field(default_factory=list)
    schedule: ContentFilterSchedule = Field(default_factory=ContentFilterSchedule)


class NetworkDns(BaseModel):
    """Which resolver a network hands out over DHCP.

    Pairing a filtering resolver with a public one is a common and damaging
    mistake: DHCP resolvers are a set, not an ordered failover chain, so clients
    query whichever they like and randomly bypass filtering.
    """

    model_config = ConfigDict(extra="allow")

    id: str = Field(alias="_id")
    name: str = ""
    purpose: str = ""
    vlan: int | str | None = None
    dhcpd_dns_enabled: bool = False
    servers: list[str] = Field(default_factory=list)
    #: DHCP search domain. Clients append this to unqualified names, which is
    #: what makes `plex` resolve without a bare single-label record.
    domain_name: str = ""

    @property
    def inherits_gateway(self) -> bool:
        return not self.dhcpd_dns_enabled or not self.servers


#: Resolvers that provide no local records and no filtering.
PUBLIC_RESOLVERS = {
    "1.1.1.1", "1.0.0.1", "8.8.8.8", "8.8.4.4", "9.9.9.9", "149.112.112.112",
    "208.67.222.222", "208.67.220.220", "94.140.14.14", "94.140.15.15",
}


class UpdateDoh(BaseModel):
    state: str | None = None
    server_names: list[str] | None = None
    custom_servers: list[dict] | None = None


class UpdateAdBlocking(BaseModel):
    enabled: bool | None = None
    network_ids: list[str] | None = None


class UpdateContentFilter(BaseModel):
    """Patch for one content-filtering profile.

    This is where custom block and allow lists actually live. The `dns_filters`
    array on the `ips` setting is a read-only legacy projection: writes to it
    return success and change nothing.
    """

    enabled: bool | None = None
    categories: list[str] | None = None
    block_list: list[str] | None = None
    allow_list: list[str] | None = None
    safe_search: list[str] | None = None


class UpdateTrafficFlow(BaseModel):
    gateway_dns_enabled: bool | None = None
    enabled_allowed_traffic: bool | None = None


class UpdateNetworkDns(BaseModel):
    dhcpd_dns_enabled: bool
    servers: list[str] = Field(default_factory=list)
    #: None leaves the search domain untouched; "" clears it.
    domain_name: str | None = None


#: `.local` is claimed by mDNS (RFC 6762). Clients divert those lookups to
#: multicast, so unicast records under it resolve inconsistently.
MDNS_TLD = ".local"

#: ICANN board resolution 2024.07.29.06 permanently reserves `.internal` from
#: root delegation for private use. `home.arpa` (RFC 8375) is the other safe one.
SAFE_PRIVATE_SUFFIXES = (".internal", ".home.arpa")

#: Undelegated today, but never reserved, so a future gTLD round could collide.
UNRESERVED_TLDS = (".lan", ".home", ".corp", ".mail", ".lab", ".box")


def search_domain_advice(domain: str) -> dict | None:
    """Flag a search domain that will misbehave. None means it looks fine."""
    d = (domain or "").strip().rstrip(".").lower()
    if not d:
        return None
    if d.endswith(MDNS_TLD):
        return {
            "severity": "high",
            "detail": (
                "`.local` is reserved for mDNS. Apple, Avahi and Android divert these "
                "lookups to multicast instead of the gateway, so records under it "
                "resolve inconsistently. Move to a `.internal` subdomain."
            ),
        }
    if d.endswith(SAFE_PRIVATE_SUFFIXES):
        return None
    if d.endswith(UNRESERVED_TLDS):
        return {
            "severity": "info",
            "detail": (
                "Undelegated today but never reserved, so a future gTLD round could "
                "collide with it. `.internal` is reserved permanently for this."
            ),
        }
    return {
        "severity": "info",
        "detail": (
            "Not a reserved private-use suffix. If this is a domain you own that is "
            "also on the public internet, internal names will shadow the real ones."
        ),
    }

"""DNS and privacy settings.

These live across three unrelated UniFi APIs (see `app.schemas.settings`), and
the console spreads them over as many pages. Presenting them together is the
point: whether ad blocking works at all depends on the DHCP resolver a network
hands out, and those two controls are nowhere near each other in the native UI.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends

from app.api.deps import get_client
from app.schemas.settings import (
    DOH_STATES,
    search_domain_advice,
    KNOWN_DOH_PROVIDERS,
    PUBLIC_RESOLVERS,
    UpdateAdBlocking,
    UpdateContentFilter,
    UpdateDoh,
    UpdateNetworkDns,
    UpdateTrafficFlow,
)
from app.unifi.client import UnifiClient

router = APIRouter(prefix="/settings", tags=["settings"])

GATEWAY_HINT = "Clients on this network resolve through the gateway."


def _network_warnings(networks: list, gateway_ips: set[str]) -> list[dict]:
    """Findings that make the rest of this page moot if left unaddressed."""
    out: list[dict] = []
    for n in networks:
        public = [s for s in n.servers if s in PUBLIC_RESOLVERS]
        private = [s for s in n.servers if s not in PUBLIC_RESOLVERS]
        if not n.dhcpd_dns_enabled or not n.servers:
            continue
        if public and private:
            out.append({
                "network": n.name, "severity": "high", "servers": n.servers,
                "detail": (
                    f"{n.name} hands out both a private resolver and a public one "
                    f"({', '.join(public)}). DHCP resolvers are a set, not an ordered "
                    "failover chain, so clients query whichever they like and randomly "
                    "bypass filtering and local records."
                ),
            })
        elif public and not private:
            out.append({
                "network": n.name, "severity": "high", "servers": n.servers,
                "detail": (
                    f"{n.name} resolves only via public DNS ({', '.join(public)}). "
                    "It gets no local records and no filtering at all."
                ),
            })
        elif private and not any(s in gateway_ips for s in private):
            out.append({
                "network": n.name, "severity": "info", "servers": n.servers,
                "detail": (
                    f"{n.name} points at {', '.join(private)} rather than the gateway. "
                    "Intentional if that host is your resolver."
                ),
            })
    return out


@router.get("/dns")
async def dns_settings(client: Annotated[UnifiClient, Depends(get_client)]):
    doh = await client.get_doh()
    ips = await client.get_ips()
    flow = await client.get_traffic_flow()
    profiles = await client.get_content_filters()
    networks = await client.get_networks()
    categories = await client.content_filter_categories()

    by_id = {n.id: n.name for n in networks}
    adblock_ids = {c.network_id for c in ips.ad_blocking_configurations}
    gateway_ips = {client._host.replace("https://", "").replace("http://", "")}

    return {
        "doh": {
            "state": doh.state,
            "server_names": doh.server_names,
            "custom_servers": doh.custom_servers,
            "states": list(DOH_STATES),
            "known_providers": list(KNOWN_DOH_PROVIDERS),
            # server_names is stored verbatim with no server-side validation, so
            # a typo is accepted and only surfaces as broken resolution later.
            "server_names_validated": False,
            "unverified_providers": [
                n for n in doh.server_names if n not in KNOWN_DOH_PROVIDERS
            ],
        },
        # ips.ad_blocking_* is a read-only projection, same as ips.dns_filters.
        # The authoritative per-network state is the ADVERTISEMENT category on
        # each content-filtering profile, so derive it from there.
        "ad_blocking": {
            "enabled": ips.ad_blocking_enabled,
            "dns_filtering": ips.dns_filtering,
            "readonly_mirror": True,
            "networks": [
                {
                    "id": nid,
                    "name": by_id.get(nid, nid),
                    "enabled": any(
                        nid in p.network_ids
                        and "ADVERTISEMENT" in p.categories
                        and p.enabled
                        for p in profiles
                    ),
                    "profile": next(
                        (p.name for p in profiles if nid in p.network_ids), None
                    ),
                }
                for nid in by_id
            ],
        },
        # Read-only. Writes to ips.dns_filters return rc:ok and persist nothing;
        # the working equivalent is the content_filters profiles below.
        "dns_filters_readonly": True,
        "dns_filters": [
            {
                "network_id": f.network_id,
                "network_name": by_id.get(f.network_id, f.network_id),
                "filter": f.filter,
                "blocked_sites": f.blocked_sites,
                "allowed_sites": f.allowed_sites,
                "blocked_tld": f.blocked_tld,
            }
            for f in ips.dns_filters
        ],
        "content_filters": [
            {
                "id": p.id, "name": p.name, "enabled": p.enabled,
                "categories": p.categories, "allow_list": p.allow_list,
                "block_list": p.block_list, "client_macs": p.client_macs,
                "networks": [by_id.get(i, i) for i in p.network_ids],
                "safe_search": p.safe_search, "schedule": p.schedule.mode,
            }
            for p in profiles
        ],
        "categories": categories,
        "traffic_logging": {
            "gateway_dns_enabled": flow.gateway_dns_enabled,
            "enabled_allowed_traffic": flow.enabled_allowed_traffic,
        },
        "networks": [
            {
                "id": n.id, "name": n.name, "vlan": n.vlan,
                "dhcpd_dns_enabled": n.dhcpd_dns_enabled,
                "servers": n.servers,
                "inherits_gateway": n.inherits_gateway,
                "domain_name": n.domain_name,
                "domain_advice": search_domain_advice(n.domain_name),
            }
            for n in networks
        ],
        "warnings": _network_warnings(networks, gateway_ips),
    }


@router.put("/doh")
async def update_doh(
    body: UpdateDoh, client: Annotated[UnifiClient, Depends(get_client)]
):
    current = await client.get_doh()
    payload = {
        "state": body.state if body.state is not None else current.state,
        "server_names": (
            body.server_names if body.server_names is not None else current.server_names
        ),
        "custom_servers": (
            body.custom_servers
            if body.custom_servers is not None
            else current.custom_servers
        ),
    }
    return (await client.set_doh(payload)).model_dump(by_alias=True)


ADVERTISEMENT = "ADVERTISEMENT"


@router.put("/ad-blocking")
async def update_ad_blocking(
    body: UpdateAdBlocking, client: Annotated[UnifiClient, Depends(get_client)]
):
    """Turn ad blocking on or off per network.

    There is no standalone ad blocking API. What the UniFi console calls
    "Filter Scope: Ad Block" is the `ADVERTISEMENT` category on that network's
    content-filtering profile, and that profile is the only writable path.

    `ips.ad_blocking_enabled` and `ips.ad_blocking_configurations` look like the
    control and are not: every write to either returns rc:ok and persists
    nothing, exactly like `ips.dns_filters`.
    """
    if body.network_ids is None:
        return {"detail": "network_ids is required; there is no global toggle"}

    wanted = set(body.network_ids)
    changed = []
    for profile in await client.get_content_filters():
        if not profile.id:
            continue
        covers = wanted.intersection(profile.network_ids)
        # Effective ad blocking needs both the category and an enabled profile.
        # Checking membership alone misses the case where the profile was
        # switched off (which is how "off" is expressed when ADVERTISEMENT is
        # the only category, since an empty category list is rejected).
        has_ads = ADVERTISEMENT in profile.categories and profile.enabled
        should = bool(covers)
        if should == has_ads:
            continue
        if should:
            cats = profile.categories
            if ADVERTISEMENT not in cats:
                cats = [*cats, ADVERTISEMENT]
            patch = {"categories": cats, "enabled": True}
        else:
            remaining = [c for c in profile.categories if c != ADVERTISEMENT]
            # The API rejects an empty `categories` list outright, so a profile
            # whose only category is ADVERTISEMENT is switched off rather than
            # emptied. The category stays, ready for re-enabling.
            patch = (
                {"categories": remaining, "enabled": profile.enabled}
                if remaining
                else {"categories": profile.categories, "enabled": False}
            )
        await client.update_content_filter(profile.id, patch)
        changed.append(
            {"profile": profile.name, "ad_blocking": should, "applied": patch}
        )
    return {"changed": changed}


@router.put("/content-filter/{profile_id}")
async def update_content_filter(
    profile_id: str,
    body: UpdateContentFilter,
    client: Annotated[UnifiClient, Depends(get_client)],
):
    """Edit a content-filtering profile: categories, custom lists, safe search.

    Custom block and allow lists live here, not on `ips.dns_filters`.
    """
    patch = body.model_dump(exclude_none=True)
    profile = await client.update_content_filter(profile_id, patch)
    return profile.model_dump(by_alias=True)


@router.put("/traffic-flow")
async def update_traffic_flow(
    body: UpdateTrafficFlow, client: Annotated[UnifiClient, Depends(get_client)]
):
    current = await client.get_traffic_flow()
    payload = {
        "gateway_dns_enabled": (
            body.gateway_dns_enabled
            if body.gateway_dns_enabled is not None
            else current.gateway_dns_enabled
        ),
        "enabled_allowed_traffic": (
            body.enabled_allowed_traffic
            if body.enabled_allowed_traffic is not None
            else current.enabled_allowed_traffic
        ),
    }
    return (await client.set_traffic_flow(payload)).model_dump(by_alias=True)


@router.put("/networks/{network_id}/dns")
async def update_network_dns(
    network_id: str,
    body: UpdateNetworkDns,
    client: Annotated[UnifiClient, Depends(get_client)],
):
    """Set which resolvers a network hands out over DHCP.

    Disabling means the network inherits the gateway, which is what you want
    for everything the gateway should filter.
    """
    await client.set_network_dns(
        network_id, body.dhcpd_dns_enabled, body.servers, body.domain_name
    )
    return {"ok": True}

"""Client-bound local DNS records.

UniFi has two unrelated places a local DNS record can live:

  1. `integration/v1/.../dns/policies` -- the DNS records editor.
  2. `local_dns_record` on a *client* object in `api/s/{site}/rest/user`,
     which is what the "Local DNS Record" toggle on the Client Devices page
     writes.

The second does not appear in the DNS API at all, but the gateway resolves it
just the same. A tool that reads only the DNS API will therefore under-report
what the network actually answers for, which is exactly the trap this module
exists to close.

These records are bound to a device rather than free-standing, so they cannot be
created or deleted independently. They are set, edited, disabled, or cleared on
the client they belong to.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

#: Presented as an A record because that is what it behaves as.
CLIENT_RECORD_TYPE = "A_RECORD"


class ClientDnsRecord(BaseModel):
    """Duck-types the DnsRecord surface that zone synthesis relies on."""

    model_config = ConfigDict(populate_by_name=True)

    client_id: str = Field(alias="_id")
    mac: str | None = None
    name: str | None = None
    hostname: str | None = None
    local_dns_record: str
    enabled: bool = Field(default=False, alias="local_dns_record_enabled")
    fixed_ip: str | None = None
    last_ip: str | None = None
    use_fixedip: bool = False
    network_name: str | None = Field(default=None, alias="last_connection_network_name")

    @property
    def id(self) -> str:
        return self.client_id

    @property
    def type(self) -> str:
        return CLIENT_RECORD_TYPE

    @property
    def domain(self) -> str:
        return self.local_dns_record

    @property
    def fqdn(self) -> str:
        return self.local_dns_record

    @property
    def value(self) -> str:
        return self.fixed_ip or self.last_ip or ""

    @property
    def ttl_seconds(self) -> None:
        return None

    @property
    def display_name(self) -> str:
        return self.name or self.hostname or self.mac or self.client_id

    @property
    def unstable(self) -> bool:
        """True when the record points at a DHCP address rather than a reservation.

        Without a fixed IP the answer changes whenever the lease moves, which is
        a future outage rather than a working record.
        """
        return not self.use_fixedip


class ClientRecordUpdate(BaseModel):
    """Set, rename, toggle, or clear a client's local DNS record.

    `hostname=None` clears it. Setting a hostname on a client that has none is
    how one is created; there is no separate create path.
    """

    hostname: str | None = None
    enabled: bool | None = None


class EligibleClient(BaseModel):
    """A client that could carry a record but does not have one yet."""

    client_id: str
    name: str
    mac: str | None = None
    fixed_ip: str | None = None
    network_name: str | None = None

"""Typed model of the UniFi Network Integration v1 DNS record.

The API is polymorphic on `type`, and it is strict in ways that are easy to get
wrong by hand. Verified against UniFi Network 10.5.67:

  - `ttlSeconds` is accepted on A/AAAA/CNAME and rejected with
    `Unknown request body property '$.ttlSeconds'` on MX/TXT/SRV.
  - `id` in a PUT body is rejected; it belongs in the path only.
  - `metadata` is response-only.
  - SRV splits `_service._proto.name` into three separate fields.

Encoding those rules here means the model, not the caller, is responsible for
producing a payload the gateway will accept.
"""

from __future__ import annotations

from ipaddress import IPv4Address, IPv6Address
from typing import Annotated, Literal, Union

from pydantic import BaseModel, ConfigDict, Field

# Wire values for the `type` discriminator.
A = "A_RECORD"
AAAA = "AAAA_RECORD"
CNAME = "CNAME_RECORD"
MX = "MX_RECORD"
TXT = "TXT_RECORD"
SRV = "SRV_RECORD"

DEFAULT_TTL = 300

#: Types the gateway accepts a `ttlSeconds` property on.
TTL_CAPABLE = frozenset({A, AAAA, CNAME})


class RecordMetadata(BaseModel):
    """Response-only. Never sent on a write."""

    model_config = ConfigDict(extra="allow")

    origin: str | None = None


class _RecordBase(BaseModel):
    model_config = ConfigDict(
        populate_by_name=True,
        extra="forbid",
        ser_json_timedelta="float",
    )

    id: str | None = None
    enabled: bool = True
    domain: str
    metadata: RecordMetadata | None = None

    #: Fields the gateway rejects on any write.
    _write_excluded = {"id", "metadata"}

    def write_payload(self) -> dict:
        """Body for POST and PUT.

        Drops response-only fields, and drops `ttlSeconds` on the three types
        that reject it rather than ignore it.
        """
        exclude = set(self._write_excluded)
        if self.type not in TTL_CAPABLE:
            exclude.add("ttl_seconds")
        return self.model_dump(
            by_alias=True,
            exclude=exclude,
            exclude_none=True,
            mode="json",
        )

    @property
    def fqdn(self) -> str:
        """Display name. Overridden by SRV, which stores its label split apart."""
        return self.domain

    @property
    def value(self) -> str:
        """Single-string rendering of the type-specific payload, for list views."""
        raise NotImplementedError


class _TTLRecord(_RecordBase):
    #: 0 means "use the gateway default" and is what the console writes when TTL
    #: is left on Auto. It is a legal stored value, so the floor is 0, not 1.
    ttl_seconds: int = Field(default=DEFAULT_TTL, alias="ttlSeconds", ge=0)

    @property
    def ttl_is_auto(self) -> bool:
        return self.ttl_seconds == 0


class ARecord(_TTLRecord):
    type: Literal["A_RECORD"] = A
    ipv4_address: IPv4Address = Field(alias="ipv4Address")

    @property
    def value(self) -> str:
        return str(self.ipv4_address)


class AAAARecord(_TTLRecord):
    type: Literal["AAAA_RECORD"] = AAAA
    ipv6_address: IPv6Address = Field(alias="ipv6Address")

    @property
    def value(self) -> str:
        return str(self.ipv6_address)


class CNAMERecord(_TTLRecord):
    type: Literal["CNAME_RECORD"] = CNAME
    target_domain: str = Field(alias="targetDomain")

    @property
    def value(self) -> str:
        return self.target_domain


class MXRecord(_RecordBase):
    type: Literal["MX_RECORD"] = MX
    mail_server_domain: str = Field(alias="mailServerDomain")
    priority: int = 10

    @property
    def value(self) -> str:
        return f"{self.priority} {self.mail_server_domain}"


class TXTRecord(_RecordBase):
    type: Literal["TXT_RECORD"] = TXT
    text: str

    @property
    def value(self) -> str:
        return self.text


class SRVRecord(_RecordBase):
    type: Literal["SRV_RECORD"] = SRV
    server_domain: str = Field(alias="serverDomain")
    service: str
    protocol: str
    priority: int = 0
    weight: int = 0
    port: int = Field(ge=0, le=65535)

    @property
    def fqdn(self) -> str:
        """Rebuild `_service._proto.name` for display."""
        return f"{self.service}.{self.protocol}.{self.domain}"

    @property
    def value(self) -> str:
        return f"{self.priority} {self.weight} {self.port} {self.server_domain}"


DnsRecord = Annotated[
    Union[ARecord, AAAARecord, CNAMERecord, MXRecord, TXTRecord, SRVRecord],
    Field(discriminator="type"),
]


class RecordPage(BaseModel):
    """Envelope returned by the list endpoint."""

    model_config = ConfigDict(populate_by_name=True)

    offset: int = 0
    limit: int = 0
    count: int = 0
    total_count: int = Field(default=0, alias="totalCount")
    data: list[DnsRecord] = Field(default_factory=list)


def split_srv_fqdn(fqdn: str) -> tuple[str, str, str]:
    """`_sip._tcp.example.com` -> (`_sip`, `_tcp`, `example.com`).

    The gateway wants these as separate fields on write.
    """
    parts = fqdn.split(".", 2)
    if len(parts) < 3 or not parts[0].startswith("_") or not parts[1].startswith("_"):
        raise ValueError(f"{fqdn!r} is not in _service._proto.name form")
    return parts[0], parts[1], parts[2]

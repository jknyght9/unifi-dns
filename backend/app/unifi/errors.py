"""UniFi API error envelope.

The gateway returns a consistent, machine-readable shape. Surface `message`
verbatim rather than reimplementing validation locally; the gateway knows more
about what it accepts than we do.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class UnifiErrorBody(BaseModel):
    model_config = ConfigDict(extra="allow")

    statusCode: int | None = None
    statusName: str | None = None
    code: str | None = None
    message: str | None = None
    timestamp: str | None = None
    requestPath: str | None = None
    requestId: str | None = None


class UnifiError(Exception):
    """Raised for any non-2xx response from the gateway."""

    def __init__(self, status: int, body: UnifiErrorBody | None, raw: str = "") -> None:
        self.status = status
        self.body = body
        self.raw = raw
        detail = (body.message if body and body.message else raw) or f"HTTP {status}"
        super().__init__(detail)

    @property
    def code(self) -> str | None:
        return self.body.code if self.body else None

    @property
    def request_id(self) -> str | None:
        return self.body.requestId if self.body else None

    def as_dict(self) -> dict:
        return {
            "status": self.status,
            "code": self.code,
            "message": str(self),
            "request_id": self.request_id,
        }


class UnifiAuthError(UnifiError):
    """401/403. The API key is missing, wrong, or revoked."""


class UnifiNotFound(UnifiError):
    """404. Usually a record deleted out from under us."""

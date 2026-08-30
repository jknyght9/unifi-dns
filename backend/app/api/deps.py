from __future__ import annotations

from collections.abc import AsyncIterator

from fastapi import Depends, Request

from app.config import Settings, get_settings
from app.services.changes import Author
from app.unifi.client import UnifiClient


async def get_client(request: Request) -> AsyncIterator[UnifiClient]:
    """One client per app, not per request: it caches the site UUID and owns the
    write lock that serialises mutations against the gateway."""
    yield request.app.state.unifi


def get_author(
    request: Request, settings: Settings = Depends(get_settings)
) -> Author:
    """Who is making this change.

    OIDC (or a trusted forward-auth header) identifies the human. The UniFi
    admin behind the API key is recorded separately, so both halves survive in
    the audit log.
    """
    user = getattr(request.state, "user", None) or {}
    if not user and settings.trusted_user_header:
        header_value = request.headers.get(settings.trusted_user_header)
        if header_value:
            user = {"sub": header_value, "name": header_value}
    return Author(
        subject=user.get("sub"),
        name=user.get("name") or user.get("preferred_username"),
        email=user.get("email"),
        unifi_admin=getattr(request.app.state, "unifi_admin", None),
    )

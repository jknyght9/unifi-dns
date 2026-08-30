"""Authentication.

Deliberately separate from the UniFi API key. OIDC (Authentik, Keycloak,
Pocket ID, Authelia) establishes who the human is; the UniFi key is a service
credential the app uses on the gateway. Keeping them apart matters because the
UniFi settings endpoint returns the device SSH password in plaintext to any key
holder, which makes that key root-equivalent. It must never double as a login.

Two modes:
  - OIDC authorisation code flow, when `oidc_issuer` is configured.
  - A trusted forward-auth header, for deployments already behind an SSO proxy.
If neither is configured the app runs open, which is only appropriate on a
trusted host during development.
"""

from __future__ import annotations

import logging

from authlib.integrations.starlette_client import OAuth
from fastapi import APIRouter, HTTPException, Request
from starlette.responses import JSONResponse, RedirectResponse

from app.config import Settings, get_settings

log = logging.getLogger(__name__)
router = APIRouter()
_oauth = OAuth()

SESSION_KEY = "user"


def configure(settings: Settings) -> bool:
    if not (settings.oidc_issuer and settings.oidc_client_id and settings.oidc_client_secret):
        return False
    _oauth.register(
        name="oidc",
        server_metadata_url=f"{settings.oidc_issuer.rstrip('/')}/.well-known/openid-configuration",
        client_id=settings.oidc_client_id,
        client_secret=settings.oidc_client_secret,
        client_kwargs={"scope": "openid profile email"},
    )
    return True


@router.get("/auth/login")
async def login(request: Request):
    settings = get_settings()
    if not settings.oidc_issuer:
        raise HTTPException(404, "OIDC is not configured")
    redirect_uri = settings.oidc_redirect_url or str(request.url_for("auth_callback"))
    return await _oauth.oidc.authorize_redirect(request, redirect_uri)


@router.get("/auth/callback", name="auth_callback")
async def auth_callback(request: Request):
    token = await _oauth.oidc.authorize_access_token(request)
    claims = token.get("userinfo") or {}
    request.session[SESSION_KEY] = {
        "sub": claims.get("sub"),
        "name": claims.get("name") or claims.get("preferred_username"),
        "email": claims.get("email"),
    }
    return RedirectResponse("/")


@router.post("/auth/logout")
async def logout(request: Request):
    request.session.pop(SESSION_KEY, None)
    return {"ok": True}


@router.get("/auth/me")
async def me(request: Request):
    """Identity, plus which backend is in force.

    The frontend needs the mode to know what to render when unauthenticated:
    an OIDC deployment gets a sign-in button, a forward-auth deployment cannot
    fix it from the browser and gets a configuration message instead.
    """
    settings = get_settings()
    if settings.oidc_issuer and settings.oidc_client_id:
        mode = "oidc"
    elif settings.trusted_user_header:
        mode = "forward-auth"
    else:
        mode = "open"
    user = getattr(request.state, "user", None)
    return {
        "authenticated": bool(user) or mode == "open",
        "user": user,
        "mode": mode,
        # Authorisation is the identity provider's job: anyone it admits gets
        # full access here. There are no roles in this application.
        "authorization": "delegated",
    }


#: Reachable without a session, so an unauthenticated browser can start a login.
PUBLIC_PATHS = frozenset(
    {"/api/health", "/api/auth/login", "/api/auth/callback", "/api/auth/me"}
)


class AuthMiddleware:
    """Populates `request.state.user`, and enforces when a backend is configured.

    Enforcement is deliberately conditional. If neither OIDC nor a trusted
    forward-auth header is set up, the app stays open rather than locking the
    operator out of their own tool; `create_app` logs a warning in that case.
    Once a backend exists, unauthenticated API calls get a 401 instead of
    silently acting as nobody.
    """

    def __init__(self, app, settings: Settings, enforce: bool) -> None:
        self.app = app
        self.settings = settings
        self.enforce = enforce or bool(settings.trusted_user_header)

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request = Request(scope, receive)
        user = None
        session = scope.get("session") or {}
        if isinstance(session, dict):
            user = session.get(SESSION_KEY)
        if not user and self.settings.trusted_user_header:
            value = request.headers.get(self.settings.trusted_user_header)
            if value:
                user = {"sub": value, "name": value}

        scope.setdefault("state", {})
        scope["state"]["user"] = user

        path = scope.get("path", "")
        if (
            self.enforce
            and user is None
            and path.startswith("/api/")
            and path not in PUBLIC_PATHS
        ):
            response = JSONResponse({"detail": "authentication required"}, status_code=401)
            await response(scope, receive, send)
            return

        await self.app(scope, receive, send)

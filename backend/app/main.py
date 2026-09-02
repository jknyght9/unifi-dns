from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager, suppress

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.middleware.sessions import SessionMiddleware

from app import auth
from app.api.routes import router as api_router
from app.api.routes_migrate import router as migrate_router
from app.api.routes_settings import router as settings_router
from app.api.routes_stats import router as stats_router
from app.config import get_settings
from app.unifi.client import UnifiClient
from app.unifi.errors import UnifiError

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("unifi-dns")


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    app.state.unifi = UnifiClient(
        settings.unifi_host,
        settings.unifi_api_key,
        site=settings.unifi_site,
        verify_tls=settings.unifi_verify_tls,
    )
    app.state.unifi_admin = None
    try:
        admin = await app.state.unifi.whoami()
        app.state.unifi_admin = admin.get("name")
        version = await app.state.unifi.application_version()
        log.info("connected to UniFi Network %s as %s", version, app.state.unifi_admin)
    except UnifiError as exc:
        # Do not abort startup: the UI should come up and show the error rather
        # than crash-loop behind a container restart policy.
        log.error("UniFi unreachable at startup: %s", exc)
    # Poll flow telemetry in the background. Started after the client exists so
    # a gateway that is down at boot delays collection rather than startup.
    from app.services.collector import run_collector

    app.state.collector = asyncio.create_task(run_collector(app.state.unifi))

    yield

    app.state.collector.cancel()
    with suppress(asyncio.CancelledError):
        await app.state.collector
    await app.state.unifi.aclose()


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title="unifi-dns", version="0.1.0", lifespan=lifespan)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    oidc_ready = auth.configure(settings)
    # AuthMiddleware reads scope["session"], which SessionMiddleware populates.
    # Starlette runs the LAST-added middleware first (outermost), so
    # SessionMiddleware must be added AFTER AuthMiddleware to run before it —
    # otherwise the session is never loaded when AuthMiddleware checks it and
    # every request looks unauthenticated (OIDC login loops back to sign-in).
    app.add_middleware(auth.AuthMiddleware, settings=settings, enforce=oidc_ready)
    app.add_middleware(SessionMiddleware, secret_key=settings.session_secret)
    if not oidc_ready and not settings.trusted_user_header:
        log.warning(
            "no authentication configured; set OIDC_ISSUER or TRUSTED_USER_HEADER "
            "before exposing this beyond a trusted host"
        )

    app.include_router(auth.router, prefix="/api")
    app.include_router(api_router, prefix="/api")
    app.include_router(settings_router, prefix="/api")
    app.include_router(migrate_router, prefix="/api")
    app.include_router(stats_router, prefix="/api")

    @app.exception_handler(UnifiError)
    async def _unifi_error(_request: Request, exc: UnifiError) -> JSONResponse:
        """Surface the gateway's own message. It knows what it accepts."""
        return JSONResponse(status_code=502, content={"detail": exc.as_dict()})

    @app.get("/api/health")
    async def health() -> dict:
        return {"ok": True}

    return app


app = create_app()

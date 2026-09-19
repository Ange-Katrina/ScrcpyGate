"""FastAPI application factory and infrastructure assembly."""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from . import security, web_ui
from .logging_config import setup_logging
from .middleware import LocaleContextMiddleware, SelectiveGZipMiddleware, WebSocketAccessMiddleware, security_middleware
from .runtime import install_runtime, lifespan
from .version import get_version
from .pow_provider import get_provider
from .routers import (
    admin_access,
    admin_access_log,
    admin_ban,
    admin_geo,
    admin_alas,
    admin_audit,
    admin_overview,
    admin_settings,
    admin_update,
    admin_video,
    admin_workbench,
    alas,
    alas_embed,
    mirror,
    mirror_record,
    notifications,
    public,
    websockets,
)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
STATIC_ROOT = PROJECT_ROOT / "static"


def create_app() -> FastAPI:
    setup_logging()
    api_docs_enabled = security.env_bool("ENABLE_API_DOCS", False)
    app = FastAPI(
        title="ScrcpyGate",
        version=get_version(),
        docs_url="/docs" if api_docs_enabled else None,
        redoc_url="/redoc" if api_docs_enabled else None,
        openapi_url="/openapi.json" if api_docs_enabled else None,
        lifespan=lifespan,
    )
    install_runtime(app)
    provider = get_provider()
    app.add_middleware(SelectiveGZipMiddleware, minimum_size=500)
    app.add_middleware(LocaleContextMiddleware)
    app.add_middleware(WebSocketAccessMiddleware)
    app.middleware("http")(security_middleware)
    # Resolve assets from the project, not the process working directory.
    # Uvicorn and test runners may be launched from a different directory.
    if provider.static_dir is not None:
        # Only a trusted, explicitly installed extension can supply assets.
        # Mount before /static; retain the existing same-origin CSP.
        app.mount("/static/pow-extension", StaticFiles(directory=provider.static_dir), name="pow-extension")
    app.mount("/static", StaticFiles(directory=STATIC_ROOT), name="static")
    app.mount("/shared", StaticFiles(directory=STATIC_ROOT / "shared"), name="shared")
    app.mount("/vendor", StaticFiles(directory=STATIC_ROOT / "vendor"), name="vendor")
    app.mount("/css", StaticFiles(directory=STATIC_ROOT / "css"), name="css")
    web_ui.install(app)
    # Keep this order aligned with the former route declaration order in
    # app.main.  In particular, ALAS proxy paths must stay ahead of generic
    # catch-all routes and WebSocket paths remain registered last.
    app.include_router(public.router)
    app.include_router(notifications.router)
    app.include_router(mirror.router)
    app.include_router(mirror_record.router)
    app.include_router(alas.router)
    app.include_router(alas_embed.router)
    app.include_router(admin_overview.router)
    app.include_router(admin_access.router)
    app.include_router(admin_access_log.router)
    app.include_router(admin_ban.router)
    app.include_router(admin_geo.router)
    app.include_router(admin_video.router)
    app.include_router(admin_alas.router)
    app.include_router(admin_settings.router)
    app.include_router(admin_update.router)
    app.include_router(admin_workbench.router)
    app.include_router(admin_audit.router)
    app.include_router(websockets.router)
    return app


app = create_app()

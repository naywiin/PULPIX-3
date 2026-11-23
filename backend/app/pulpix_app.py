"""Entrypoint multi-usuario de PULPIX (FastAPI).

Envuelve `create_app()` añadiendo:
- Autenticación vía Firebase Auth (Google + Correo/Contraseña) con `PulpixAuthMiddleware`.
- Registro de clientes aislado por usuario (`UserScopedClientStore`).
- Conexiones OAuth cifradas por usuario para Meta Ads y Google Ads.
- Endpoints `/api/auth/*` y `/api/integrations/*`.
"""
from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .auth import PulpixAuthMiddleware
from .config import Settings
from .connections import UserConnectionsStore
from .main import _default_providers, create_app
from .oauth_routes import build_oauth_router
from .registry import ClientRegistry
from .stores import UserScopedClientStore
from .user_providers import UserGoogleAdsProvider, UserMetaProvider


def create_pulpix_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()
    connections = UserConnectionsStore(settings)

    providers = _default_providers(settings)
    providers["meta_ads"] = UserMetaProvider(settings, connections)
    providers["google_ads"] = UserGoogleAdsProvider(settings, connections)

    scoped_store = UserScopedClientStore(settings)
    repo = ClientRegistry(scoped_store)

    app = create_app(
        settings=settings,
        repo=repo,
        providers=providers,
    )
    app.title = "Pulpix Performance API"
    app.state.connections = connections

    # Asegura que el header Authorization sea permitido en CORS.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
        allow_headers=["Content-Type", "Authorization"],
        expose_headers=["Content-Disposition"],
    )
    app.add_middleware(PulpixAuthMiddleware, settings=settings)

    # Montamos las rutas OAuth y de usuario ANTES de la SPA (FastAPI evalúa routers antes de mounts).
    oauth_router = build_oauth_router(settings, connections, repo, app.state.service)
    app.include_router(oauth_router)

    # Reordenar rutas para que el mount "/" de la SPA quede siempre al final.
    spa_routes = [r for r in app.router.routes if getattr(r, "name", None) == "dashboard"]
    other_routes = [r for r in app.router.routes if getattr(r, "name", None) != "dashboard"]
    app.router.routes[:] = other_routes + spa_routes

    return app


app = create_pulpix_app()

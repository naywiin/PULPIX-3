"""Autenticación multi-usuario vía Firebase Auth (Google + Correo/Contraseña).

Valida el `Authorization: Bearer <id_token>` enviado por el frontend en cada
petición a `/api/*` y expone el usuario actual mediante `ContextVar`, de modo
que el registro de clientes, las conexiones OAuth y la caché queden aislados
por `uid` sin riesgo de fugas entre usuarios.
"""
from __future__ import annotations

import contextvars
import logging
import threading
from dataclasses import dataclass
from typing import Any

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from .config import Settings

log = logging.getLogger("mp.auth")


@dataclass(frozen=True)
class AuthenticatedUser:
    uid: str
    email: str | None = None
    name: str | None = None
    picture: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "uid": self.uid,
            "email": self.email,
            "name": self.name,
            "picture": self.picture,
        }


LOCAL_DEV_USER = AuthenticatedUser(
    uid="local-dev",
    email="dev@pulpix.local",
    name="Pulpix Dev",
)

_current_user: contextvars.ContextVar[AuthenticatedUser | None] = contextvars.ContextVar(
    "pulpix_current_user",
    default=None,
)

_firebase_lock = threading.Lock()
_firebase_app: Any = None

# Rutas bajo /api que no requieren Bearer token (healthcheck, config pública y callbacks OAuth
# que validan un `state` firmado con HMAC).
PUBLIC_API_PATHS = {
    "/api/health",
    "/api/auth/config",
    "/api/integrations/meta/callback",
    "/api/integrations/google-ads/callback",
}


def get_current_user() -> AuthenticatedUser | None:
    return _current_user.get()


def get_current_user_uid() -> str:
    user = _current_user.get()
    return user.uid if user else LOCAL_DEV_USER.uid


def set_current_user(user: AuthenticatedUser | None) -> contextvars.Token:
    return _current_user.set(user)


def reset_current_user(token: contextvars.Token) -> None:
    _current_user.reset(token)


def _get_firebase_auth(settings: Settings):
    global _firebase_app
    with _firebase_lock:
        try:
            import firebase_admin
            from firebase_admin import auth as fb_auth
        except ImportError as exc:
            raise RuntimeError(
                "Falta firebase-admin en el backend. Instálalo desde requirements.txt."
            ) from exc

        if _firebase_app is None:
            if firebase_admin._apps:
                _firebase_app = firebase_admin.get_app()
            else:
                options = {"projectId": settings.firebase_project_id} if settings.firebase_project_id else None
                _firebase_app = firebase_admin.initialize_app(options=options)
        return fb_auth


def verify_firebase_token(id_token: str, settings: Settings) -> AuthenticatedUser:
    fb_auth = _get_firebase_auth(settings)
    decoded = fb_auth.verify_id_token(id_token)
    uid = str(decoded.get("uid") or decoded.get("sub") or "").strip()
    if not uid:
        raise ValueError("Token sin uid válido")
    return AuthenticatedUser(
        uid=uid,
        email=decoded.get("email"),
        name=decoded.get("name"),
        picture=decoded.get("picture"),
    )


class PulpixAuthMiddleware(BaseHTTPMiddleware):
    """Autentica las peticiones `/api/*` y fija el usuario actual en ContextVar."""

    def __init__(self, app, settings: Settings) -> None:
        super().__init__(app)
        self.settings = settings

    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        if request.method == "OPTIONS" or not path.startswith("/api/") or path in PUBLIC_API_PATHS:
            return await call_next(request)

        auth_header = request.headers.get("Authorization", "").strip()
        user: AuthenticatedUser | None = None

        if auth_header.lower().startswith("bearer "):
            raw_token = auth_header[7:].strip()
            if raw_token:
                try:
                    user = verify_firebase_token(raw_token, self.settings)
                except Exception as exc:
                    log.warning("Token Firebase inválido en %s: %s", path, type(exc).__name__)
                    return JSONResponse(
                        status_code=401,
                        content={"detail": "Sesión inválida o expirada. Inicia sesión nuevamente."},
                    )

        if user is None:
            if self.settings.auth_required:
                return JSONResponse(
                    status_code=401,
                    content={"detail": "Debes iniciar sesión para acceder a Pulpix."},
                )
            user = LOCAL_DEV_USER

        ctx_token = set_current_user(user)
        try:
            request.state.user = user
            return await call_next(request)
        finally:
            reset_current_user(ctx_token)

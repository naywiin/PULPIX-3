"""Configuración del backend leída exclusivamente de variables de entorno.

Los secretos (tokens OAuth, developer token de Google Ads, clave de cifrado)
NUNCA se guardan en archivos del proyecto ni se envían al navegador: solo se
leen del entorno del proceso en el momento de usarlos.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
PROJECT_DIR = BASE_DIR.parent


def _int_env(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except ValueError:
        return default


def _customer_id_env(name: str, default: str = "") -> str:
    """ID de cuenta de Google Ads sin guiones (opcional)."""
    raw = os.environ.get(name) or default
    digits = "".join(ch for ch in raw if ch.isdigit())
    return digits or default


def _bool_env(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() not in {"0", "false", "no", "off", "disabled"}


@dataclass
class Settings:
    # Persistencia: "file" para desarrollo local, "firestore" para producción.
    store_backend: str = field(default_factory=lambda: os.environ.get("MP_STORE", "file"))
    firestore_collection: str = field(default_factory=lambda: os.environ.get("MP_FIRESTORE_COLLECTION", "clients"))
    budgets_firestore_collection: str = field(default_factory=lambda: os.environ.get("MP_BUDGETS_FIRESTORE_COLLECTION", "performance_budgets"))
    reporting_groups_firestore_collection: str = field(
        default_factory=lambda: os.environ.get("MP_REPORTING_GROUPS_FIRESTORE_COLLECTION", "performance_reporting_groups")
    )
    reporting_group_snapshots_firestore_collection: str = field(
        default_factory=lambda: os.environ.get("MP_REPORTING_GROUP_SNAPSHOTS_FIRESTORE_COLLECTION", "performance_reporting_group_snapshots")
    )
    users_firestore_collection: str = field(
        default_factory=lambda: os.environ.get("MP_USERS_FIRESTORE_COLLECTION", "users")
    )
    firestore_project: str | None = field(default_factory=lambda: os.environ.get("MP_FIRESTORE_PROJECT") or None)
    firestore_database: str | None = field(default_factory=lambda: os.environ.get("MP_FIRESTORE_DATABASE") or None)
    clients_file: Path = field(default_factory=lambda: Path(os.environ.get("MP_CLIENTS_FILE", BASE_DIR / "data" / "clients.json")))
    clients_seed_file: Path = field(default_factory=lambda: Path(os.environ.get("MP_CLIENTS_SEED_FILE", BASE_DIR / "data" / "clients.json")))
    data_dir: Path = field(default_factory=lambda: Path(os.environ.get("MP_DATA_DIR", BASE_DIR / "data")))
    # En Pulpix cada usuario empieza limpio con sus propias cuentas; seed desactivado por defecto.
    seed_clients: bool = field(default_factory=lambda: _bool_env("MP_SEED_CLIENTS", False))
    cache_ttl_seconds: int = field(default_factory=lambda: _int_env("MP_CACHE_TTL_SECONDS", 300))
    discovery_ttl_seconds: int = field(default_factory=lambda: _int_env("MP_DISCOVERY_TTL_SECONDS", 1800))
    # MCC raíz opcional (en multi-usuario se descubren las cuentas accesibles del usuario vía OAuth).
    google_ads_manager_customer_id: str = field(
        default_factory=lambda: _customer_id_env("GOOGLE_ADS_MANAGER_CUSTOMER_ID", "")
    )
    meta_api_version: str = field(default_factory=lambda: os.environ.get("META_API_VERSION", "v24.0"))
    cors_origins: list[str] = field(
        default_factory=lambda: [
            o.strip()
            for o in os.environ.get("MP_CORS_ORIGINS", "http://127.0.0.1:5173,http://localhost:5173").split(",")
            if o.strip()
        ]
    )
    static_dir: Path = field(default_factory=lambda: Path(os.environ.get("MP_STATIC_DIR", PROJECT_DIR / "dashboard" / "dist")))

    # --- Autenticación multi-usuario (Firebase Auth) ---
    auth_required: bool = field(default_factory=lambda: _bool_env("PULPIX_AUTH_REQUIRED", True))
    firebase_project_id: str | None = field(
        default_factory=lambda: os.environ.get("FIREBASE_PROJECT_ID") or os.environ.get("MP_FIRESTORE_PROJECT") or None
    )

    # --- OAuth por usuario (Meta Ads & Google Ads) ---
    oauth_redirect_base_url: str = field(
        default_factory=lambda: os.environ.get("PULPIX_PUBLIC_URL", "http://localhost:8000").rstrip("/")
    )
    frontend_url: str = field(
        default_factory=lambda: os.environ.get("PULPIX_FRONTEND_URL", "http://localhost:5173").rstrip("/")
    )
    meta_app_id: str | None = field(default_factory=lambda: os.environ.get("META_APP_ID") or None)
    google_oauth_client_id: str | None = field(default_factory=lambda: os.environ.get("GOOGLE_OAUTH_CLIENT_ID") or None)

    @property
    def meta_app_secret(self) -> str | None:
        return os.environ.get("META_APP_SECRET") or None

    @property
    def google_oauth_client_secret(self) -> str | None:
        return os.environ.get("GOOGLE_OAUTH_CLIENT_SECRET") or None

    @property
    def token_encryption_key(self) -> str | None:
        """Clave Fernet (32 bytes base64) para cifrar tokens OAuth en reposo."""
        return os.environ.get("PULPIX_TOKEN_ENCRYPTION_KEY") or None

    @property
    def meta_access_token(self) -> str | None:
        return os.environ.get("META_ACCESS_TOKEN") or None

    @property
    def google_ads_developer_token(self) -> str | None:
        return os.environ.get("GOOGLE_ADS_DEVELOPER_TOKEN") or None

    @property
    def woosync_analytics_api_key(self) -> str | None:
        """Clave de analytics de WooSync; nunca se serializa ni se guarda."""
        return os.environ.get("WOOSYNC_ANALYTICS_API_KEY") or None

    @property
    def spotify_client_id(self) -> str | None:
        """Client ID de Spotify, inyectado desde Secret Manager en runtime."""
        return os.environ.get("SPOTIFY_CLIENT_ID") or None

    @property
    def spotify_client_secret(self) -> str | None:
        """Client secret de Spotify; nunca se serializa ni se guarda."""
        return os.environ.get("SPOTIFY_CLIENT_SECRET") or None

    @property
    def spotify_refresh_token(self) -> str | None:
        """Refresh token de Spotify; el access token se genera dinámicamente."""
        return os.environ.get("SPOTIFY_REFRESH_TOKEN") or None

    @property
    def pinterest_access_token(self) -> str | None:
        """Token de Pinterest inyectado desde Secret Manager en runtime."""
        return os.environ.get("PINTEREST_ACCESS_TOKEN") or None

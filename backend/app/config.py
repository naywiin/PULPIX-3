"""Configuración del backend leída exclusivamente de variables de entorno.

Los secretos (token de Meta, developer token de Google Ads) NUNCA se guardan en
archivos del proyecto ni se envían al navegador: solo se leen del entorno del
proceso en el momento de usarlos. En Cloud Run se inyectan desde Secret Manager
con `--set-secrets META_ACCESS_TOKEN=<secreto>:latest`.
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


def _customer_id_env(name: str, default: str) -> str:
    """ID de cuenta de Google Ads sin guiones (no es un secreto: es un identificador)."""
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
    # Registro de clientes. En Cloud Run apúntalo a un volumen persistente
    # (bucket montado): el disco del contenedor es efímero y no se comparte entre instancias.
    # Dónde vive el registro EN SERIO. "file" solo es duradero si la ruta está
    # en un volumen persistente; dentro de la imagen se pierde en cada revisión
    # de Cloud Run (ver stores.py). En producción usa "firestore".
    store_backend: str = field(default_factory=lambda: os.environ.get("MP_STORE", "file"))
    firestore_collection: str = field(default_factory=lambda: os.environ.get("MP_FIRESTORE_COLLECTION", "clients"))
    budgets_firestore_collection: str = field(default_factory=lambda: os.environ.get("MP_BUDGETS_FIRESTORE_COLLECTION", "performance_budgets"))
    reporting_groups_firestore_collection: str = field(
        default_factory=lambda: os.environ.get("MP_REPORTING_GROUPS_FIRESTORE_COLLECTION", "performance_reporting_groups")
    )
    reporting_group_snapshots_firestore_collection: str = field(
        default_factory=lambda: os.environ.get("MP_REPORTING_GROUP_SNAPSHOTS_FIRESTORE_COLLECTION", "performance_reporting_group_snapshots")
    )
    firestore_project: str | None = field(default_factory=lambda: os.environ.get("MP_FIRESTORE_PROJECT") or None)
    firestore_database: str | None = field(default_factory=lambda: os.environ.get("MP_FIRESTORE_DATABASE") or None)
    clients_file: Path = field(default_factory=lambda: Path(os.environ.get("MP_CLIENTS_FILE", BASE_DIR / "data" / "clients.json")))
    # SEED: registro incluido en la imagen. Solo se usa para inicializar un
    # almacenamiento VACÍO, nunca para sobrescribir datos existentes.
    clients_seed_file: Path = field(default_factory=lambda: Path(os.environ.get("MP_CLIENTS_SEED_FILE", BASE_DIR / "data" / "clients.json")))
    # Revisions aisladas pueden desactivar explícitamente el seed incluso si
    # la colección de prueba queda vacía. Por defecto conserva compatibilidad.
    seed_clients: bool = field(default_factory=lambda: _bool_env("MP_SEED_CLIENTS", True))
    cache_ttl_seconds: int = field(default_factory=lambda: _int_env("MP_CACHE_TTL_SECONDS", 300))
    discovery_ttl_seconds: int = field(default_factory=lambda: _int_env("MP_DISCOVERY_TTL_SECONDS", 1800))
    # MCC raíz de la agencia: fuente de la jerarquía de clientes de Google Ads
    # (customer_client) y valor de login-customer-id al consultar cuentas cliente.
    google_ads_manager_customer_id: str = field(
        default_factory=lambda: _customer_id_env("GOOGLE_ADS_MANAGER_CUSTOMER_ID", "7954113535")
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

    # Los secretos se exponen como propiedades para leerlos en cada uso y no
    # dejarlos en el repr() del objeto (que podría acabar en un log).
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

"""WooSync Analytics — integración de solo lectura para Ssiento.

La credencial se lee del entorno en cada petición y viaja exclusivamente en el
header Authorization. Los filtros se mandan con ``params`` para que requests
se encargue de codificarlos de forma segura.
"""
from __future__ import annotations

import logging
from typing import Any

import requests

from ..config import Settings
from .base import ProviderError, ProviderNotConfigured

log = logging.getLogger("mp.woosync")

OVERVIEW_URL = "https://woosync.ssiento.com/api/integrations/analytics/overview"
OPTIONAL_ARRAYS = (
    "daily_sales",
    "weekly_sales",
    "monthly_sales",
    "top_products",
    "top_categories",
    "top_customers",
    "order_statuses",
)
SENSITIVE_KEYS = {"authorization", "woosync_analytics_api_key", "api_key", "access_token", "token"}


def strip_sensitive(value: Any, secret: str | None = None) -> Any:
    """Evita propagar credenciales aunque un upstream las reflejara por error."""
    if isinstance(value, dict):
        return {
            key: strip_sensitive(item, secret)
            for key, item in value.items()
            if str(key).lower() not in SENSITIVE_KEYS
        }
    if isinstance(value, list):
        return [strip_sensitive(item, secret) for item in value]
    if secret and isinstance(value, str) and secret in value:
        return value.replace(secret, "[redacted]")
    return value


def build_overview_params(
    *,
    from_date: str | None = None,
    to_date: str | None = None,
    branches: list[str] | tuple[str, ...] | None = None,
    top_limit: int | None = 10,
    compare_to: str | None = None,
    **extra_filters: Any,
) -> dict[str, Any]:
    """Construye parámetros omitiendo valores vacíos y admite filtros futuros."""
    params: dict[str, Any] = {
        "from": from_date,
        "to": to_date,
        "branches": ",".join(branches) if branches else None,
        "top_limit": top_limit,
        "compare_to": compare_to,
        **extra_filters,
    }
    return {key: value for key, value in params.items() if value is not None and value != "" and value != []}


def validate_overview(payload: Any) -> dict[str, Any]:
    """Valida el contrato mínimo y tolera propiedades nuevas del proveedor."""
    if not isinstance(payload, dict):
        raise ProviderError("WooSync devolvió una respuesta inválida.")
    overview = payload.get("sales_overview")
    if not isinstance(overview, dict):
        raise ProviderError("WooSync no devolvió sales_overview.")
    # Deliberadamente NO hay compatibilidad con el typo histórico total_sale.
    if "total_sales" not in overview:
        raise ProviderError("WooSync no devolvió sales_overview.total_sales.")

    normalized = dict(payload)
    for field in OPTIONAL_ARRAYS:
        value = normalized.get(field)
        if value is None:
            normalized[field] = []
        elif not isinstance(value, list):
            raise ProviderError(f"WooSync devolvió {field} con formato inválido.")
    customers = normalized.get("customers")
    if customers is not None and not isinstance(customers, (dict, list)):
        raise ProviderError("WooSync devolvió customers con formato inválido.")
    applied = normalized.get("applied_filters")
    if applied is None:
        normalized["applied_filters"] = {}
    elif not isinstance(applied, dict):
        raise ProviderError("WooSync devolvió applied_filters con formato inválido.")
    comparison = normalized.get("comparison")
    if comparison is not None and not isinstance(comparison, dict):
        raise ProviderError("WooSync devolvió comparison con formato inválido.")
    return normalized


def validate_applied_branches(payload: dict[str, Any], requested: list[str] | tuple[str, ...] | None) -> None:
    """Avisa si WooSync no aplicó las sucursales solicitadas.

    El payload se conserva: una discrepancia es una señal operativa controlada,
    no un motivo para ocultar una respuesta que sigue siendo útil para diagnóstico.
    """
    if not requested:
        return
    received = payload.get("applied_filters", {}).get("branches")
    clean_received = [str(value).strip().lower() for value in received] if isinstance(received, list) else None
    clean_requested = [str(value).strip().lower() for value in requested]
    if clean_received is None or sorted(set(clean_received)) != sorted(set(clean_requested)):
        log.warning(
            "WooSync aplicó branches distintos a los solicitados: solicitados=%s aplicados=%s",
            clean_requested,
            clean_received,
        )


class WooSyncProvider:
    platform = "woosync"

    def __init__(self, settings: Settings, session: requests.Session | None = None) -> None:
        self.settings = settings
        self.session = session or requests.Session()

    def _headers(self) -> dict[str, str]:
        token = self.settings.woosync_analytics_api_key
        if not token:
            raise ProviderNotConfigured(
                "WooSync no está configurado en el servidor (falta WOOSYNC_ANALYTICS_API_KEY)."
            )
        return {"Authorization": f"Bearer {token}", "Accept": "application/json"}

    def overview(self, **filters: Any) -> dict[str, Any]:
        params = build_overview_params(**filters)
        try:
            response = self.session.get(
                OVERVIEW_URL,
                headers=self._headers(),
                params=params,
                timeout=(5, 45),
            )
        except requests.Timeout:
            raise ProviderError("WooSync tardó demasiado en responder.") from None
        except requests.RequestException:
            raise ProviderError("No se pudo conectar con WooSync.") from None

        if response.status_code == 401:
            raise ProviderNotConfigured("WooSync rechazó la credencial configurada (HTTP 401).")
        if response.status_code >= 400:
            raise ProviderError(f"WooSync respondió con HTTP {response.status_code}.")
        try:
            payload = response.json()
        except ValueError:
            raise ProviderError("WooSync devolvió JSON inválido.") from None
        normalized = validate_overview(strip_sensitive(payload, self.settings.woosync_analytics_api_key))
        validate_applied_branches(normalized, filters.get("branches"))
        return normalized

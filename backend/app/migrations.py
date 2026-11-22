"""Migraciones del registro de clientes.

Regla de oro: **una migración nunca borra ni resetea configuración**. Solo
traduce el formato antiguo al nuevo, conservando todo lo que el usuario ya
había configurado. Son idempotentes: aplicarlas dos veces da el mismo
resultado, así que un arranque repetido no puede estropear nada.

Historial de versiones
----------------------
1. Una cuenta por plataforma: `google_ads_customer_id`, `meta_ad_account_id`,
   `ga4_property_id`.
2. GA4 multi-propiedad: aparece `ga4_property_ids`.
3. Fuentes de campañas: aparecen `google_ads_sources` y `meta_ads_sources`,
   y en Meta el nivel de ad set (`adset_selections`).
4. Visibilidad de métricas por cliente: aparece `hidden_metrics` (lista de
   familias de métrica ocultas en el dashboard; ver `metrics.py`).
5. Spotify Ads: aparece `spotify_ads_sources`; se inicializa vacío cuando aún
   no existe una configuración explícita. No se crean asignaciones durante un
   arranque o despliegue.

Los modelos (`models.Client`) ya saben migrar campo-a-campo al construirse; lo
que hace este módulo es dejar el DOCUMENTO PERSISTIDO en la forma nueva y
anotar la versión, para no depender de esa traducción en cada lectura.
"""
from __future__ import annotations

import logging
from typing import Any, Callable

log = logging.getLogger("mp.migrations")

#: Versión de esquema que escribe esta build.
SCHEMA_VERSION = 5


def _detect_version(payload: dict[str, Any]) -> int:
    """Versión de un documento que aún no la declara."""
    declared = payload.get("schema_version")
    if isinstance(declared, int) and declared > 0:
        return declared
    clients = payload.get("clients") or []
    if any("spotify_ads_sources" in c for c in clients):
        return 5
    if any("hidden_metrics" in c for c in clients):
        return 4
    if any("google_ads_sources" in c or "meta_ads_sources" in c for c in clients):
        return 3
    if any("ga4_property_ids" in c for c in clients):
        return 2
    return 1


def _to_v2(payload: dict[str, Any]) -> dict[str, Any]:
    """`ga4_property_id` → `ga4_property_ids` sin perder la propiedad."""
    for c in payload.get("clients") or []:
        if c.get("ga4_property_ids"):
            continue
        pid = c.get("ga4_property_id")
        c["ga4_property_ids"] = [pid] if pid else []
    return payload


def _to_v3(payload: dict[str, Any]) -> dict[str, Any]:
    """Cuenta única → lista de fuentes, con la semántica que ya tenía.

    Una cuenta configurada antes significaba "todas las campañas de esta
    cuenta", que es exactamente `campaign_ids: []` + `allocation: exclusive`.
    """
    for c in payload.get("clients") or []:
        if not c.get("google_ads_sources"):
            customer_id = c.get("google_ads_customer_id")
            c["google_ads_sources"] = (
                [{
                    "customer_id": customer_id,
                    "login_customer_id": c.get("google_ads_login_customer_id"),
                    "campaign_ids": [],
                    "allocation": "exclusive",
                }]
                if customer_id else []
            )
        if not c.get("meta_ads_sources"):
            account_id = c.get("meta_ad_account_id")
            c["meta_ads_sources"] = (
                [{
                    "ad_account_id": account_id,
                    "campaign_ids": [],
                    "allocation": "exclusive",
                    "adset_selections": [],
                }]
                if account_id else []
            )
        # Las fuentes de Meta que vienen de la v3 temprana no traían el nivel
        # de ad set; se completa para que el documento sea homogéneo.
        for src in c.get("meta_ads_sources") or []:
            src.setdefault("adset_selections", [])
    return payload


def _to_v4(payload: dict[str, Any]) -> dict[str, Any]:
    """Ningún cliente tenía `hidden_metrics` antes: se completa con lista
    vacía, que es exactamente "ninguna métrica oculta" (el comportamiento que
    ya tenían)."""
    for c in payload.get("clients") or []:
        c.setdefault("hidden_metrics", [])
    return payload


def _to_v5(payload: dict[str, Any]) -> dict[str, Any]:
    """Añade el campo sin asignar una cuenta por inferencia.

    `setdefault` conserva cualquier vínculo que el usuario ya haya guardado;
    los registros antiguos reciben una lista vacía y requieren configuración
    explícita antes de consultar Spotify Ads.
    """
    for c in payload.get("clients") or []:
        c.setdefault("spotify_ads_sources", [])
    return payload


#: versión de origen → función que la lleva a la siguiente.
STEPS: dict[int, Callable[[dict[str, Any]], dict[str, Any]]] = {
    1: _to_v2,
    2: _to_v3,
    3: _to_v4,
    4: _to_v5,
}


def migrate(payload: dict[str, Any]) -> tuple[dict[str, Any], int, bool]:
    """Lleva un documento a `SCHEMA_VERSION`.

    Devuelve (documento, versión de origen, cambió). `cambió` es False cuando ya
    estaba al día, para no reescribir el store en cada arranque.
    """
    payload = dict(payload or {})
    payload.setdefault("clients", [])
    origin = _detect_version(payload)
    version = origin
    while version < SCHEMA_VERSION:
        step = STEPS.get(version)
        if step is None:  # pragma: no cover - solo si falta un paso
            raise RuntimeError(f"Falta la migración de la versión {version}")
        payload = step(payload)
        version += 1
    changed = origin != SCHEMA_VERSION or payload.get("schema_version") != SCHEMA_VERSION
    payload["schema_version"] = SCHEMA_VERSION
    if origin != SCHEMA_VERSION:
        log.info("Registro migrado de la versión %d a la %d (%d clientes, sin pérdidas)",
                 origin, SCHEMA_VERSION, len(payload.get("clients") or []))
    return payload, origin, changed


def backup_name(origin: int) -> str:
    """Nombre del respaldo lógico que se guarda antes de migrar."""
    return f"backup_v{origin}"

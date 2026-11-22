"""Consolidación de varias propiedades GA4 de un mismo cliente.

Reglas (pactadas con el usuario):

- `sessions`, `engaged_sessions`, `key_events`, `revenue` → SUMA.
- `engagement_rate` → se RECALCULA como SUM(engaged_sessions) / SUM(sessions).
  Nunca se promedian tasas por propiedad: daría un número distinto al real.
- `users` (activeUsers) → se SUMA, pero es la única métrica NO deduplicable:
  un mismo usuario puede estar en dos propiedades. La UI la etiqueta
  explícitamente como "Usuarios activos (suma de propiedades)".

Las series y tablas se agrupan por su clave (`date`, source/medium, campaign,
landing page) sumando las métricas de todas las propiedades.
"""
from __future__ import annotations

from typing import Any, Iterable

# Métricas que se suman tal cual.
SUM_METRICS = ("users", "sessions", "engaged_sessions", "key_events", "revenue")
# Tablas consolidadas por nombre.
BREAKDOWN_KEYS = ("by_source_medium", "by_campaign", "by_landing_page")
# Filas por tabla consolidada, igual que el top de cada propiedad.
BREAKDOWN_LIMIT = 50


def empty_metrics() -> dict[str, Any]:
    return {"users": 0, "sessions": 0, "engaged_sessions": 0, "engagement_rate": 0.0, "key_events": 0.0, "revenue": 0.0}


def _add(target: dict[str, Any], src: dict[str, Any]) -> None:
    for m in SUM_METRICS:
        target[m] = (target.get(m) or 0) + (src.get(m) or 0)


def _finish(m: dict[str, Any]) -> dict[str, Any]:
    """Cierra un acumulado: enteros donde corresponde y tasa recalculada."""
    sessions = m.get("sessions") or 0
    engaged = m.get("engaged_sessions") or 0
    return {
        "users": int(m.get("users") or 0),
        "sessions": int(sessions),
        "engaged_sessions": int(engaged),
        # SUM(engagedSessions) / SUM(sessions), no el promedio de las tasas.
        "engagement_rate": (engaged / sessions) if sessions else 0.0,
        "key_events": round(float(m.get("key_events") or 0), 2),
        "revenue": round(float(m.get("revenue") or 0), 2),
    }


def _group(rows: Iterable[dict[str, Any]], key: str) -> list[dict[str, Any]]:
    """Agrupa filas por `key` sumando métricas (valores iguales se unen)."""
    acc: dict[str, dict[str, Any]] = {}
    for row in rows:
        k = str(row.get(key) or "")
        bucket = acc.setdefault(k, {key: k, **empty_metrics()})
        _add(bucket, row)
    return [{key: v[key], **_finish(v)} for v in acc.values()]


def consolidate(periods: list[dict[str, Any]]) -> dict[str, Any]:
    """Un solo periodo GA4 a partir de los periodos de N propiedades.

    `periods` son las respuestas de `GA4Provider.performance` que SÍ funcionaron;
    las propiedades que fallaron se omiten (el aviso lo arma el servicio).
    """
    totals = empty_metrics()
    for p in periods:
        _add(totals, p.get("totals") or {})

    daily = sorted(
        _group((d for p in periods for d in (p.get("daily") or [])), "date"),
        key=lambda d: d["date"],
    )

    out: dict[str, Any] = {
        "totals": _finish(totals),
        "daily": daily,
        "properties_count": len(periods),
    }
    for key in BREAKDOWN_KEYS:
        rows = _group((r for p in periods for r in (p.get(key) or [])), "name")
        rows.sort(key=lambda r: (-r["sessions"], r["name"]))
        out[key] = rows[:BREAKDOWN_LIMIT]
    return out


def currencies(periods: list[dict[str, Any]]) -> list[str]:
    """Monedas distintas entre las propiedades consolidadas, en orden estable."""
    seen: list[str] = []
    for p in periods:
        c = p.get("currency")
        if c and c not in seen:
            seen.append(c)
    return seen

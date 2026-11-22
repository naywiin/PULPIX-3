"""Consolidación de campañas de varias cuentas publicitarias en un cliente.

Regla única e innegociable: **las métricas derivadas se recalculan desde las
métricas base ya sumadas**, nunca se promedian entre cuentas ni entre campañas.

    CTR  = SUM(clicks) / SUM(impressions)
    CPC  = SUM(spend) / SUM(clicks)
    CPM  = SUM(spend) / SUM(impressions) * 1000
    CPA  = SUM(spend) / SUM(conversions)
    ROAS = SUM(conversion_value) / SUM(spend)

Promediar los ratios de dos cuentas da un número que no existe: una cuenta con
10 clics y otra con 10 000 pesarían igual.

`reach` (Meta) se suma, pero NO es deduplicable entre cuentas ni entre campañas:
una misma persona alcanzada en dos campañas cuenta dos veces. Por eso el
resultado lleva `reach_is_sum` para que la UI lo etiquete, igual que hacemos con
los usuarios activos de GA4.
"""
from __future__ import annotations

from typing import Any, Iterable

from .metrics import BASE_FIELDS, derive

# Métricas base que se suman. Son las de metrics.BASE_FIELDS más las que solo
# trae alguna plataforma (reach/inline_link_clicks en Meta, all_* en Google).
SUM_FIELDS = (
    *BASE_FIELDS,
    "reach",
    "inline_link_clicks",
    "leads",
    "purchases",
    "all_conversions",
    "all_conversions_value",
    "completes",
    "starts",
    "first_quartiles",
    "midpoints",
    "third_quartiles",
    "video_views",
    "video_expands",
)

#: Claves que identifican la procedencia de una campaña (nunca se pierden).
SOURCE_KEYS = ("source_account_id", "source_account_name")


def empty() -> dict[str, float]:
    return {f: 0.0 for f in SUM_FIELDS}


def add(target: dict[str, Any], src: dict[str, Any]) -> None:
    for f in SUM_FIELDS:
        value = src.get(f)
        if value:
            target[f] = (target.get(f) or 0) + value


def sum_rows(rows: Iterable[dict[str, Any]]) -> dict[str, Any]:
    """Suma las métricas base de varias filas y recalcula las derivadas."""
    totals = empty()
    rows = list(rows)
    for r in rows:
        add(totals, r)
    totals["leads"] = sum(r["leads"] for r in rows) if rows and all(r.get("leads") is not None for r in rows) else None
    totals["purchases"] = sum(r["purchases"] for r in rows) if rows and all(r.get("purchases") is not None for r in rows) else None
    result = derive(totals)
    value_missing = any(
        r.get("value_available") is False
        or ("conversion_value" in r and r.get("conversion_value") is None)
        for r in rows
    )
    if any("value_available" in r for r in rows):
        result["value_available"] = not value_missing and all(r.get("value_available") is True for r in rows)
    if value_missing:
        result["conversion_value"] = None
        result["roas"] = None
    result["cost_per_result"] = result["spend"] / result["leads"] if result["leads"] not in (None, 0) else None
    result["cost_per_purchase"] = result["spend"] / result["purchases"] if result["purchases"] not in (None, 0) else None
    return result


def merge_daily(rows: Iterable[dict[str, Any]], date_key: str = "date") -> list[dict[str, Any]]:
    """Une series diarias de varias fuentes: mismo día → métricas sumadas."""
    by_day: dict[str, dict[str, Any]] = {}
    for r in rows:
        day = r.get(date_key) or r.get("date_start")
        if not day:
            continue
        bucket = by_day.setdefault(str(day), empty())
        if r.get("value_available") is False or ("conversion_value" in r and r.get("conversion_value") is None):
            bucket["_value_missing"] = True
        add(bucket, r)
    result = []
    for day, values in sorted(by_day.items()):
        missing = values.pop("_value_missing", False)
        derived = derive(values)
        if missing:
            derived["conversion_value"] = None
            derived["roas"] = None
            derived["value_available"] = False
        result.append({"date": day, **derived})
    return result


def merge_campaigns(rows: Iterable[dict[str, Any]], key: str = "campaign_id") -> list[dict[str, Any]]:
    """Agrupa campañas por cuenta + campaña.

    La identidad NUNCA es el campaign_id suelto: dos cuentas distintas pueden
    reutilizar el mismo número y se mezclarían datos de clientes diferentes.
    """
    merged: dict[tuple[str, str], dict[str, Any]] = {}
    for r in rows:
        ident = (str(r.get("source_account_id") or ""), str(r.get(key) or ""))
        bucket = merged.get(ident)
        if bucket is None:
            # Se conserva todo lo descriptivo de la primera aparición (nombre,
            # estado, canal y de qué cuenta viene).
            bucket = {k: v for k, v in r.items() if k not in SUM_FIELDS}
            bucket.update(empty())
            merged[ident] = bucket
        if r.get("leads") is None:
            bucket["_leads_missing"] = True
        if r.get("purchases") is None:
            bucket["_purchases_missing"] = True
        if r.get("value_available") is False or ("conversion_value" in r and r.get("conversion_value") is None):
            bucket["_value_missing"] = True
        add(bucket, r)
    result = []
    for bucket in merged.values():
        leads_missing = bucket.pop("_leads_missing", False)
        purchases_missing = bucket.pop("_purchases_missing", False)
        value_missing = bucket.pop("_value_missing", False)
        if leads_missing:
            bucket["leads"] = None
        if purchases_missing:
            bucket["purchases"] = None
        campaign = derive(bucket)
        if value_missing:
            campaign["value_available"] = False
            campaign["conversion_value"] = None
            campaign["roas"] = None
        campaign["cost_per_result"] = campaign["spend"] / campaign["leads"] if campaign["leads"] else None
        campaign["cost_per_purchase"] = campaign["spend"] / campaign["purchases"] if campaign["purchases"] else None
        result.append(campaign)
    return result


def tag_source(rows: Iterable[dict[str, Any]], account_id: str, account_name: str | None) -> list[dict[str, Any]]:
    """Marca cada fila con la cuenta de la que viene, para poder rastrearla."""
    return [{**r, "source_account_id": str(account_id), "source_account_name": account_name} for r in rows]


def select_campaigns(
    campaigns: list[dict[str, Any]],
    account_id: str,
    campaign_ids: list[str],
    blocked: set[str],
) -> list[dict[str, Any]]:
    """Campañas de una fuente que le tocan a este cliente.

    - `campaign_ids` con valores → SOLO esas (selección explícita del usuario).
    - `campaign_ids` vacío → todas las de la cuenta MENOS las que otro cliente
      tenga asignadas en exclusiva (`blocked`), para no contarlas dos veces.
    """
    if campaign_ids:
        wanted = set(campaign_ids)
        return [c for c in campaigns if str(c.get("campaign_id")) in wanted]
    return [c for c in campaigns if str(c.get("campaign_id")) not in blocked]

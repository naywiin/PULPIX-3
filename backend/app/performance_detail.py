"""Filtros y agregados comunes del detalle de performance."""
from __future__ import annotations

from typing import Any, Iterable, Literal

DetailLevel = Literal["campaign", "ad"]
StatusFilter = Literal["all", "active", "inactive"]
SpendFilter = Literal["all", "with_spend", "without_spend"]

STATUS_LABELS = {
    "ENABLED": "Activo",
    "ACTIVE": "Activo",
    "PAUSED": "Pausado",
    "REMOVED": "Eliminado",
    "ARCHIVED": "Archivado",
    "CAMPAIGN_PAUSED": "Campaña pausada",
    "ADSET_PAUSED": "Ad set pausado",
    "WITH_ISSUES": "Con problemas",
}

BASE_METRICS = ("spend", "impressions", "clicks", "conversions", "purchases", "leads", "conversion_value", "reach")


def current_entity_status(row: dict[str, Any]) -> str | None:
    """Meta effective_status es más cercano a la entrega actual; Google usa status."""
    value = row.get("effective_status") or row.get("status")
    return str(value).upper() if value is not None else None


def is_active(row: dict[str, Any]) -> bool:
    return current_entity_status(row) in {"ENABLED", "ACTIVE"}


def status_label(row: dict[str, Any]) -> str:
    status = current_entity_status(row)
    return STATUS_LABELS.get(status or "", status.replace("_", " ").title() if status else "Desconocido")


def filter_detail_rows(
    rows: Iterable[dict[str, Any]],
    status: StatusFilter = "all",
    spend: SpendFilter = "all",
) -> list[dict[str, Any]]:
    """Aplica status y spend de forma independiente. Null nunca equivale a cero."""
    result = []
    for original in rows:
        row = {**original, "status_label": status_label(original)}
        def ratio(numerator: str, denominator: str) -> float | None:
            top, bottom = row.get(numerator), row.get(denominator)
            return top / bottom if top is not None and bottom not in (None, 0) else None
        row.update({
            "cpc": ratio("spend", "clicks"),
            "ctr": ratio("clicks", "impressions"),
            "cpa": ratio("spend", "conversions"),
            "cpl": ratio("spend", "leads"),
            "cost_per_purchase": ratio("spend", "purchases"),
            "roas": None if row.get("value_available") is False else ratio("conversion_value", "spend"),
        })
        if status == "active" and not is_active(row):
            continue
        if status == "inactive" and (current_entity_status(row) is None or is_active(row)):
            continue
        value = row.get("spend")
        if spend == "with_spend" and (value is None or value <= 0):
            continue
        if spend == "without_spend" and (value is None or value != 0):
            continue
        result.append(row)
    return result


def detail_totals(rows: Iterable[dict[str, Any]]) -> dict[str, float | None]:
    rows = list(rows)
    totals: dict[str, float | None] = {}
    for field in BASE_METRICS:
        if field == "conversion_value" and any(row.get("value_available") is False for row in rows):
            totals[field] = None
            continue
        available = [float(row[field]) for row in rows if row.get(field) is not None]
        totals[field] = sum(available) if available else None
    currencies = {str(row["currency"]) for row in rows if row.get("currency")}
    if len(currencies) > 1:
        totals["spend"] = None
        totals["conversion_value"] = None

    def ratio(numerator: str, denominator: str) -> float | None:
        top, bottom = totals[numerator], totals[denominator]
        return top / bottom if top is not None and bottom not in (None, 0) else None

    totals.update({
        "cpc": ratio("spend", "clicks"),
        "ctr": ratio("clicks", "impressions"),
        "cpa": ratio("spend", "conversions"),
        "cpl": ratio("spend", "leads"),
        "cost_per_purchase": ratio("spend", "purchases"),
        "roas": ratio("conversion_value", "spend"),
    })
    return totals


def build_detail_response(
    rows: Iterable[dict[str, Any]],
    *,
    client_id: str,
    platform: str,
    level: DetailLevel,
    status: StatusFilter,
    spend: SpendFilter,
    start_date: str,
    end_date: str,
) -> dict[str, Any]:
    visible = filter_detail_rows(rows, status=status, spend=spend)
    currencies = {str(row["currency"]) for row in visible if row.get("currency")}
    return {
        "client_id": client_id,
        "platform": platform,
        "level": level,
        "status_filter": status,
        "spend_filter": spend,
        "range": {"start": start_date, "end": end_date},
        "count": len(visible),
        "mixed_currency": len(currencies) > 1,
        "rows": visible,
        "totals": detail_totals(visible),
    }

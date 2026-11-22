"""Métricas derivadas comunes a todas las plataformas.

Las métricas derivadas se recalculan SIEMPRE a partir de las base agregadas
(nunca se suman CTR/CPC diarios). ROAS = conversion_value / cost, expresado
como multiplicador (2.44 → "2.44x" en la UI), no como moneda.
"""
from __future__ import annotations

from typing import Any

BASE_FIELDS = ("impressions", "clicks", "spend", "conversions", "conversion_value")


def _div(a: float | None, b: float | None) -> float | None:
    return a / b if a is not None and b not in (None, 0) else None


def derive(m: dict[str, Any]) -> dict[str, Any]:
    out = dict(m)
    out["spend"] = round(float(m.get("spend", 0)), 2)
    raw_conversion_value = m.get("conversion_value", 0)
    out["conversion_value"] = round(float(raw_conversion_value), 2) if raw_conversion_value is not None else None
    out["ctr"] = _div(m.get("clicks", 0), m.get("impressions", 0))
    out["cpc"] = _div(m.get("spend", 0), m.get("clicks", 0))
    cpm = _div(m.get("spend", 0), m.get("impressions", 0))
    out["cpm"] = cpm * 1000 if cpm is not None else None
    out["cpa"] = _div(m.get("spend", 0), m.get("conversions", 0))
    out["roas"] = _div(out["conversion_value"], m.get("spend", 0))
    out["conversion_rate"] = _div(m.get("conversions", 0), m.get("clicks", 0))
    reach = m.get("reach") or 0
    out["reach"] = reach
    if "frequency" not in m or not m.get("frequency"):
        out["frequency"] = _div(m.get("impressions", 0), reach)
    return out


def empty_base(**extra: float) -> dict[str, float]:
    return {"impressions": 0, "clicks": 0, "spend": 0.0, "conversions": 0.0, "conversion_value": 0.0, **extra}


def accumulate(target: dict[str, float], src: dict[str, Any], fields: tuple[str, ...]) -> None:
    for f in fields:
        target[f] = target.get(f, 0) + (src.get(f) or 0)


#: Alias → familia canónica. Cualquier variante visual de una métrica (por
#: plataforma o por nombre comercial) se clasifica bajo la misma familia, así
#: `hidden_metrics: ["roas"]` oculta también "Purchase ROAS", "Blended ROAS",
#: "meta_roas", etc. sin tener que enumerarlas todas. Para ocultar una métrica
#: nueva en el futuro basta con añadir sus variantes aquí.
METRIC_FAMILY_ALIASES: dict[str, str] = {
    "roas": "roas",
    "meta_roas": "roas",
    "google_roas": "roas",
    "gads_roas": "roas",
    "blended_roas": "roas",
    "purchase_roas": "roas",
    "conversion_value": "conversion_value",
    "purchase_value": "conversion_value",
    "purchase_conversion_value": "conversion_value",
    "value": "conversion_value",
    "cpa": "cpa",
    "cost_per_conversion": "cpa",
    "cost_per_purchase": "cpa",
    "cpp": "cpa",
}


def normalize_metric_family(key: str) -> str:
    """Colapsa cualquier variante visual de una métrica a su familia canónica."""
    norm = str(key).strip().lower().replace("-", "_").replace(" ", "_")
    return METRIC_FAMILY_ALIASES.get(norm, norm)

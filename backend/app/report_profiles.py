"""Perfiles XLSX opcionales por cliente.

En Pulpix no hay perfiles hardcodeados de clientes de terceros: los reportes
se generan dinámicamente con la configuración de cada cliente del usuario.
"""
from __future__ import annotations

from dataclasses import dataclass
import unicodedata


@dataclass(frozen=True)
class ReportBlockProfile:
    id: str
    display_name: str
    platforms: tuple[str, ...]
    metrics: tuple[str, ...]
    group_match: tuple[str, ...] = ()
    requires_value_metrics: bool = False
    historical_rows: tuple[int, int] | None = None
    notes: tuple[str, ...] = ()
    primary_kpi: str | None = None


@dataclass(frozen=True)
class ReportProfile:
    client_id: str
    display_name: str
    section: str
    primary_kpi: str
    blocks: tuple[ReportBlockProfile, ...]
    sort_order: int = 100
    meta_result_mode: str | None = None
    uses_woosync: bool = False
    notes: tuple[str, ...] = ()


AD_PLATFORMS = ("google_ads", "meta_ads")
TRAFFIC_PLATFORMS = ("google_ads", "meta_ads", "spotify_ads")
ECOMMERCE_METRICS = (
    "spend", "result", "budget_difference", "expected_spend", "pacing_difference",
    "conversion_value", "ticket_average", "daily_result", "projected_result", "cost_per_result", "roas",
)
REAL_ESTATE_METRICS = (
    "spend", "leads_or_conversions", "budget_difference", "expected_spend", "pacing_difference",
    "daily_result", "projected_result", "cost_per_result", "cpc", "clicks", "impressions", "ctr",
)
CORPORATE_LEAD_METRICS = (
    "spend", "leads_or_conversions", "budget_difference", "expected_spend", "pacing_difference",
    "cost_per_result", "clicks", "cpc", "impressions", "ctr",
)
TRAFFIC_METRICS = (
    "spend", "budget_difference", "expected_spend", "pacing_difference", "clicks", "cpc", "impressions", "ctr",
)

REPORT_PROFILES: dict[str, ReportProfile] = {}


def report_profile(client_id: str) -> ReportProfile | None:
    """Resuelve perfil opcional por client_id canónico."""
    return REPORT_PROFILES.get(client_id)


def block_for_group(profile: ReportProfile, group_name: str | None) -> ReportBlockProfile | None:
    """Asigna bloques solo con grupos reales reconocibles; nunca por campaña/nombre cliente."""
    if group_name is None:
        return profile.blocks[0] if len(profile.blocks) == 1 else None

    def canonical(value: str) -> str:
        decomposed = unicodedata.normalize("NFD", value.strip().casefold())
        return "".join(char for char in decomposed if unicodedata.category(char) != "Mn")

    value = canonical(group_name)
    for block in profile.blocks:
        if block.group_match and any(canonical(token) in value for token in block.group_match):
            return block
    if len(profile.blocks) == 1:
        return profile.blocks[0]
    return None

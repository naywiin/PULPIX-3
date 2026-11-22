"""Perfiles XLSX explícitos por cliente y bloque histórico.

Los perfiles son configuración de presentación; las bases numéricas siguen
siendo las mismas normalizadas por el dashboard.
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


def _profile(client_id: str, name: str, section: str, kpi: str, block: ReportBlockProfile,
             order: int, mode: str | None = "lead", *, woosync: bool = False) -> ReportProfile:
    return ReportProfile(client_id, name, section, kpi, (block,), order, mode, woosync)


REPORT_PROFILES: dict[str, ReportProfile] = {
    "cirque-du-soleil-joya": _profile(
        "cirque-du-soleil-joya", "JOYÀ", "ENTRETENIMIENTO", "cpa",
        ReportBlockProfile("conversion", "JOYÀ", AD_PLATFORMS, ECOMMERCE_METRICS,
                           requires_value_metrics=True, historical_rows=(7, 18),
                           notes=("Meta Purchase usa la configuración validada de Cirque; Google conserva Conversiones.",)),
        10, "purchase"),
    "jungala": _profile(
        "jungala", "Jungala", "ENTRETENIMIENTO", "cpa",
        ReportBlockProfile("conversion", "Jungala", AD_PLATFORMS, ECOMMERCE_METRICS,
                           requires_value_metrics=True, historical_rows=(19, 28),
                           notes=("Meta usa omni_purchase; Google conserva Conversiones.",)), 20, "purchase"),
    "blumia": ReportProfile(
        "blumia", "Blumia", "ENTRETENIMIENTO", "cpa",
        (
            ReportBlockProfile("conversion", "BLUMIA - Conversión", AD_PLATFORMS, ECOMMERCE_METRICS,
                               ("conversion", "conversión"), True, (55, 63)),
            ReportBlockProfile("traffic", "BLUMIA - Tráfico", TRAFFIC_PLATFORMS,
                               ("spend", "budget_difference", "expected_spend", "pacing_difference", "clicks",
                                "impressions", "reach", "ctr", "frequency", "cpc"),
                               ("traffic", "tráfico"), historical_rows=(64, 69),
                               notes=("Clics al sitio web no está en el esquema normalizado actual; se usa Clics.",), primary_kpi="cpc"),
        ), 30, "purchase"),
    "doc-towers": _profile(
        "doc-towers", "Doc Towers", "REAL ESTATE", "cpl",
        ReportBlockProfile("leads", "Doc Towers", AD_PLATFORMS, REAL_ESTATE_METRICS, historical_rows=(94, 101)), 40),
    "bambino": _profile(
        "bambino", "Bambino", "REAL ESTATE", "cpl",
        ReportBlockProfile("leads", "Bambino", AD_PLATFORMS, REAL_ESTATE_METRICS, historical_rows=(104, 112)), 45),
    "santuario-residencial": _profile(
        "santuario-residencial", "Santuario", "REAL ESTATE", "cpl",
        ReportBlockProfile("leads", "Santuario", AD_PLATFORMS, REAL_ESTATE_METRICS, historical_rows=(104, 112),
                           notes=("El histórico combina Santuario y Bambino; este perfil usa únicamente el client_id Santuario.",)), 46),
    "dorado-center": _profile(
        "dorado-center", "Dorado Center", "REAL ESTATE", "cpl",
        ReportBlockProfile("leads", "Dorado Center", AD_PLATFORMS, REAL_ESTATE_METRICS, historical_rows=(116, 122)), 47),
    "maternalia": _profile(
        "maternalia", "Maternalia", "REAL ESTATE", "cpl",
        ReportBlockProfile("leads", "Maternalia", AD_PLATFORMS, REAL_ESTATE_METRICS, historical_rows=(146, 152)), 49),
    # Registro del dashboard confirmado: client_id iupi tiene nombre visible
    # Torre Aurelio y coincide exactamente con el bloque histórico (filas 102–108).
    "iupi": _profile(
        "iupi", "Torre Aurelio", "REAL ESTATE", "cpl",
        ReportBlockProfile("leads", "Torre Aurelio", AD_PLATFORMS, REAL_ESTATE_METRICS,
                           historical_rows=(102, 108)), 48),
    "clarvi": ReportProfile(
        "clarvi", "Clarvi", "B2B / CORPORATIVO", "cpl",
        (
            ReportBlockProfile("leads", "CLARVI LEADS", AD_PLATFORMS, CORPORATE_LEAD_METRICS,
                               ("lead", "leads", "conversion", "conversión"), historical_rows=(159, 165)),
            ReportBlockProfile("traffic", "CLARVI TRÁFICO", TRAFFIC_PLATFORMS, TRAFFIC_METRICS,
                               ("traffic", "tráfico", "linkedin"), historical_rows=(167, 170),
                               notes=("El grupo Tráfico usa únicamente las plataformas activas en el snapshot mensual; LinkedIn no tiene provider en el dashboard actual.",), primary_kpi="cpc"),
            ReportBlockProfile("ecommerce", "CLARVI ECOMMERCE", AD_PLATFORMS,
                               ("spend", "leads_or_conversions", "budget_difference", "expected_spend",
                                "pacing_difference", "cost_per_result", "cpm", "cpc", "clicks", "impressions", "ctr"),
                               ("ecommerce", "e-commerce", "commerce", "ventas"), historical_rows=(172, 178),
                               notes=("Las conversiones se presentan como leads hasta que haya una fuente de ventas atribuibles.",)),
        ), 60, "lead"),
    "grupo-regio": _profile(
        "grupo-regio", "Grupo Regio", "B2B / CORPORATIVO", "ctr",
        ReportBlockProfile("performance", "Grupo Regio", AD_PLATFORMS, REAL_ESTATE_METRICS, historical_rows=(181, 185)), 70),
    "sign-factory": _profile(
        "sign-factory", "Sign Factory", "B2B / CORPORATIVO", "ctr",
        ReportBlockProfile("performance", "Sign Factory", AD_PLATFORMS, REAL_ESTATE_METRICS, historical_rows=(189, 193)), 80),
    "tachuela": _profile(
        "tachuela", "Tachuela", "B2B / CORPORATIVO", "ctr",
        ReportBlockProfile("performance", "Tachuela", AD_PLATFORMS, REAL_ESTATE_METRICS, historical_rows=(197, 201)), 90),
    "ssiento": ReportProfile(
        "ssiento", "Ssiento", "ECOMMERCE", "roas",
        (ReportBlockProfile("performance", "SSIENTO - Performance", AD_PLATFORMS,
                            ("spend", "result", "budget_difference", "expected_spend", "pacing_difference",
                             "conversion_value", "daily_result", "projected_result", "cost_per_result", "roas"),
                            requires_value_metrics=True, historical_rows=(209, 224)),),
        50, "purchase", True),
}


def report_profile(client_id: str) -> ReportProfile | None:
    """Resuelve perfil únicamente por client_id canónico."""
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

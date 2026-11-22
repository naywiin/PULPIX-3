"""Exportador XLSX construido exclusivamente sobre la capa de datos del dashboard."""
from __future__ import annotations

import calendar
from copy import copy
from dataclasses import dataclass
from datetime import date
from io import BytesIO
from math import isclose, isfinite
from typing import Any, Iterable

from openpyxl import Workbook, load_workbook
from openpyxl.formatting.rule import FormulaRule
from openpyxl.styles import Alignment, Border, Font, PatternFill, Protection, Side
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.datavalidation import DataValidation

from .budgets import BudgetRepository, MonthlyBudget, REPORT_PLATFORMS
from .models import Client, account_ids, exclusive_claims
from .metrics import derive, normalize_metric_family
from .reporting_groups import ReportingGroup, enabled_platforms_for_group
from .report_profiles import REPORT_PROFILES, ReportBlockProfile, ReportProfile, block_for_group, report_profile
from .report_order import CATEGORY_ORDER, client_report_category, client_report_sort_key

PLATFORM_LABELS = {
    "google_ads": "Google Ads",
    "meta_ads": "Meta Ads",
    "spotify_ads": "Spotify Ads",
    "pinterest_ads": "Pinterest Ads",
}
MONTH_NAMES = (
    "Enero", "Febrero", "Marzo", "Abril", "Mayo", "Junio",
    "Julio", "Agosto", "Septiembre", "Octubre", "Noviembre", "Diciembre",
)

# Desviación absoluta del pacing respecto al gasto esperado.
PACING_GREEN_MAX = 0.10
PACING_YELLOW_MAX = 0.20

MONEY_FORMAT = '$#,##0.00'
NUMBER_FORMAT = '#,##0.00'
INTEGER_FORMAT = '#,##0'
PERCENT_FORMAT = '0.00%'
RATIO_FORMAT = '0.00x'

# Una definición matemática alimenta tanto el resumen JSON como las fórmulas
# editables del XLSX. Mantener el AST como fuente única evita divergencias.
KPI_EXPRESSIONS: dict[str, Any] = {
    "budget_period": ("div", ("mul", "monthly_budget", "applicable_days"), "days_in_month"),
    "budget_difference": ("sub", "monthly_budget", "spend"),
    "expected_spend": ("div", ("mul", "monthly_budget", "applicable_days"), "days_in_month"),
    "pacing_difference": ("sub", "expected_spend", "spend"),
    "budget_consumed": ("div", "spend", "monthly_budget"),
    "daily_conversions": ("div", "conversions", "observed_days"),
    "projected_conversions": ("mul", "daily_conversions", "days_in_month"),
    "cpa": ("div", "spend", "conversions"),
    "cpl": ("div", "spend", "leads"),
    "roas": ("div", "conversion_value", "spend"),
    "ticket_average": ("div", "conversion_value", "conversions"),
    "cpc": ("div", "spend", "clicks"),
    "ctr": ("div", "clicks", "impressions"),
    "frequency": ("div", "impressions", "reach"),
}


def _expression_leaves(expression: Any) -> set[str]:
    if isinstance(expression, str):
        nested = KPI_EXPRESSIONS.get(expression)
        return _expression_leaves(nested) if nested is not None else {expression}
    if isinstance(expression, tuple):
        return set().union(*(_expression_leaves(item) for item in expression[1:]))
    return set()


def _evaluate_expression(expression: Any, values: dict[str, float | None]) -> float | None:
    if isinstance(expression, str):
        if expression in values:
            value = values[expression]
            return float(value) if value is not None and isfinite(float(value)) else None
        nested = KPI_EXPRESSIONS.get(expression)
        return _evaluate_expression(nested, values) if nested is not None else None
    operator, *args = expression
    evaluated = [_evaluate_expression(arg, values) for arg in args]
    if any(value is None for value in evaluated):
        return None
    left = evaluated[0]
    right = evaluated[1]
    if operator == "add":
        return left + right
    if operator == "sub":
        return left - right
    if operator == "mul":
        return left * right
    if operator == "div":
        return None if right == 0 else left / right
    raise ValueError(f"Operador KPI desconocido: {operator}")


def calculate_performance_kpis(
    bases: dict[str, float | None],
    *,
    monthly_budget: float | None,
    applicable_days: int,
    days_in_month: int,
    observed_days: int | None = None,
    budget_period: float | None = None,
    expected_spend: float | None = None,
) -> dict[str, float | None]:
    """Calcula KPI con divisor seguro; override permite agregar meses ya prorrateados."""
    values: dict[str, float | None] = {
        **bases,
        "monthly_budget": monthly_budget,
        "applicable_days": float(applicable_days),
        "days_in_month": float(days_in_month),
        "observed_days": float(observed_days if observed_days is not None else applicable_days),
    }
    if budget_period is not None:
        values["budget_period"] = budget_period
    if expected_spend is not None:
        values["expected_spend"] = expected_spend
    results: dict[str, float | None] = {}
    overrides = {"budget_period": budget_period, "expected_spend": expected_spend}
    for key, expression in KPI_EXPRESSIONS.items():
        if key in overrides and overrides[key] is not None:
            results[key] = float(overrides[key])
        else:
            results[key] = _evaluate_expression(expression, {**values, **results})
    return results


def pacing_status(kpis: dict[str, float | None]) -> str:
    """Clasifica la desviación con los mismos umbrales publicados al dashboard."""
    if kpis.get("budget_period") is None or kpis.get("expected_spend") is None:
        return "missing_budget"
    expected, difference = kpis["expected_spend"], kpis.get("pacing_difference")
    if expected is None or difference is None or expected == 0:
        return "unavailable"
    deviation = abs(difference / expected)
    if deviation <= PACING_GREEN_MAX:
        return "green"
    if deviation <= PACING_YELLOW_MAX:
        return "yellow"
    return "red"


PACING_THRESHOLDS = {"green_max": PACING_GREEN_MAX, "yellow_max": PACING_YELLOW_MAX}


class ReportValidationError(Exception):
    """El archivo no se genera cuando su base no cumple el contrato."""


@dataclass(frozen=True)
class MonthSlice:
    start: date
    end: date
    year: int
    month: int
    days_in_month: int
    applicable_days: int

    @property
    def label(self) -> str:
        return f"{MONTH_NAMES[self.month - 1]} {self.year}"


@dataclass
class ReportLine:
    period: MonthSlice
    client_id: str
    client_name: str
    platform: str
    account_ids: list[str]
    currency: str
    status: str
    spend: float | None
    conversions: float | None
    leads: float | None
    conversion_value: float | None
    clicks: float | None
    impressions: float | None
    reach: float | None
    reach_is_sum: bool
    reporting_group_id: str | None = None
    reporting_group_name: str | None = None
    reporting_group_type: str | None = None
    grouping_enabled: bool = False
    hidden_metrics: tuple[str, ...] = ()
    purchases: float | None = None
    checkouts: float | None = None


@dataclass
class DataLine:
    period: MonthSlice
    client_id: str
    client_name: str
    platform: str
    account_id: str
    account_name: str | None
    level: str
    campaign_id: str | None
    campaign_name: str | None
    objective: str | None
    spend: float | None
    conversions: float | None
    leads: float | None
    conversion_value: float | None
    clicks: float | None
    impressions: float | None
    reach: float | None
    currency: str
    status: str
    reporting_group_id: str | None = None
    reporting_group_name: str | None = None
    reporting_group_type: str | None = None
    grouping_enabled: bool = False
    configured_group_id: str | None = None
    checkouts: float | None = None
    configured_group_name: str | None = None
    configured_group_type: str | None = None
    configured_group_active: bool | None = None
    configured_group_enabled_platforms: tuple[str, ...] = ()
    purchases: float | None = None


@dataclass(frozen=True)
class GeneratedReport:
    content: bytes
    filename: str


def month_slices(start: date, end: date) -> list[MonthSlice]:
    if start > end:
        raise ValueError("Desde debe ser anterior o igual a Hasta")
    slices: list[MonthSlice] = []
    year, month = start.year, start.month
    while (year, month) <= (end.year, end.month):
        total_days = calendar.monthrange(year, month)[1]
        month_start = date(year, month, 1)
        month_end = date(year, month, total_days)
        clipped_start, clipped_end = max(start, month_start), min(end, month_end)
        slices.append(MonthSlice(
            start=clipped_start,
            end=clipped_end,
            year=year,
            month=month,
            days_in_month=total_days,
            applicable_days=(clipped_end - clipped_start).days + 1,
        ))
        if month == 12:
            year, month = year + 1, 1
        else:
            month += 1
    return slices


def observed_days_in_period(period: MonthSlice, as_of: date | None = None) -> int:
    """Count only selected dates through today; future dates cannot dilute pacing."""
    last_observed = min(period.end, as_of or date.today())
    return max(0, (last_observed - period.start).days + 1)


def _campaign_leads(campaigns: list[dict[str, Any]]) -> float | None:
    values = [_metric(row, "leads") for row in campaigns]
    return sum(values) if values and all(value is not None for value in values) else None


def _metric(value: dict[str, Any], key: str, *, available: bool = True) -> float | None:
    """Conserva ceros reales, pero no convierte una métrica inexistente en cero."""
    if not available or key not in value or value[key] is None:
        return None
    try:
        number = float(value[key])
    except (TypeError, ValueError):
        return None
    return number if isfinite(number) else None


def collect_report_lines(
    service,
    clients: Iterable[Client],
    start: date,
    end: date,
    owners: dict,
    refresh: bool = False,
    reporting_groups: list[ReportingGroup] | None = None,
    reporting_groups_by_client: dict[str, list[ReportingGroup]] | None = None,
    reporting_groups_by_client_month: dict[tuple[str, int, int], list[ReportingGroup]] | None = None,
) -> tuple[list[ReportLine], list[DataLine]]:
    """Usa exactamente `DashboardService.platform_block`, sin proveedores paralelos."""
    report: list[ReportLine] = []
    data: list[DataLine] = []
    any_grouping = False
    for client in [item for item in clients if item.active]:
        for period in month_slices(start, end):
            configured_groups = (reporting_groups_by_client_month or {}).get(
                (client.id, period.year, period.month),
                (reporting_groups_by_client or {}).get(client.id, reporting_groups or []),
            )
            active_groups = [group for group in configured_groups if group.active]
            grouping_enabled = bool(configured_groups)
            any_grouping = any_grouping or grouping_enabled
            assignment_map = {}
            configured_assignment_map = {}
            group_platforms = {group.id: enabled_platforms_for_group(group) for group in configured_groups}
            for group in configured_groups:
                for assignment in group.assignments:
                    if assignment.key in configured_assignment_map:
                        raise ReportValidationError(
                            f"La campaña {assignment.campaign_id} está configurada en más de un grupo."
                        )
                    configured_assignment_map[assignment.key] = group
            for group in active_groups:
                for assignment in group.assignments:
                    if group_platforms[group.id].get(assignment.platform, False):
                        assignment_map[assignment.key] = group
            rng = (period.start, period.end)
            for platform in (*REPORT_PLATFORMS, "pinterest_ads"):
                allowed_accounts = account_ids(client, platform)
                if not allowed_accounts:
                    continue
                block = service.platform_block(client, platform, rng, None, refresh, owners)
                current = block.get("current") if block.get("status") == "ok" else None
                totals = current.get("totals") if isinstance(current, dict) else None
                campaigns = current.get("campaigns") or [] if isinstance(current, dict) else []
                leads = _campaign_leads(campaigns) if platform in ("google_ads", "meta_ads") else None
                status = str(block.get("status") or "error")
                currency = str(block.get("currency") or client.currency)
                supports_conversions = platform in ("google_ads", "meta_ads")
                supports_value = supports_conversions or platform == "pinterest_ads"
                supports_reach = platform in ("meta_ads", "spotify_ads")
                line = ReportLine(
                    period=period,
                    client_id=client.id,
                    client_name=client.name,
                    platform=platform,
                    account_ids=list(allowed_accounts),
                    currency=currency,
                    status=status,
                    spend=_metric(totals, "spend") if totals else None,
                    conversions=_metric(totals, "conversions", available=supports_conversions) if totals else None,
                    leads=leads,
                    purchases=_metric(totals, "purchases", available=platform == "meta_ads") if totals else None,
                    conversion_value=_metric(totals, "conversion_value", available=supports_value) if totals else None,
                    clicks=_metric(totals, "clicks") if totals else None,
                    impressions=_metric(totals, "impressions") if totals else None,
                    reach=_metric(totals, "reach", available=supports_reach) if totals else None,
                    reach_is_sum=bool(current.get("reach_is_sum")) if current else False,
                    hidden_metrics=tuple(client.hidden_metrics or []),
                )
                if platform != "pinterest_ads":
                    report.append(line)
                data.append(DataLine(
                    period=period,
                    client_id=client.id,
                    client_name=client.name,
                    platform=platform,
                    account_id=",".join(allowed_accounts),
                    account_name=None,
                    level="TOTAL PLATAFORMA",
                    campaign_id=None,
                    campaign_name=None,
                    objective=None,
                    spend=line.spend,
                    conversions=line.conversions,
                    purchases=line.purchases,
                    leads=line.leads,
                    conversion_value=line.conversion_value,
                    clicks=line.clicks,
                    impressions=line.impressions,
                    reach=line.reach,
                    currency=currency,
                    status=status,
                    checkouts=_metric(totals, "checkouts") if totals and platform == "pinterest_ads" else None,
                ))
                for campaign in campaigns:
                    source_id = str(
                        campaign.get("source_account_id")
                        or campaign.get("ad_account_id")
                        or block.get("account_id")
                        or allowed_accounts[0]
                    )
                    if source_id not in allowed_accounts:
                        raise ReportValidationError(
                            f"La cuenta {source_id} no está asignada a {client.id} en {platform}."
                        )
                    objective = str(campaign.get("objective") or "") or None
                    is_lead = bool(objective and "LEAD" in objective.upper())
                    campaign_key = (platform, source_id, str(campaign.get("campaign_id") or ""))
                    group = assignment_map.get(campaign_key)
                    configured_group = configured_assignment_map.get(campaign_key)
                    data.append(DataLine(
                        period=period,
                        client_id=client.id,
                        client_name=client.name,
                        platform=platform,
                        account_id=source_id,
                        account_name=campaign.get("source_account_name"),
                        level="CAMPAÑA",
                        campaign_id=str(campaign.get("campaign_id") or "") or None,
                        campaign_name=campaign.get("campaign_name"),
                        objective=objective,
                        spend=_metric(campaign, "spend"),
                        conversions=_metric(campaign, "conversions", available=supports_conversions),
                        purchases=_metric(campaign, "purchases", available=platform == "meta_ads"),
                        leads=_metric(campaign, "leads", available=platform in ("google_ads", "meta_ads")),
                        conversion_value=_metric(campaign, "conversion_value", available=supports_value),
                        clicks=_metric(campaign, "clicks"),
                        impressions=_metric(campaign, "impressions"),
                        reach=_metric(campaign, "reach", available=supports_reach),
                        currency=currency,
                        status=status,
                        checkouts=_metric(campaign, "checkouts") if platform == "pinterest_ads" else None,
                        reporting_group_id=group.id if group else None,
                        reporting_group_name=group.name if group else None,
                        reporting_group_type=group.type if group else None,
                        grouping_enabled=grouping_enabled,
                        configured_group_id=configured_group.id if configured_group else None,
                        configured_group_name=configured_group.name if configured_group else None,
                        configured_group_type=configured_group.type if configured_group else None,
                        configured_group_active=configured_group.active if configured_group else None,
                        configured_group_enabled_platforms=tuple(
                            platform for platform, enabled in group_platforms.get(configured_group.id, {}).items() if enabled
                        ) if configured_group else (),
                    ))
    if any_grouping:
        # La salida agrupada se construye SOLO desde campañas ya filtradas por
        # DashboardService y sus asignaciones shared/exclusive existentes.
        seen_campaigns: set[tuple[str, str, int, int, str, str]] = set()
        for row in data:
            if row.level != "CAMPAÑA" or not row.campaign_id:
                continue
            key = (row.client_id, row.platform, row.period.year, row.period.month, row.account_id, row.campaign_id)
            if key in seen_campaigns:
                raise ReportValidationError(f"La campaña {row.campaign_id} aparece más de una vez en la base agrupada.")
            seen_campaigns.add(key)
        platform_lines = list(report)
        grouped_rows: list[ReportLine] = []
        for client in [item for item in clients if item.active]:
            for period in month_slices(start, end):
                original_lines = [line for line in platform_lines if line.client_id == client.id and line.period == period]
                if not any(line.client_id == client.id and line.period == period and line.grouping_enabled for line in data):
                    grouped_rows.extend(original_lines)
                    continue
                profile = report_profile(client.id)
                # A single-block profile has no group-specific layout. Keep the
                # normalized DashboardService platform totals intact instead of
                # rebuilding purchases/clicks from campaign rows that may have
                # nulls for campaigns without that event.
                if profile and len(profile.blocks) == 1:
                    grouped_rows.extend(original_lines)
                    continue
                configured_groups = (reporting_groups_by_client_month or {}).get(
                    (client.id, period.year, period.month),
                    (reporting_groups_by_client or {}).get(client.id, reporting_groups or []),
                )
                for platform in REPORT_PLATFORMS:
                    campaigns = [row for row in data if row.client_id == client.id and row.period == period and row.platform == platform and row.level == "CAMPAÑA"]
                    group_keys = {row.reporting_group_id for row in campaigns}
                    for group in configured_groups:
                        if group.active and enabled_platforms_for_group(group).get(platform, False) and account_ids(client, platform):
                            group_keys.add(group.id)
                    group_keys = sorted(group_keys, key=lambda value: value or "")
                    for group_id in group_keys:
                        rows = [row for row in campaigns if row.reporting_group_id == group_id]
                        source_line = next((line for line in platform_lines if line.client_id == client.id and line.period == period and line.platform == platform), None)
                        group = next((item for item in configured_groups if item.id == group_id), None) if group_id else None
                        if not rows:
                            # A platform enabled for a group remains structurally
                            # visible even when it has no campaign metrics. Nulls
                            # stay null; assignments and snapshots are untouched.
                            if group is None or source_line is None:
                                continue
                            grouped_rows.append(ReportLine(
                                period=period, client_id=client.id, client_name=client.name,
                                platform=platform, account_ids=list(account_ids(client, platform)),
                                currency=source_line.currency, status=source_line.status,
                                spend=None, conversions=None, leads=None, purchases=None,
                                conversion_value=None, clicks=None, impressions=None, reach=None,
                                reach_is_sum=False, reporting_group_id=group.id,
                                reporting_group_name=group.name, reporting_group_type=group.type,
                                grouping_enabled=True, hidden_metrics=tuple(client.hidden_metrics or []),
                            ))
                            continue
                        currencies = {row.currency for row in rows}
                        currency = next(iter(currencies)) if len(currencies) == 1 else ""
                        def val(key):
                            if len(currencies) != 1:
                                return None
                            values = [getattr(row, key) for row in rows]
                            if key == "leads":
                                # Campaign rows can omit the lead action when
                                # that campaign reported none. Match the
                                # dashboard's aggregate semantics: sum measured
                                # values, and preserve null only when none were
                                # reported. Never fabricate a zero row.
                                observed = [float(value) for value in values if value is not None]
                                return sum(observed) if observed else None
                            return None if any(value is None for value in values) else sum(float(value) for value in values)
                        group_name = group.name if group else next((row.reporting_group_name for row in rows if row.reporting_group_name), None)
                        group_type = group.type if group else next((row.reporting_group_type for row in rows if row.reporting_group_type), None)
                        grouped_rows.append(ReportLine(
                            period=period, client_id=client.id, client_name=client.name,
                            platform=platform, account_ids=sorted({row.account_id for row in rows}),
                            currency=currency, status="ok" if all(row.status == "ok" for row in rows) else "partial",
                            spend=val("spend"), conversions=val("conversions"), leads=val("leads"),
                            purchases=val("purchases") if platform == "meta_ads" else None,
                            conversion_value=val("conversion_value"), clicks=val("clicks"),
                            impressions=val("impressions"),
                            # No afirmar alcance/frecuencia deduplicados al unir campañas.
                            reach=rows[0].reach if len(rows) == 1 and source_line is not None and not source_line.reach_is_sum else None,
                            reach_is_sum=len(rows) != 1 or bool(source_line and source_line.reach_is_sum),
                            reporting_group_id=group_id,
                            reporting_group_name=group_name,
                            reporting_group_type=group_type,
                            grouping_enabled=True,
                            hidden_metrics=tuple(client.hidden_metrics or []),
                        ))
        report = grouped_rows
    return report, data


BASE_METRICS = (
    "spend", "conversions", "purchases", "leads", "conversion_value", "clicks", "impressions", "reach",
)


def _effective_budget(line: ReportLine, budgets: dict, data_lines: list[DataLine]) -> MonthlyBudget | None:
    """Resuelve un solo presupuesto efectivo: límite del grupo o suma de campañas.

    Un límite explícito del grupo siempre prevalece; las asignaciones de campaña
    quedan como desglose y nunca se suman encima del límite.
    """
    key = _budget_key(line.client_id, line.platform, line.period.year, line.period.month, line.reporting_group_id)
    explicit = budgets.get(key)
    if explicit is not None:
        return explicit
    campaign_rows = [row for row in data_lines if row.level == "CAMPAÑA"
                     and row.client_id == line.client_id and row.platform == line.platform
                     and row.period.year == line.period.year and row.period.month == line.period.month
                     and row.reporting_group_id == line.reporting_group_id and row.campaign_id]
    allocations = [budgets.get((line.client_id, line.platform, line.period.year, line.period.month, None,
                                row.account_id, row.campaign_id)) for row in campaign_rows]
    present = [item for item in allocations if item is not None]
    if not present or len({item.currency for item in present}) != 1:
        return None
    if any(item.currency != line.currency for item in present):
        return None
    return MonthlyBudget(
        client_id=line.client_id, platform=line.platform, year=line.period.year, month=line.period.month,
        budget=sum(item.budget for item in present), currency=line.currency,
    )


def _sum_base(lines: list[ReportLine], key: str) -> float | None:
    values = [getattr(line, key) for line in lines]
    if key == "leads":
        available = [value for value in values if value is not None]
        return sum(float(value) for value in available) if available else None
    if not values or any(value is None for value in values):
        return None
    return sum(float(value) for value in values)


def _summary_record(
    lines: list[ReportLine],
    budgets: dict[tuple[str, str, int, int, str | None], MonthlyBudget],
    *,
    level: str,
    period: MonthSlice | None = None,
    data_lines: list[DataLine] | None = None,
) -> dict[str, Any]:
    if not lines:
        raise ValueError("No hay líneas para resumir")
    # The combined Overview metrics use the same client-specific event and
    # value semantics as the platform views. Keep the source rows intact so
    # missing Meta leads cannot erase valid Google conversions.
    profile = report_profile(lines[0].client_id)
    event_lines = [line for line in lines if line.platform in ("google_ads", "meta_ads")]
    event_values: list[tuple[ReportLine, float]] = []
    for line in event_lines:
        value = line.conversions
        if line.platform == "meta_ads" and profile and profile.meta_result_mode == "purchase":
            value = line.purchases
        elif line.platform == "meta_ads" and profile and profile.meta_result_mode == "lead":
            value = line.leads
        if value is not None:
            event_values.append((line, float(value)))
    total_events = sum(value for _, value in event_values) if event_values else None
    total_events_partial = bool(event_values) and len(event_values) < len(event_lines)
    total_event_sources = sorted({line.platform for line, _ in event_values})

    # Meta conversion value is purchase value. It is not a valid lead value
    # for lead-mode clients, even when the provider returns it.
    value_candidate_lines = [line for line in lines if line.platform in ("google_ads", "meta_ads")]
    value_lines = [line for line in lines if line.platform == "google_ads" or (
        line.platform == "meta_ads" and profile and profile.meta_result_mode == "purchase"
        and line.purchases is not None
    )]
    valid_value_lines = [line for line in value_lines if line.conversion_value is not None]
    value_currencies = {line.currency for line in valid_value_lines}
    value_partial = bool(valid_value_lines) and (
        len(valid_value_lines) < len(value_candidate_lines)
        or len(valid_value_lines) < len(value_lines)
    )
    conversion_value = (
        sum(float(line.conversion_value) for line in valid_value_lines)
        if valid_value_lines and len(value_currencies) == 1 else None
    )
    value_sources = sorted({line.platform for line in valid_value_lines})
    value_spend_lines = [line for line in valid_value_lines if line.spend is not None]
    value_spend_currencies = {line.currency for line in value_spend_lines}
    value_spend = (
        sum(float(line.spend) for line in value_spend_lines)
        if value_spend_lines and value_spend_currencies == value_currencies and len(value_currencies) == 1
        else None
    )

    currencies = {line.currency for line in lines}
    currency = next(iter(currencies)) if len(currencies) == 1 else None
    bases = {key: _sum_base(lines, key) for key in BASE_METRICS}
    bases["conversion_value"] = conversion_value
    # The total KPI is semantic (Google conversions + configured Meta result),
    # not the sum of a generic conversions field that may contain purchases.
    bases["conversions"] = total_events
    if currency is None:
        # No convertir ni sumar importes de monedas distintas.
        bases["spend"] = None
        bases["conversion_value"] = None
    # Alcance acumulado entre meses o medios no equivale a personas únicas.
    reach_is_aggregatable = len(lines) == 1 and not lines[0].reach_is_sum
    if not reach_is_aggregatable:
        bases["reach"] = None

    total_events_available = total_events is not None
    event_currency_set = {line.currency for line, _ in event_values}
    complete_event_lines = [line for line, _ in event_values if line.spend is not None]
    event_spend_currencies = {line.currency for line in complete_event_lines}
    total_event_spend = (
        sum(float(line.spend) for line in complete_event_lines)
        if complete_event_lines and len(event_spend_currencies) == 1
        and len(event_currency_set) == 1 and event_spend_currencies == event_currency_set
        else None
    )
    # CPA numerator and denominator include exactly the sources with both
    # spend and their client-semantic result. A missing source makes it partial.
    cpa_lines = complete_event_lines
    cpa_events = sum(value for line, value in event_values if line in cpa_lines)
    cpa_currency_set = {line.currency for line in cpa_lines}
    total_cpa = (
        sum(float(line.spend) for line in cpa_lines) / cpa_events
        if cpa_lines and len(cpa_currency_set) == 1 and cpa_events != 0 else None
    )
    total_cpa_partial = bool(cpa_lines) and (len(cpa_lines) < len(event_lines) or total_events_partial)
    total_cpa_sources = sorted({line.platform for line in cpa_lines})
    roas = conversion_value / value_spend if value_spend not in (None, 0) and conversion_value is not None else None
    roas_partial = bool(value_sources) and (
        len(value_sources) < len({line.platform for line in value_candidate_lines})
        or len(value_sources) < len({line.platform for line in value_lines})
    )
    all_paid = [line for line in lines if line.platform in ("google_ads", "meta_ads", "spotify_ads")]
    paid_currency_set = {line.currency for line in all_paid}
    paid_spend_values = [line.spend for line in all_paid]
    paid_click_values = [line.clicks for line in all_paid]
    total_cpc = (
        sum(float(value) for value in paid_spend_values) / sum(float(value) for value in paid_click_values)
        if len(paid_currency_set) == 1 and paid_spend_values and paid_click_values
        and all(value is not None for value in paid_spend_values)
        and all(value is not None for value in paid_click_values)
        and sum(float(value) for value in paid_click_values) != 0 else None
    )

    # A group budget is a single cap. Resolve it once per platform/month/group,
    # even when the summary contains several campaign rows from that group.
    budget_scope_lines = {
        (line.client_id, line.platform, line.period.year, line.period.month, line.reporting_group_id): line
        for line in lines
    }
    all_budget_scope_items = [
        (line, _effective_budget(line, budgets, data_lines or []))
        for line in budget_scope_lines.values()
    ]
    # Grouped reporting can coexist with ungrouped campaigns. A campaign with
    # no allocation must not erase captured group caps from pacing. Resolve one
    # effective amount per reporting scope, where a group cap already takes
    # precedence over allocations inside that group.
    has_group_budget = any(
        line.reporting_group_id is not None
        and budgets.get(_budget_key(line.client_id, line.platform, line.period.year, line.period.month, line.reporting_group_id)) is not None
        for line, _ in all_budget_scope_items
    )
    if has_group_budget:
        budget_scope_items = [(line, item) for line, item in all_budget_scope_items if line.reporting_group_id is not None]
    else:
        budget_scope_items = all_budget_scope_items
    captured_items = [(line, item) for line, item in budget_scope_items if item is not None]
    items = [item for _, item in captured_items]
    budget_captured = bool(items)
    budget_complete = bool(budget_scope_items) and len(captured_items) == len(budget_scope_items)
    budget_partial = budget_captured and not budget_complete
    item_currencies = {item.currency for item in items}
    budget_currency_compatible = bool(items) and len(item_currencies) == 1 and all(
        item.currency == line.currency for line, item in captured_items
    )
    budget_usable = budget_captured and budget_currency_compatible
    monthly_budget = sum(item.budget for item in items) if budget_usable else None
    budget_period_parts = []
    for line, item in captured_items:
        budget_period_parts.append(item.budget / line.period.days_in_month * line.period.applicable_days)
    budget_period = sum(budget_period_parts) if budget_usable else None
    explicit_scope_count = sum(
        1 for line, item in captured_items
        if budgets.get(_budget_key(line.client_id, line.platform, line.period.year, line.period.month, line.reporting_group_id)) is not None
    )
    budget_source = (
        "unavailable" if not budget_captured else
        "group" if has_group_budget and explicit_scope_count == len(captured_items) else
        "mixed" if has_group_budget else
        "campaign"
    )
    unique_periods = {
        (line.period.year, line.period.month): line.period for line in lines
    }
    applicable_days = period.applicable_days if period else sum(
        item.applicable_days for item in unique_periods.values()
    )
    days_in_month = period.days_in_month if period else max(
        item.days_in_month for item in unique_periods.values()
    )
    single_month = len({(line.period.year, line.period.month) for line in lines}) == 1
    observed_days = sum(observed_days_in_period(item) for item in unique_periods.values())
    kpis = calculate_performance_kpis(
        bases,
        monthly_budget=monthly_budget,
        applicable_days=applicable_days,
        days_in_month=days_in_month,
        observed_days=observed_days,
        budget_period=budget_period,
        expected_spend=budget_period,
    )
    kpis["cost_per_purchase"] = (
        bases["spend"] / bases["purchases"]
        if bases["spend"] is not None and bases["purchases"] not in (None, 0) else None
    )
    # Para un total multi-mes no existe un único mes/días; proyección aplica al
    # cierre de cada mes y se presenta únicamente en el detalle mensual.
    if not single_month:
        kpis["projected_conversions"] = None
        kpis["projected_sales_value"] = None
    else:
        observed_days = observed_days_in_period(period) if period else observed_days
        kpis["projected_sales_value"] = (
            conversion_value / observed_days * days_in_month
            if conversion_value is not None and observed_days > 0
            else None
        )
    if not reach_is_aggregatable:
        kpis["frequency"] = None
    if not budget_currency_compatible:
        kpis = {**kpis, **calculate_performance_kpis(
            bases, monthly_budget=None, applicable_days=applicable_days,
            days_in_month=days_in_month, observed_days=observed_days,
        )}
        if not single_month:
            kpis["projected_conversions"] = None
        if not reach_is_aggregatable:
            kpis["frequency"] = None
    # These client-semantic totals remain valid even when pacing budget
    # currencies differ; their own source-compatibility checks govern them.
    kpis["roas"] = roas
    kpis["cpa"] = total_cpa
    names = {line.client_name for line in lines}
    platforms = {line.platform for line in lines}
    return {
        "level": level,
        "reporting_group_id": lines[0].reporting_group_id if len({line.reporting_group_id for line in lines}) == 1 else None,
        "reporting_group_name": lines[0].reporting_group_name if len({line.reporting_group_id for line in lines}) == 1 else None,
        "reporting_group_type": lines[0].reporting_group_type if len({line.reporting_group_id for line in lines}) == 1 else None,
        "client_id": lines[0].client_id,
        "client_name": next(iter(names)) if len(names) == 1 else None,
        "platform": next(iter(platforms)) if len(platforms) == 1 else None,
        "platforms": sorted(platforms),
        "month": period.month if period else None,
        "year": period.year if period else None,
        "month_label": period.label if period else None,
        "from": min(line.period.start for line in lines).isoformat(),
        "to": max(line.period.end for line in lines).isoformat(),
        "currency": currency,
        "account_ids": sorted({account for line in lines for account in line.account_ids}),
        "status": "ok" if all(line.status == "ok" for line in lines) else "partial",
        "budget_captured": budget_captured,
        "budget_complete": budget_complete,
        "budget_partial": budget_partial,
        "budget_source": budget_source,
        "budget_currency_compatible": budget_currency_compatible,
        "currency_compatible": currency is not None,
        "monthly_budget": monthly_budget,
        "applicable_days": applicable_days,
        "days_in_month": days_in_month,
        "observed_days": observed_days,
        **bases,
        "total_events": total_events,
        "total_events_available": total_events_available,
        "total_events_partial": total_events_partial,
        "total_event_sources": total_event_sources,
        "total_cpa": total_cpa,
        "total_cpa_partial": total_cpa_partial,
        "total_cpa_sources": total_cpa_sources,
        "total_cpc": total_cpc,
        "conversion_value_partial": value_partial,
        "conversion_value_sources": value_sources,
        "roas_partial": roas_partial,
        "roas_sources": value_sources if roas is not None else [],
        **kpis,
        "pacing_status": (
            "currency_mismatch"
            if budget_captured and not budget_currency_compatible
            else pacing_status(kpis)
        ),
        "reach_is_unique": bool(reach_is_aggregatable and len(platforms) == 1 and len(lines) == 1),
        "reach_is_sum": any(line.reach_is_sum for line in lines),
    }


def build_performance_summary(
    service,
    client: Client,
    clients: list[Client],
    budget_repository: BudgetRepository,
    start: date,
    end: date,
    refresh: bool = False,
    reporting_groups: list[ReportingGroup] | None = None,
    reporting_groups_by_client_month: dict[tuple[str, int, int], list[ReportingGroup]] | None = None,
) -> dict[str, Any]:
    """Mismo dataset mensual y mismos KPI que alimentan el XLSX descargable."""
    report_lines, data_lines = collect_report_lines(
        service, [client], start, end, owners=exclusive_claims(clients), refresh=refresh,
        reporting_groups=reporting_groups,
        reporting_groups_by_client_month=reporting_groups_by_client_month,
    )
    budgets = {item.key: item for item in budget_repository.list(client.id)}
    slices = month_slices(start, end)
    grouping_active = any(line.grouping_enabled for line in report_lines)
    monthly = []
    for month in slices:
        for platform in REPORT_PLATFORMS:
            candidates = [line for line in report_lines if line.period.year == month.year and line.period.month == month.month and line.platform == platform]
            month_grouping_active = any(line.grouping_enabled for line in candidates)
            group_ids = sorted({line.reporting_group_id for line in candidates}, key=lambda value: value or "")
            if not month_grouping_active and candidates:
                group_ids = [None]
            for group_id in group_ids:
                selected = candidates if not month_grouping_active else [line for line in candidates if line.reporting_group_id == group_id]
                if selected:
                    monthly.append(_summary_record(selected, budgets, level="month_platform", period=month, data_lines=data_lines))
    platform_totals = [
        _summary_record([line for line in report_lines if line.platform == platform], budgets, level="platform_total", data_lines=data_lines)
        for platform in REPORT_PLATFORMS
        if any(line.platform == platform for line in report_lines)
    ]
    period_total = _summary_record(report_lines, budgets, level="period_total", data_lines=data_lines) if report_lines else None
    grouped_monthly = monthly if grouping_active else []
    return {
        "client_id": client.id,
        "client_name": client.name,
        "from": start.isoformat(),
        "to": end.isoformat(),
        "currency": client.currency,
        "monthly": monthly,
        "grouping_active": grouping_active,
        "grouped_monthly": grouped_monthly,
        "platform_totals": platform_totals,
        "period_total": period_total,
        "pacing_thresholds": PACING_THRESHOLDS,
    }


REPORT_HEADERS = [
    "MES", "CLIENTE", "MEDIO", "CUENTAS", "PRESUPUESTO MENSUAL",
    "PRESUPUESTO DEL PERIODO", "GASTO DEL PERIODO", "DIF. PRESUPUESTO",
    "GASTO ESPERADO", "DIF. PACING", "% CONSUMIDO", "CONVERSIONES", "CLIENTES POTENCIALES",
    "VALOR DE CONVERSIÓN", "PROMEDIO DIARIO", "PROYECCIÓN FIN DE MES", "CPA",
    "COSTO POR RESULTADO", "ROAS", "TICKET PROMEDIO", "CPC", "CLICS", "IMPRESIONES", "CTR",
    "ALCANCE", "FRECUENCIA", "DÍAS APLICABLES", "DÍAS DEL MES", "ESTADO", "GRUPO", "TIPO_GRUPO",
]

REPORT_COLUMNS = {
    "monthly_budget": 5, "budget_period": 6, "spend": 7,
    "budget_difference": 8, "expected_spend": 9, "pacing_difference": 10,
    "budget_consumed": 11, "conversions": 12, "leads": 13,
    "conversion_value": 14, "daily_conversions": 15, "projected_conversions": 16,
    "cpa": 17, "cpl": 18, "roas": 19, "ticket_average": 20, "cpc": 21,
    "clicks": 22, "impressions": 23, "ctr": 24, "reach": 25,
    "frequency": 26, "applicable_days": 27, "days_in_month": 28, "status": 29,
}

DATA_HEADERS = [
    "DESDE", "HASTA", "MES", "CLIENTE_ID", "CLIENTE", "PLATAFORMA", "CUENTA_ID",
    "CUENTA", "NIVEL", "CAMPAÑA_ID", "CAMPAÑA", "OBJETIVO", "GASTO",
    "CONVERSIONES", "LEADS", "VALOR_CONVERSION", "CLICS", "IMPRESIONES", "ALCANCE",
    "MONEDA", "ESTADO", "GRUPO_REPORTE", "TIPO_GRUPO", "CHECKOUTS",
]


def _render_excel_expression(expression: Any, references: dict[str, str]) -> str:
    if isinstance(expression, str):
        if expression in references:
            return references[expression]
        nested = KPI_EXPRESSIONS.get(expression)
        return _render_excel_expression(nested, references) if nested is not None else ""
    operator, *args = expression
    left = _render_excel_expression(args[0], references)
    right = _render_excel_expression(args[1], references)
    symbols = {"add": "+", "sub": "-", "mul": "*", "div": "/"}
    return f"({left}{symbols[operator]}{right})"


def _formulae(row: int) -> dict[int, str]:
    references = {
        "monthly_budget": f"E{row}", "budget_period": f"F{row}",
        "expected_spend": f"I{row}", "spend": f"G{row}",
        "conversions": f"L{row}", "leads": f"M{row}",
        "conversion_value": f"N{row}", "clicks": f"V{row}",
        "impressions": f"W{row}", "reach": f"Y{row}",
        "applicable_days": f"AA{row}", "days_in_month": f"AB{row}",
        "observed_days": f"AA{row}",
    }
    formulas = {}
    for name, expression in KPI_EXPRESSIONS.items():
        # Estas dos columnas reciben fórmulas propias/aggregateas antes de los
        # KPI dependientes, para conservar la edición del presupuesto mensual.
        if name in ("budget_period", "expected_spend"):
            continue
        leaves = _expression_leaves(expression)
        checks = [f"NOT(ISNUMBER({references[leaf]}))" for leaf in leaves if leaf in references]
        if name == "budget_difference":
            output_column = REPORT_COLUMNS[name]
        else:
            output_column = REPORT_COLUMNS[name]
        expression_text = _render_excel_expression(expression, references)
        guard = f"OR({','.join(checks)})" if checks else "FALSE"
        formulas[output_column] = f'=IF({guard},"",IFERROR({expression_text},""))'
    # Prorrateo y esperado salen del mismo AST de KPI_EXPRESSIONS.
    for name in ("budget_period", "expected_spend"):
        expression = KPI_EXPRESSIONS[name]
        leaves = _expression_leaves(expression)
        checks = [f"NOT(ISNUMBER({references[leaf]}))" for leaf in leaves if leaf in references]
        expression_text = _render_excel_expression(expression, references)
        formulas[REPORT_COLUMNS[name]] = f'=IF(OR({",".join(checks)}),"",IFERROR({expression_text},""))'
    return formulas


def _style_header(ws, row: int, color: str = "1F4E78") -> None:
    fill = PatternFill("solid", fgColor=color)
    for cell in ws[row]:
        cell.fill = fill
        cell.font = Font(color="FFFFFF", bold=True)
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)


def _budget_key(client_id: str, platform: str, year: int, month: int, reporting_group_id: str | None = None) -> tuple[str, str, int, int, str | None, str | None, str | None]:
    return client_id, platform, year, month, reporting_group_id, None, None


def _build_workbook(
    report_lines: list[ReportLine],
    data_lines: list[DataLine],
    budgets: list[MonthlyBudget],
    start: date,
    end: date,
) -> Workbook:
    wb = Workbook()
    report_ws = wb.active
    report_ws.title = "REPORTE SEMANAL"
    budget_ws = wb.create_sheet("PRESUPUESTOS")
    data_ws = wb.create_sheet("DATOS")
    config_ws = wb.create_sheet("CONFIGURACION")
    wb.calculation.fullCalcOnLoad = True
    wb.calculation.forceFullCalc = True
    wb.calculation.calcMode = "auto"

    # ------------------------------ presupuestos: una fila por clave del reporte
    stored = {item.key: item for item in budgets}
    unique_lines = {
        _budget_key(line.client_id, line.platform, line.period.year, line.period.month, line.reporting_group_id): line
        for line in report_lines
    }
    budget_ws.append(["CLIENTE", "GRUPO", "TIPO_GRUPO", "PLATAFORMA", "MES", "AÑO", "PRESUPUESTO", "MONEDA"])
    _style_header(budget_ws, 1)
    budget_row: dict[tuple[str, str, int, int, str | None], int] = {}
    for key, line in sorted(unique_lines.items(), key=lambda item: (item[1].client_name.lower(), item[0][2], item[0][3], item[0][1], item[0][4] or "")):
        item = stored.get(key)
        budget_ws.append([
            line.client_name,
            line.reporting_group_name or ("GENERAL" if line.grouping_enabled else ""),
            line.reporting_group_type or ("general" if line.grouping_enabled else ""),
            PLATFORM_LABELS[line.platform],
            MONTH_NAMES[line.period.month - 1],
            line.period.year,
            item.budget if item else None,
            item.currency if item else line.currency,
        ])
        current_row = budget_ws.max_row
        budget_row[key] = current_row
        budget_ws.cell(current_row, 7).number_format = MONEY_FORMAT
        budget_ws.cell(current_row, 7).protection = Protection(locked=False)
    budget_ws.freeze_panes = "A2"
    budget_ws.auto_filter.ref = budget_ws.dimensions
    for column, width in (("A", 28), ("B", 22), ("C", 16), ("D", 18), ("E", 16), ("F", 12), ("G", 17), ("H", 12)):
        budget_ws.column_dimensions[column].width = width
    if budget_ws.max_row >= 2:
        validation = DataValidation(type="decimal", operator="greaterThanOrEqual", formula1="0", allow_blank=True)
        validation.error = "Captura un presupuesto numérico mayor o igual a cero."
        validation.errorTitle = "Presupuesto inválido"
        validation.prompt = "Única columna editable: presupuesto mensual."
        validation.promptTitle = "Presupuesto"
        budget_ws.add_data_validation(validation)
        validation.add(f"G2:G{budget_ws.max_row}")
    budget_ws.protection.sheet = True
    budget_ws.protection.selectLockedCells = False
    budget_ws.protection.selectUnlockedCells = True

    # ------------------------------------------------------------- reporte
    last_col = len(REPORT_HEADERS)
    last_letter = get_column_letter(last_col)
    report_ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=last_col)
    report_ws["A1"] = "REPORTE SEMANAL DE PERFORMANCE"
    report_ws["A1"].font = Font(size=18, bold=True, color="FFFFFF")
    report_ws["A1"].fill = PatternFill("solid", fgColor="17365D")
    report_ws["A1"].alignment = Alignment(horizontal="center")
    report_ws.merge_cells(start_row=2, start_column=1, end_row=2, end_column=last_col)
    report_ws["A2"] = f"Periodo: {start.isoformat()} al {end.isoformat()}"
    report_ws["A2"].alignment = Alignment(horizontal="center")
    report_ws["A2"].font = Font(italic=True, color="44546A")
    report_ws.merge_cells(start_row=3, start_column=1, end_row=3, end_column=last_col)
    report_ws["A3"] = "CLIENTES ACTIVOS Y FUENTES ASIGNADAS"
    report_ws["A3"].font = Font(bold=True, color="FFFFFF")
    report_ws["A3"].fill = PatternFill("solid", fgColor="5B9BD5")
    report_ws.append([])
    report_ws.append(REPORT_HEADERS)
    _style_header(report_ws, 5)
    report_ws.row_dimensions[5].height = 42
    report_ws.freeze_panes = "A6"

    by_client: dict[tuple[str, str], list[ReportLine]] = {}
    for line in report_lines:
        by_client.setdefault((line.client_id, line.client_name), []).append(line)

    thin = Side(style="thin", color="D9E2F3")
    for (_client_id, client_name), client_lines in by_client.items():
        report_ws.append([client_name.upper()])
        client_heading = report_ws.max_row
        report_ws.merge_cells(start_row=client_heading, start_column=1, end_row=client_heading, end_column=last_col)
        heading_cell = report_ws.cell(client_heading, 1)
        heading_cell.fill = PatternFill("solid", fgColor="D9EAF7")
        heading_cell.font = Font(bold=True, color="17365D")
        month_groups: dict[tuple[int, int], list[ReportLine]] = {}
        for line in client_lines:
            month_groups.setdefault((line.period.year, line.period.month), []).append(line)
        for _month_key, month_lines in sorted(month_groups.items()):
            grouped = any(line.grouping_enabled for line in month_lines)
            sections: dict[str, list[ReportLine]] = {}
            for line in month_lines:
                section = (line.reporting_group_name or "GENERAL") if grouped else ""
                sections.setdefault(section, []).append(line)
            for section_name, lines in sections.items():
                if grouped:
                    report_ws.append([f"{client_name.upper()} · {section_name.upper()}"])
                    section_heading = report_ws.max_row
                    report_ws.merge_cells(start_row=section_heading, start_column=1, end_row=section_heading, end_column=last_col)
                    section_cell = report_ws.cell(section_heading, 1)
                    section_cell.fill = PatternFill("solid", fgColor="741B47")
                    section_cell.font = Font(bold=True, color="FFFFFF")
                    section_cell.alignment = Alignment(horizontal="center", vertical="center")
                    report_ws.row_dimensions[section_heading].height = 22
                first_detail = report_ws.max_row + 1
                for line in sorted(lines, key=lambda item: REPORT_PLATFORMS.index(item.platform)):
                    key = _budget_key(line.client_id, line.platform, line.period.year, line.period.month, line.reporting_group_id)
                    budget_ref = budget_row[key]
                    stored_budget = stored.get(key)
                    budget_reference = (
                        "Moneda incompatible"
                        if stored_budget is not None and stored_budget.currency != line.currency
                        else f'=IF(ISNUMBER(\'PRESUPUESTOS\'!$G${budget_ref}),\'PRESUPUESTOS\'!$G${budget_ref},"Presupuesto no capturado")'
                    )
                    report_ws.append([
                        line.period.label, line.client_name, PLATFORM_LABELS[line.platform], ", ".join(line.account_ids),
                        budget_reference, None, line.spend, None, None, None, None, line.conversions, line.leads,
                        line.conversion_value, None, None, None, None, None, None, None, line.clicks,
                        line.impressions, None, line.reach, None, line.period.applicable_days,
                        line.period.days_in_month, line.status,
                        line.reporting_group_name or ("GENERAL" if line.grouping_enabled else ""),
                        line.reporting_group_type or ("general" if line.grouping_enabled else ""),
                    ])
                    row = report_ws.max_row
                    report_ws.cell(row, REPORT_COLUMNS["budget_period"], _formulae(row)[REPORT_COLUMNS["budget_period"]])
                    for column, formula in _formulae(row).items():
                        report_ws.cell(row, column, formula)
                last_detail = report_ws.max_row
                group_currencies = {line.currency for line in lines}
                group_currency_compatible = len(group_currencies) == 1
                group_budget_currency_compatible = group_currency_compatible and all(
                    stored.get(_budget_key(line.client_id, line.platform, line.period.year, line.period.month, line.reporting_group_id)) is None
                    or stored[_budget_key(line.client_id, line.platform, line.period.year, line.period.month, line.reporting_group_id)].currency == line.currency
                    for line in lines
                )
                monthly_budget_total = (
                    "Moneda incompatible" if not group_budget_currency_compatible
                    else f'=IF(COUNT(E{first_detail}:E{last_detail})=ROWS(E{first_detail}:E{last_detail}),SUM(E{first_detail}:E{last_detail}),"Presupuesto no capturado")'
                )
                period_budget_total = (
                    "Moneda incompatible" if not group_budget_currency_compatible
                    else f'=IF(COUNT(F{first_detail}:F{last_detail})=ROWS(F{first_detail}:F{last_detail}),SUM(F{first_detail}:F{last_detail}),"")'
                )
                total_label = f"TOTAL {section_name}" if grouped else f"TOTAL {client_name}"
                report_ws.append([
                    lines[0].period.label, total_label, "TOTAL", "", monthly_budget_total, period_budget_total,
                    f'=IF(COUNT(G{first_detail}:G{last_detail})=ROWS(G{first_detail}:G{last_detail}),SUM(G{first_detail}:G{last_detail}),"")' if group_currency_compatible else None,
                    None, None, None, None,
                    f'=IF(COUNT(L{first_detail}:L{last_detail})=ROWS(L{first_detail}:L{last_detail}),SUM(L{first_detail}:L{last_detail}),"")',
                    f'=IF(COUNT(M{first_detail}:M{last_detail})=ROWS(M{first_detail}:M{last_detail}),SUM(M{first_detail}:M{last_detail}),"")',
                    f'=IF(COUNT(N{first_detail}:N{last_detail})=ROWS(N{first_detail}:N{last_detail}),SUM(N{first_detail}:N{last_detail}),"")' if group_currency_compatible else None,
                    None, None, None, None, None, None, None,
                    f'=IF(COUNT(V{first_detail}:V{last_detail})=ROWS(V{first_detail}:V{last_detail}),SUM(V{first_detail}:V{last_detail}),"")',
                    f'=IF(COUNT(W{first_detail}:W{last_detail})=ROWS(W{first_detail}:W{last_detail}),SUM(W{first_detail}:W{last_detail}),"")',
                    None,
                    f'=IF(COUNT(Y{first_detail}:Y{last_detail})=ROWS(Y{first_detail}:Y{last_detail}),SUM(Y{first_detail}:Y{last_detail}),"")' if len(lines) == 1 and not lines[0].reach_is_sum else None,
                    None, lines[0].period.applicable_days, lines[0].period.days_in_month, "TOTAL",
                    section_name if grouped else "", lines[0].reporting_group_type or ("general" if grouped else ""),
                ])
                total_row = report_ws.max_row
                for column, formula in _formulae(total_row).items():
                    if column != REPORT_COLUMNS["budget_period"]:
                        report_ws.cell(total_row, column, formula)
                for cell in report_ws[total_row]:
                    cell.font = Font(bold=True)
                    cell.fill = PatternFill("solid", fgColor="E2F0D9")

                # La visibilidad del cliente también aplica al XLSX. DATOS
                # conserva las bases originales, igual que el payload del dashboard.
                hidden = {normalize_metric_family(key) for key in lines[0].hidden_metrics}
                for key, column in REPORT_COLUMNS.items():
                    if normalize_metric_family(key) in hidden:
                        for detail_row in range(first_detail, total_row + 1):
                            report_ws.cell(detail_row, column).value = None

    for row in report_ws.iter_rows(min_row=6, max_row=report_ws.max_row):
        for cell in row:
            cell.border = Border(bottom=thin)
            cell.alignment = Alignment(vertical="center", wrap_text=cell.column in (2, 3, 4))
    for column in (5, 6, 7, 8, 9, 10, 14, 17, 18, 20, 21):
        for cell in report_ws.iter_cols(min_col=column, max_col=column, min_row=6, max_row=report_ws.max_row):
            for item in cell:
                item.number_format = MONEY_FORMAT
    for column in (11, 24):
        for cell in report_ws.iter_cols(min_col=column, max_col=column, min_row=6, max_row=report_ws.max_row):
            for item in cell:
                item.number_format = PERCENT_FORMAT
    for column in (19, 26):
        for cell in report_ws.iter_cols(min_col=column, max_col=column, min_row=6, max_row=report_ws.max_row):
            for item in cell:
                item.number_format = RATIO_FORMAT
    for column in (12, 13, 15, 16, 22, 23, 25, 27, 28):
        for cell in report_ws.iter_cols(min_col=column, max_col=column, min_row=6, max_row=report_ws.max_row):
            for item in cell:
                item.number_format = NUMBER_FORMAT

    widths = [18, 26, 16, 25, 29, 20, 18, 18, 18, 16, 15, 16, 14, 20, 17, 22, 14, 14, 12, 18, 14, 14, 16, 12, 14, 14, 16, 14, 16]
    for index, width in enumerate(widths, 1):
        report_ws.column_dimensions[get_column_letter(index)].width = width
    if report_ws.max_row >= 6:
        pacing_column = get_column_letter(REPORT_COLUMNS["pacing_difference"])
        budget_column = get_column_letter(REPORT_COLUMNS["monthly_budget"])
        expected_column = get_column_letter(REPORT_COLUMNS["expected_spend"])
        pacing_range = f"{pacing_column}6:{pacing_column}{report_ws.max_row}"
        gray = PatternFill("solid", fgColor="D9D9D9")
        green = PatternFill("solid", fgColor="C6EFCE")
        yellow = PatternFill("solid", fgColor="FFEB9C")
        red = PatternFill("solid", fgColor="FFC7CE")
        report_ws.conditional_formatting.add(pacing_range, FormulaRule(formula=[f'NOT(ISNUMBER(${budget_column}6))'], fill=gray))
        report_ws.conditional_formatting.add(pacing_range, FormulaRule(formula=[f'AND(ISNUMBER(${expected_column}6),${expected_column}6<>0,ABS(${pacing_column}6/${expected_column}6)<={PACING_GREEN_MAX})'], fill=green))
        report_ws.conditional_formatting.add(pacing_range, FormulaRule(formula=[f'AND(ISNUMBER(${expected_column}6),${expected_column}6<>0,ABS(${pacing_column}6/${expected_column}6)>{PACING_GREEN_MAX},ABS(${pacing_column}6/${expected_column}6)<={PACING_YELLOW_MAX})'], fill=yellow))
        report_ws.conditional_formatting.add(pacing_range, FormulaRule(formula=[f'AND(ISNUMBER(${expected_column}6),${expected_column}6<>0,ABS(${pacing_column}6/${expected_column}6)>{PACING_YELLOW_MAX})'], fill=red))
    report_ws.auto_filter.ref = f"A5:{last_letter}{report_ws.max_row}"
    report_ws.protection.sheet = True

    # ---------------------------------------------------------------- datos
    data_ws.append(DATA_HEADERS)
    _style_header(data_ws, 1, "44546A")
    for line in data_lines:
        data_ws.append([
            line.period.start,
            line.period.end,
            line.period.label,
            line.client_id,
            line.client_name,
            PLATFORM_LABELS[line.platform],
            line.account_id,
            line.account_name,
            line.level,
            line.campaign_id,
            line.campaign_name,
            line.objective,
            line.spend,
            line.conversions,
            line.leads,
            line.conversion_value,
            line.clicks,
            line.impressions,
            line.reach,
            line.currency,
            line.status,
            line.reporting_group_name or ("GENERAL" if line.grouping_enabled else None),
            line.reporting_group_type or ("general" if line.grouping_enabled else None),
            line.checkouts,
        ])
    for row in range(2, data_ws.max_row + 1):
        data_ws.cell(row, 1).number_format = "yyyy-mm-dd"
        data_ws.cell(row, 2).number_format = "yyyy-mm-dd"
        data_ws.cell(row, 13).number_format = MONEY_FORMAT
        data_ws.cell(row, 16).number_format = MONEY_FORMAT
    data_ws.freeze_panes = "A2"
    data_ws.auto_filter.ref = data_ws.dimensions
    for index, header in enumerate(DATA_HEADERS, 1):
        data_ws.column_dimensions[get_column_letter(index)].width = min(32, max(12, len(header) + 2))
    data_ws.protection.sheet = True
    data_ws.sheet_state = "hidden"

    config_headers = [
        "CLIENTE", "CLIENTE_ID", "AÑO", "MES", "GRUPO", "TIPO_GRUPO",
        "PLATAFORMA", "CUENTA_ID", "CAMPAÑA_ID", "CAMPAÑA", "OBJETIVO", "ACTIVA",
    ]
    config_ws.append(config_headers)
    _style_header(config_ws, 1, "44546A")
    observed = {
        (row.client_id, row.period.year, row.period.month, row.platform, row.account_id, row.campaign_id): row
        for row in data_lines if row.level == "CAMPAÑA"
    }
    for row in sorted(observed.values(), key=lambda item: (
        item.client_name.casefold(), item.period.year, item.period.month,
        item.platform, item.account_id, item.campaign_name or "",
    )):
        config_ws.append([
            row.client_name, row.client_id, row.period.year, MONTH_NAMES[row.period.month - 1],
            row.configured_group_name or ("GENERAL" if row.grouping_enabled else ""),
            row.configured_group_type or ("general" if row.grouping_enabled else ""),
            PLATFORM_LABELS[row.platform], row.account_id, row.campaign_id,
            row.campaign_name, row.objective,
            row.configured_group_active if row.configured_group_active is not None else True,
        ])
    config_ws.freeze_panes = "A2"
    config_ws.auto_filter.ref = config_ws.dimensions
    for index, header in enumerate(config_headers, 1):
        config_ws.column_dimensions[get_column_letter(index)].width = min(32, max(12, len(header) + 2))
    config_ws.protection.sheet = True
    return wb


# La hoja mensual usa el vocabulario y los colores observados en la plantilla
# histórica (azul claro por sección, borgoña por cliente y amarillo para KPI).
PROFILE_METRIC_LABELS = {
    "spend": "Gasto mensual a hoy", "result": "Resultado",
    "leads_or_conversions": "Leads total mes / Conversiones", "budget_difference": "Diferencia",
    "expected_spend": "Gasto que debe llevar a hoy", "pacing_difference": "Diferencia (de lo que se debe llevar)",
    "conversion_value": "Valor de conversión", "daily_result": "Promedio de conversiones diarias",
    "ticket_average": "Ticket Promedio", "projected_result": "Promedio de lo que vamos a llegar al final del mes", "cost_per_result": "CPA por medio / Costo por resultado",
    "roas": "ROAS", "clicks": "Clics", "impressions": "Impresiones", "reach": "Alcance",
    "ctr": "CTR", "frequency": "Frecuencia", "cpc": "CPC", "cpm": "CPM",
}

PROFILE_SECTION_FILL = "FFC9DAF8"
PROFILE_CLIENT_FILL = "FF741B47"
PROFILE_HEADER_FILL = "FFD9D9D9"
PROFILE_KPI_FILL = "FFFFFF00"

PROFILE_COLUMN_WIDTHS = {
    "MES": 17, "Medio": 21, "Presupuesto": 23,
    "Sede": 22, "Pedidos": 15, "Monto de venta": 23,
    "CLIENTE": 24, "KPI": 15, "VALOR": 18, "PERFIL": 22, "ESTADO": 28,
    "Gasto mensual a hoy": 23, "Diferencia": 21,
    "Gasto que debe llevar a hoy": 24, "Diferencia (de lo que se debe llevar)": 25,
    "Valor de conversión": 24, "Ticket Promedio": 21,
    "Promedio de conversiones diarias": 24,
    "Promedio de lo que vamos a llegar al final del mes": 27,
    "CPA por medio / Costo por resultado": 28, "ROAS": 14,
    "Clics": 15, "Impresiones": 18, "Alcance": 17, "CTR": 14,
    "CPC": 15, "CPM": 15, "Resultados": 24,
    "Leads total mes / Conversiones": 24,
    "Leads (Meta) / Conversiones (Google)": 25,
    "Purchase (Meta) / Conversiones (Google)": 26,
    "CPA (Google) / Costo por resultado (Meta)": 29,
}


def _profile_headers(profile: ReportProfile, metrics: tuple[str, ...]) -> list[str]:
    headers = ["MES", "Medio", "Presupuesto", *[PROFILE_METRIC_LABELS[key] for key in metrics]]
    if "result" in metrics:
        headers[3 + metrics.index("result")] = (
            "Resultados\nMeta: Compras · Google: Conversiones" if profile.meta_result_mode == "purchase" else
            "Resultados\nMeta: Clientes potenciales · Google: Conversiones" if profile.meta_result_mode == "lead" else
            "Conversiones"
        )
    if "leads_or_conversions" in metrics and profile.meta_result_mode == "lead":
        headers[3 + metrics.index("leads_or_conversions")] = "Clientes potenciales (Meta) / Conversiones (Google)"
    if "cost_per_result" in metrics:
        headers[3 + metrics.index("cost_per_result")] = (
            "CPA (Google) / Costo por resultado (Meta)" if profile.meta_result_mode == "purchase" else
            "CPA (Google) / Costo por resultado (Meta)" if profile.meta_result_mode == "lead" else
            "CPA / Costo por adquisición"
        )
    return headers

def _profile_result(profile: ReportProfile, line: ReportLine) -> tuple[float | None, str]:
    """Respeta el resultado normalizado por plataforma y modo vigente del cliente."""
    if line.platform == "meta_ads":
        if profile.meta_result_mode == "purchase":
            return line.purchases, "Compras"
        if profile.meta_result_mode == "lead":
            return line.leads, "Clientes potenciales"
    return line.conversions, "Conversiones"


def _profile_metric_values(
    profile: ReportProfile, block: ReportBlockProfile, lines: list[ReportLine],
    stored: dict, report_lines: list[ReportLine], data_lines: list[DataLine] | None = None,
) -> dict[str, float | str | None]:
    if not lines:
        return {}
    currencies = {line.currency for line in lines}
    currency_ok = len(currencies) == 1
    spend_values = [line.spend for line in lines]
    spend = sum(float(value) for value in spend_values) if currency_ok and spend_values and all(value is not None for value in spend_values) else None
    result_pairs = [_profile_result(profile, line) for line in lines]
    results = [value for value, _ in result_pairs]
    result = sum(float(value) for value in results) if results and all(value is not None for value in results) else None
    leads = [line.leads if line.platform == "meta_ads" else line.conversions for line in lines]
    lead_total = sum(float(value) for value in leads) if leads and all(value is not None for value in leads) else None
    # The provider's Meta conversion_value is tied to its canonical selected
    # Purchase action. Only purchase-mode client profiles may include it.
    value_lines = [
        line for line in lines
        if line.platform == "google_ads"
        or (line.platform == "meta_ads" and profile.meta_result_mode == "purchase" and line.purchases is not None)
    ]
    value_currencies = {line.currency for line in value_lines}
    value_currency_ok = len(value_currencies) == 1
    value_values = [
        line.conversion_value
        if not (line.platform == "meta_ads" and profile.meta_result_mode == "purchase" and line.purchases is None)
        else None
        for line in value_lines
    ]
    conversion_value = sum(float(value) for value in value_values) if value_currency_ok and value_values and all(value is not None for value in value_values) else None
    value_spend_values = [line.spend for line in value_lines]
    value_spend = sum(float(value) for value in value_spend_values) if value_currency_ok and value_spend_values and all(value is not None for value in value_spend_values) else None
    clicks = _sum_base(lines, "clicks")
    impressions = _sum_base(lines, "impressions")
    reach_values = [line.reach for line in lines]
    reach = sum(float(value) for value in reach_values) if reach_values and all(value is not None for value in reach_values) else None
    spend_weighted_budgets: list[float] = []
    monthly_budgets: list[float] = []
    for line in lines:
        budget = _effective_budget(line, stored, data_lines or [])
        if budget is not None and budget.currency == line.currency:
            monthly_budgets.append(float(budget.budget))
            spend_weighted_budgets.append(float(budget.budget) / line.period.days_in_month * line.period.applicable_days)
    monthly_budget = sum(monthly_budgets) if len(monthly_budgets) == len(lines) else None
    expected = sum(spend_weighted_budgets) if len(spend_weighted_budgets) == len(lines) else None
    bases = {
        "spend": spend, "conversions": result, "leads": lead_total,
        "conversion_value": conversion_value, "clicks": clicks,
        "impressions": impressions, "reach": reach,
    }
    observed_days = observed_days_in_period(lines[0].period)
    kpis = calculate_performance_kpis(
        bases, monthly_budget=monthly_budget,
        applicable_days=lines[0].period.applicable_days,
        days_in_month=lines[0].period.days_in_month,
        observed_days=observed_days,
        budget_period=expected, expected_spend=expected,
    )
    result_cost = spend / result if spend is not None and result not in (None, 0) else None
    cost_per_click = spend / clicks if spend is not None and clicks not in (None, 0) else None
    ctr = clicks / impressions if clicks is not None and impressions not in (None, 0) else None
    frequency = impressions / reach if impressions is not None and reach not in (None, 0) else None
    cpm = spend / impressions * 1000 if spend is not None and impressions not in (None, 0) else None
    return {
        "spend": spend, "result": result, "leads_or_conversions": lead_total,
        "budget_difference": kpis["budget_difference"], "expected_spend": expected,
        "pacing_difference": kpis["pacing_difference"],
        "conversion_value": conversion_value if block.requires_value_metrics else None,
        "ticket_average": conversion_value / result if block.requires_value_metrics and conversion_value is not None and result not in (None, 0) else None,
        "daily_result": result / observed_days if result is not None and observed_days else None,
        "projected_result": kpis["projected_conversions"], "cost_per_result": result_cost,
        "roas": conversion_value / value_spend if block.requires_value_metrics and conversion_value is not None and value_spend not in (None, 0) else None,
        "clicks": clicks, "impressions": impressions, "reach": reach,
        "ctr": ctr, "frequency": frequency, "cpc": cost_per_click, "cpm": cpm,
        "result_label": "Compras" if profile.meta_result_mode == "purchase" else ("Clientes potenciales" if profile.meta_result_mode == "lead" else "Conversiones"),
    }


def _validated_woosync_sales(woosync: dict[str, Any] | None) -> dict[str, Any] | None:
    """Valida agregados WooSync y un desglose por sede consultado por separado.

    WooSync no atribuye ventas a canales. `branch_sales` solo se acepta cuando
    cada fila contiene una sede y sus agregados reales; la suma debe reconciliar
    con el total para no presentar un detalle parcial como si fuera completo.
    """
    if not isinstance(woosync, dict) or not isinstance(woosync.get("sales_overview"), dict):
        return None
    overview = woosync["sales_overview"]
    orders = overview.get("orders_count")
    sales = overview.get("total_sales")

    def numeric(value: Any) -> bool:
        return isinstance(value, (int, float)) and not isinstance(value, bool) and isfinite(float(value)) and float(value) >= 0

    if orders is not None and (not numeric(orders) or not float(orders).is_integer()):
        raise ReportValidationError("WooSync devolvió orders_count inválido.")
    if sales is not None and not numeric(sales):
        raise ReportValidationError("WooSync devolvió total_sales inválido.")

    branches = woosync.get("branch_sales") or []
    if not isinstance(branches, list):
        raise ReportValidationError("WooSync branch_sales debe ser una lista.")
    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()
    period = woosync.get("period") if isinstance(woosync.get("period"), dict) else {}
    overall_filters = woosync.get("applied_filters") if isinstance(woosync.get("applied_filters"), dict) else {}
    for item in branches:
        if not isinstance(item, dict):
            raise ReportValidationError("WooSync devolvió un detalle de sede inválido.")
        branch_id = str(item.get("branch_id") or "").strip().lower()
        name = str(item.get("branch_name") or "").strip()
        branch_orders, branch_sales = item.get("orders_count"), item.get("total_sales")
        if not branch_id or not name or branch_id in seen or not numeric(branch_orders) or not float(branch_orders).is_integer() or not numeric(branch_sales):
            raise ReportValidationError("WooSync devolvió campos de sede inválidos o duplicados.")
        branch_filters = item.get("applied_filters")
        if isinstance(branch_filters, dict):
            if period and (branch_filters.get("from") != period.get("from") or branch_filters.get("to") != period.get("to")):
                raise ReportValidationError("El detalle de sede WooSync no corresponde al periodo del reporte.")
            if branch_filters.get("branches") not in (None, [branch_id]):
                raise ReportValidationError("El filtro aplicado por WooSync no coincide con la sede reportada.")
        aggregate_branches = overall_filters.get("branches")
        if isinstance(aggregate_branches, list) and branch_id not in aggregate_branches:
            raise ReportValidationError("WooSync devolvió una sede fuera del agregado consultado.")
        seen.add(branch_id)
        normalized.append({"branch_id": branch_id, "branch_name": name,
                           "orders_count": int(branch_orders), "total_sales": float(branch_sales)})
    if normalized:
        if orders is None or sales is None:
            raise ReportValidationError("WooSync no devolvió totales para reconciliar el desglose por sede.")
        if sum(item["orders_count"] for item in normalized) != int(orders):
            raise ReportValidationError("Los pedidos por sede de WooSync no coinciden con el total.")
        if not isclose(sum(item["total_sales"] for item in normalized), float(sales), rel_tol=0, abs_tol=0.01):
            raise ReportValidationError("Las ventas por sede de WooSync no coinciden con el total.")
    return {
        "orders_count": int(orders) if orders is not None else None,
        "total_sales": float(sales) if sales is not None else None,
        "ticket_average": float(sales) / int(orders) if sales is not None and orders not in (None, 0) else None,
        "branch_sales": normalized,
        "applied_filters": overall_filters,
        "channel_attribution_available": woosync.get("channel_attribution_available") is True,
    }


def _style_woosync_body_cells(ws, row: int, *, total: bool = False) -> None:
    for column in range(1, 4):
        cell = ws.cell(row, column)
        cell.font = Font(name="Poppins", size=10, bold=total, color="000000")
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        if total:
            cell.fill = PatternFill("solid", fgColor="FFE2F0D9")
        elif column in (2, 3):
            cell.fill = PatternFill("solid", fgColor="FFC9DAF8")
        cell.border = Border(
            left=Side(style="medium" if column == 1 else "thin", color="000000"),
            right=Side(style="medium" if column == 3 else "thin", color="000000"),
            top=Side(style="thin", color="000000"),
            bottom=Side(style="thin", color="000000"),
        )


def _write_woosync_branch_row(ws, row: int, branch: dict[str, Any], *, total: bool) -> None:
    ws.cell(row, 1, branch["branch_name"])
    ws.cell(row, 2, branch["orders_count"])
    ws.cell(row, 3, branch["total_sales"])
    _style_woosync_body_cells(ws, row, total=total)
    ws.cell(row, 2).number_format = INTEGER_FORMAT
    ws.cell(row, 3).number_format = MONEY_FORMAT


def _build_profile_workbook(
    report_lines: list[ReportLine], data_lines: list[DataLine], budgets: list[MonthlyBudget],
    clients: list[Client], start: date, end: date, woosync_data: dict[str, dict] | None = None,
    reporting_groups_by_client: dict[str, list[ReportingGroup]] | None = None,
    reporting_groups_by_client_month: dict[tuple[str, int, int], list[ReportingGroup]] | None = None,
) -> Workbook:
    # `active` is the canonical client eligibility flag. Filtering here keeps
    # all workbook sheets aligned while leaving stored history untouched.
    clients = [client for client in clients if client.active]
    active_ids = {client.id for client in clients}
    report_lines = [line for line in report_lines if line.client_id in active_ids]
    data_lines = [line for line in data_lines if line.client_id in active_ids]
    budgets = [budget for budget in budgets if budget.client_id in active_ids]
    wb = Workbook()
    wb.remove(wb.active)
    stored = {item.key: item for item in budgets}
    data_ws = wb.create_sheet("DATOS")
    data_ws.append([*DATA_HEADERS[:13], "PURCHASES", *DATA_HEADERS[13:]])
    _style_header(data_ws, 1, "44546A")
    for line in data_lines:
        data_ws.append([
            line.period.start, line.period.end, line.period.label, line.client_id, line.client_name,
            PLATFORM_LABELS[line.platform], line.account_id, line.account_name, line.level,
            line.campaign_id, line.campaign_name, line.objective, line.spend, line.purchases,
            line.conversions, line.leads, line.conversion_value, line.clicks, line.impressions,
            line.reach, line.currency, line.status, line.reporting_group_name or ("GENERAL" if line.grouping_enabled else None),
            line.reporting_group_type or ("general" if line.grouping_enabled else None), line.checkouts,
        ])
    data_ws.sheet_state = "hidden"
    data_ws.freeze_panes = "A2"
    data_ws.auto_filter.ref = data_ws.dimensions
    for col in range(1, data_ws.max_column + 1):
        data_ws.column_dimensions[get_column_letter(col)].width = 19

    budget_ws = wb.create_sheet("PRESUPUESTOS")
    budget_ws.append(["CLIENTE_ID", "CLIENTE", "GRUPO", "PLATAFORMA", "AÑO", "MES", "PRESUPUESTO", "MONEDA", "ALCANCE", "CUENTA_ID", "CAMPAÑA_ID", "GRUPO_ID", "CATEGORÍA_EXCEL", "POSICIÓN_EXCEL"])
    _style_header(budget_ws, 1)
    clients_by_id = {client.id: client for client in clients}
    unique = {_budget_key(line.client_id, line.platform, line.period.year, line.period.month, line.reporting_group_id): line for line in report_lines}
    for key, line in sorted(unique.items(), key=lambda item: (
        client_report_sort_key(clients_by_id[item[0][0]]) if item[0][0] in clients_by_id else (1, 4, 1, 0, item[0][0]),
        item[0][2], item[0][3], item[0][1], item[0][4] or "",
    )):
        budget = stored.get(key)
        client = clients_by_id.get(line.client_id)
        budget_ws.append([line.client_id, line.client_name, line.reporting_group_name, PLATFORM_LABELS[line.platform], line.period.year, MONTH_NAMES[line.period.month - 1], budget.budget if budget else None, budget.currency if budget else line.currency, "GRUPO" if line.reporting_group_id else "GENERAL", None, None, line.reporting_group_id, client_report_category(client) if client else "GENERAL", client.report_position if client else None])
        budget_ws.cell(budget_ws.max_row, 7).protection = Protection(locked=False)
        budget_ws.cell(budget_ws.max_row, 7).number_format = MONEY_FORMAT
    represented_budget_keys = set(unique)
    client_names = {client.id: client.name for client in clients}
    for budget in budgets:
        if budget.campaign_id is None or budget.key in represented_budget_keys:
            continue
        campaign_group_id = next((line.reporting_group_id for line in data_lines if line.client_id == budget.client_id and line.platform == budget.platform and line.period.year == budget.year and line.period.month == budget.month and line.account_id == budget.account_id and line.campaign_id == budget.campaign_id), None)
        client = clients_by_id.get(budget.client_id)
        budget_ws.append([budget.client_id, client_names.get(budget.client_id, budget.client_id), "", PLATFORM_LABELS[budget.platform], budget.year, MONTH_NAMES[budget.month - 1], budget.budget, budget.currency, "CAMPAÑA", budget.account_id, budget.campaign_id, campaign_group_id, client_report_category(client) if client else "GENERAL", client.report_position if client else None])
        budget_ws.cell(budget_ws.max_row, 7).number_format = MONEY_FORMAT
        budget_ws.cell(budget_ws.max_row, 9).protection = Protection(locked=True)
    # Reordena también las asignaciones que solo existen como presupuesto de
    # campaña, manteniendo valores y formato de celda.
    budget_rows = []
    for source_row in range(2, budget_ws.max_row + 1):
        category = budget_ws.cell(source_row, 13).value or "GENERAL"
        position = budget_ws.cell(source_row, 14).value
        budget_rows.append((
            (CATEGORY_ORDER.get(category, CATEGORY_ORDER["GENERAL"]), position is None,
             position if position is not None else 0,
             str(budget_ws.cell(source_row, 2).value or "").casefold(),
             budget_ws.cell(source_row, 5).value or 0,
             budget_ws.cell(source_row, 6).value or "",
             str(budget_ws.cell(source_row, 4).value or ""),
             str(budget_ws.cell(source_row, 3).value or ""),
             str(budget_ws.cell(source_row, 11).value or "")),
            [copy(budget_ws.cell(source_row, col)) for col in range(1, budget_ws.max_column + 1)],
        ))
    budget_rows.sort(key=lambda item: item[0])
    for dest_row, (_key, cells) in enumerate(budget_rows, 2):
        for column, source_cell in enumerate(cells, 1):
            target = budget_ws.cell(dest_row, column, source_cell.value)
            if source_cell.has_style:
                target._style = copy(source_cell._style)
            if source_cell.number_format:
                target.number_format = source_cell.number_format
            target.protection = copy(source_cell.protection)
    budget_ws.freeze_panes = "A2"
    budget_ws.auto_filter.ref = budget_ws.dimensions
    budget_ws.protection.sheet = True
    budget_ws.protection.selectLockedCells = False
    budget_ws.protection.selectUnlockedCells = True
    if budget_ws.max_row > 1:
        validation = DataValidation(type="decimal", operator="greaterThanOrEqual", formula1="0", allow_blank=True)
        validation.error = "Captura un presupuesto numérico mayor o igual a cero."
        budget_ws.add_data_validation(validation)
        validation.add(f"G2:G{budget_ws.max_row}")
    budget_ws.freeze_panes = "A2"
    budget_ws.auto_filter.ref = budget_ws.dimensions

    config_ws = wb.create_sheet("CONFIGURACION")
    config_headers = [
        "CLIENTE", "CLIENTE_ID", "AÑO", "MES", "GRUPO", "TIPO_GRUPO", "PLATAFORMA",
        "CUENTA_ID", "CAMPAÑA_ID", "CAMPAÑA", "OBJETIVO", "ACTIVA", "PERFIL",
        "SECCIÓN", "KPI PRINCIPAL", "BLOQUE", "MÉTRICAS", "ESTADO_PERFIL", "PLATAFORMAS_ACTIVAS",
        "ESTADO_CLIENTE", "CATEGORÍA_EXCEL", "POSICIÓN_EXCEL",
    ]
    config_ws.append(config_headers)
    _style_header(config_ws, 1, "44546A")
    observed = {(row.client_id, row.period.year, row.period.month, row.platform, row.account_id, row.campaign_id): row
                for row in data_lines if row.level == "CAMPAÑA"}
    written_profile_months = set()
    for row in sorted(observed.values(), key=lambda item: (item.client_name.casefold(), item.period.year, item.period.month, item.platform, item.account_id, item.campaign_name or "")):
        profile = report_profile(row.client_id)
        block = block_for_group(profile, row.configured_group_name or row.reporting_group_name) if profile else None
        profile_status = "OK" if profile else "REPORT_PROFILE_NOT_CONFIGURED"
        if profile and len(profile.blocks) > 1 and not row.grouping_enabled:
            profile_status = "BLUMIA_REPORTING_GROUP_DATA_MISSING" if row.client_id == "blumia" else "REPORTING_GROUP_DATA_MISSING"
        config_ws.append([
            row.client_name, row.client_id, row.period.year, MONTH_NAMES[row.period.month - 1],
            row.configured_group_name or ("GENERAL" if row.grouping_enabled else ""),
            row.configured_group_type or ("general" if row.grouping_enabled else ""),
            PLATFORM_LABELS.get(row.platform, row.platform), row.account_id, row.campaign_id,
            row.campaign_name, row.objective,
            row.configured_group_active if row.configured_group_active is not None else True,
            "CONFIGURADO" if profile else "GENERAL", profile.section if profile else "GENERAL",
            profile.primary_kpi if profile else "", block.display_name if block else "",
            ", ".join(block.metrics) if block else "Gasto, resultados disponibles, clics",
            profile_status, ", ".join(row.configured_group_enabled_platforms),
            "ACTIVO", client_report_category(clients_by_id.get(row.client_id)) if clients_by_id.get(row.client_id) else "GENERAL", clients_by_id[row.client_id].report_position if row.client_id in clients_by_id else None,
        ])
        written_profile_months.add((row.client_id, row.period.year, row.period.month))
    for client in clients:
        profile = report_profile(client.id)
        for period in month_slices(start, end):
            if (client.id, period.year, period.month) not in written_profile_months:
                blocks = profile.blocks if profile else (ReportBlockProfile("general", "GENERAL", REPORT_PLATFORMS, ("spend", "leads_or_conversions", "clicks")),)
                for block in blocks:
                    config_ws.append([
                        client.name, client.id, period.year, MONTH_NAMES[period.month - 1], "", "", "", "", "", "", "", True,
                        "CONFIGURADO" if profile else "GENERAL", profile.section if profile else "GENERAL",
                        profile.primary_kpi if profile else "", block.display_name, ", ".join(block.metrics),
                        ("BLUMIA_REPORTING_GROUP_DATA_MISSING" if profile and client.id == "blumia" and len(profile.blocks) > 1 else
                         ("REPORTING_GROUP_DATA_MISSING" if profile and len(profile.blocks) > 1 else
                          ("OK" if profile else "REPORT_PROFILE_NOT_CONFIGURED"))),
                        "", client.active, client_report_category(client), client.report_position,
                    ])
            configured_groups = (reporting_groups_by_client_month or {}).get(
                (client.id, period.year, period.month), (reporting_groups_by_client or {}).get(client.id, [])
            )
            for group in configured_groups:
                enabled = enabled_platforms_for_group(group)
                for platform in REPORT_PLATFORMS:
                    if not enabled.get(platform, False):
                        continue
                    config_ws.append([
                        client.name, client.id, period.year, MONTH_NAMES[period.month - 1], group.name,
                        group.type, PLATFORM_LABELS[platform], "", "", "", "", group.active,
                        "CONFIGURADO" if profile else "GENERAL", profile.section if profile else "GENERAL",
                        profile.primary_kpi if profile else "", "", "", "OK", platform,
                        client.active, client_report_category(client), client.report_position,
                    ])
            if profile and len(profile.blocks) > 1:
                for block in profile.blocks:
                    block_groups = [group for group in configured_groups if block_for_group(profile, group.name) == block]
                    active_platforms = {
                        platform for group in block_groups if group.active
                        for platform, enabled in enabled_platforms_for_group(group).items() if enabled
                    }
                    state = (
                        "REPORT_PROFILE_DATA_SOURCE_NOT_AVAILABLE" if not block.platforms else
                        "BLUMIA_REPORTING_GROUP_DATA_MISSING" if client.id == "blumia" and not configured_groups else
                        "REPORTING_GROUP_DATA_MISSING" if not configured_groups else
                        "REPORTING_GROUP_NOT_CONFIGURED" if not block_groups else
                        "REPORTING_GROUP_HAS_NO_ENABLED_PLATFORMS" if not active_platforms else
                        "OK"
                    )
                    config_ws.append([
                        client.name, client.id, period.year, MONTH_NAMES[period.month - 1], "", "", "", "", "", "", "", True,
                        "CONFIGURADO", profile.section, profile.primary_kpi, block.display_name,
                        ", ".join(block.metrics), state, "", client.active, client_report_category(client), client.report_position,
                    ])
    config_ws.freeze_panes = "A2"
    config_ws.auto_filter.ref = config_ws.dimensions
    audit_ws = wb.create_sheet("AUDITORIA")
    audit_ws.append(["Cliente", "Bloque", "Plataforma", "Métrica", "Fuente", "Valor", "Periodo", "Grupo", "Categoría Excel", "Posición Excel", "Estado"])
    _style_header(audit_ws, 1, "44546A")

    for period in month_slices(start, end):
        ws = wb.create_sheet(period.label[:31])
        # El layout ancho lo determina la tabla publicitaria más amplia.
        layout_width = max(13, max([len(block.metrics) + 3 for profile in REPORT_PROFILES.values() for block in profile.blocks]))
        ws.sheet_view.showGridLines = False
        ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=layout_width)
        ws["A1"] = "REPORTE SEMANAL DE PERFORMANCE"
        ws["A1"].font = Font(name="Poppins", size=16, bold=True, color="FFFFFF")
        ws["A1"].fill = PatternFill("solid", fgColor="17365D")
        ws["A1"].alignment = Alignment(horizontal="center")
        ws.merge_cells(start_row=2, start_column=1, end_row=2, end_column=layout_width)
        ws["A2"] = f"Periodo: {period.start.isoformat()} al {period.end.isoformat()}"
        ws["A2"].alignment = Alignment(horizontal="center")
        # Los perfiles de clientes activos permanecen visibles aunque no tengan
        # filas para el periodo; los clientes inactivos no aparecen en reportes nuevos.
        renderable_clients = list(clients)
        all_sections = {client_report_category(c) for c in renderable_clients}
        sections = [item for item in ("ENTRETENIMIENTO", "B2B / CORPORATIVO", "ECOMMERCE", "GENERAL") if item in all_sections]
        sections.append("CLIENTES GENERAL")
        current = 4
        for section in sections[:-1]:
            ws.merge_cells(start_row=current, start_column=1, end_row=current, end_column=13)
            cell = ws.cell(current, 1, section)
            cell.fill = PatternFill("solid", fgColor=PROFILE_SECTION_FILL)
            cell.font = Font(name="Poppins", size=15, bold=True, color="000000")
            cell.alignment = Alignment(horizontal="center", vertical="center")
            current += 1
            ordered_clients = sorted(renderable_clients, key=client_report_sort_key)
            for client in ordered_clients:
                profile = report_profile(client.id)
                client_section = client_report_category(client)
                if client_section != section:
                    continue
                client_section_start = current
                client_lines = [line for line in report_lines if line.client_id == client.id and line.period.year == period.year and line.period.month == period.month]
                period_groups = (reporting_groups_by_client_month or {}).get(
                    (client.id, period.year, period.month), (reporting_groups_by_client or {}).get(client.id, [])
                )
                blocks = profile.blocks if profile else (ReportBlockProfile("general", client.name, REPORT_PLATFORMS, ("spend", "leads_or_conversions", "clicks", "impressions", "ctr")),)
                if profile and len(blocks) > 1 and not any(line.grouping_enabled for line in client_lines):
                    missing_group_code = "BLUMIA_REPORTING_GROUP_DATA_MISSING" if client.id == "blumia" else "REPORTING_GROUP_DATA_MISSING"
                    for missing_block in blocks:
                        audit_ws.append([
                            profile.display_name, missing_block.display_name, "", "Estado de asignación",
                            "Reporting group snapshot", missing_group_code, period.label, "",
                        ])
                for block in blocks:
                    candidates = [line for line in client_lines if line.platform in block.platforms]
                    if len(blocks) > 1:
                        if not period_groups:
                            # Mantiene explícitos los bloques históricos; sin grupos no replica totales.
                            candidates = []
                        else:
                            candidates = [line for line in candidates if block_for_group(profile, line.reporting_group_name) == block]
                    block_groups = [group for group in period_groups if profile and block_for_group(profile, group.name) == block]
                    group_config_missing = bool(profile and len(blocks) > 1 and not period_groups)
                    if profile and block.platforms == ():
                        audit_ws.append([client.id, block.display_name, "", "Estado de fuente", "Perfil de reporte", "REPORT_PROFILE_DATA_SOURCE_NOT_AVAILABLE", period.label, ""])
                    if len(blocks) > 1:
                        active_platforms = {
                            platform for group in block_groups if group.active
                            for platform, enabled in enabled_platforms_for_group(group).items() if enabled
                        }
                        assigned_platforms = [platform for platform in block.platforms if platform in active_platforms and account_ids(client, platform)]
                        if not block_groups:
                            audit_ws.append([
                                client.id, block.display_name, "", "Estado de grupo", "Snapshot mensual",
                                "REPORTING_GROUP_NOT_CONFIGURED", period.label, "",
                            ])
                        elif not assigned_platforms:
                            audit_ws.append([
                                client.id, block.display_name, "", "Plataformas activas", "Snapshot mensual",
                                "REPORTING_GROUP_HAS_NO_ENABLED_PLATFORMS", period.label,
                                ", ".join(group.name for group in block_groups),
                            ])
                    else:
                        assigned_platforms = [platform for platform in block.platforms if account_ids(client, platform)]
                    platforms_present = {line.platform for line in candidates}
                    missing_platforms = [platform for platform in assigned_platforms if platform not in platforms_present]
                    # Un bloque configurado siempre conserva su tabla. Cuando no
                    # hay filas, las filas de plataforma asignada muestran nulls;
                    # en bloques agrupados no se atribuye gasto sin asignación.
                    hidden = {normalize_metric_family(key) for key in client.hidden_metrics or []}
                    metrics_to_render = tuple(key for key in block.metrics if normalize_metric_family(key) not in hidden)
                    title = ws.cell(current, 1, block.display_name.upper())
                    title.fill = PatternFill("solid", fgColor=PROFILE_CLIENT_FILL)
                    title.font = Font(name="Poppins", size=10, bold=True, color="FFFFFF")
                    title.alignment = Alignment(horizontal="center", vertical="center")
                    ws.cell(current, 2, "Marketing Performance")
                    ws.cell(current, 2).font = Font(name="Poppins", size=9, color="FFFFFF")
                    ws.cell(current, 2).fill = PatternFill("solid", fgColor=PROFILE_CLIENT_FILL)
                    kpi_name = block.primary_kpi or (profile.primary_kpi if profile else "")
                    kpi_metric = {"cpa": "cost_per_result", "cpl": "cost_per_result", "ctr": "ctr", "roas": "roas"}.get(kpi_name, kpi_name)
                    kpi_values = _profile_metric_values(profile, block, candidates, stored, report_lines, data_lines) if profile else {}
                    ws.cell(current, 3, f"{kpi_name.upper()} REAL" if kpi_name else "")
                    ws.cell(current, 3).fill = PatternFill("solid", fgColor=PROFILE_KPI_FILL)
                    ws.cell(current, 3).font = Font(name="Poppins", size=10, bold=True)
                    ws.cell(current, 4, kpi_values.get(kpi_metric))
                    ws.cell(current, 4).number_format = RATIO_FORMAT if kpi_metric == "roas" else (PERCENT_FORMAT if kpi_metric == "ctr" else MONEY_FORMAT)
                    current += 1
                    headers = _profile_headers(profile, metrics_to_render) if profile else ["MES", "Medio", "Presupuesto", *[PROFILE_METRIC_LABELS[key] for key in metrics_to_render]]
                    header_row = current
                    for column, value in enumerate(headers, 1):
                        ws.cell(header_row, column, value)
                    current += 1
                    _style_header(ws, header_row, "D9D9D9")
                    for cell in ws[header_row]:
                        cell.font = Font(name="Poppins", size=10, bold=True, color="000000")
                        cell.fill = PatternFill("solid", fgColor=PROFILE_HEADER_FILL)
                        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
                        cell.border = Border(bottom=Side(style="thin", color="808080"))
                    ws.row_dimensions[header_row].height = min(60, max(42, 15 * max((str(value).count("\n") + 1 for value in headers), default=1)))
                    for index, metric in enumerate(metrics_to_render, 4):
                        if metric in {"cost_per_result", "roas"}:
                            ws.cell(header_row, index).fill = PatternFill("solid", fgColor="FFF2CC")
                    first_row = current
                    values_for_block: list[dict] = []
                    render_rows: list[tuple[ReportLine | None, str]] = [
                        (line, line.platform) for line in sorted(candidates, key=lambda item: REPORT_PLATFORMS.index(item.platform))
                    ]
                    render_rows.extend((None, platform) for platform in missing_platforms)
                    if not render_rows:
                        # Perfiles como el bloque histórico de LinkedIn de Clarvi
                        # conservan sus columnas aunque no haya una asignación activa.
                        render_rows = [(None, "")]
                    for line, row_platform in render_rows:
                        if line is None:
                            placeholder_label = (
                                PLATFORM_LABELS.get(row_platform, row_platform) if row_platform else
                                "Sin plataformas activas" if block_groups and not assigned_platforms else
                                "Grupo sin configurar" if len(blocks) > 1 and not block_groups else
                                "Sin datos"
                            )
                            row = [period.label, placeholder_label, "Presupuesto no capturado"]
                            row.extend(["—"] * len(metrics_to_render))
                            output_row = current
                            for column, value in enumerate(row, 1):
                                ws.cell(output_row, column, value)
                            ws.row_dimensions[output_row].height = 24
                            current += 1
                            continue
                        metrics = _profile_metric_values(profile or ReportProfile(client.id, client.name, "GENERAL", "", blocks), block, [line], stored, report_lines, data_lines)
                        key = _budget_key(line.client_id, line.platform, line.period.year, line.period.month, line.reporting_group_id)
                        budget = stored.get(key)
                        platform_label = PLATFORM_LABELS.get(line.platform, line.platform)
                        cap_row = next((index for index in range(2, budget_ws.max_row + 1)
                                        if budget_ws.cell(index, 1).value == line.client_id
                                        and budget_ws.cell(index, 4).value == platform_label
                                        and budget_ws.cell(index, 5).value == line.period.year
                                        and budget_ws.cell(index, 6).value == MONTH_NAMES[line.period.month - 1]
                                        and budget_ws.cell(index, 9).value != "CAMPAÑA"
                                        and budget_ws.cell(index, 12).value == line.reporting_group_id), None)
                        matching_campaigns = [item for item in data_lines if item.level == "CAMPAÑA" and item.client_id == line.client_id and item.platform == line.platform and item.period.year == line.period.year and item.period.month == line.period.month and item.reporting_group_id == line.reporting_group_id and item.campaign_id]
                        allocation_rows = [index for index in range(2, budget_ws.max_row + 1)
                                           if budget_ws.cell(index, 9).value == "CAMPAÑA"
                                           and any(budget_ws.cell(index, 1).value == item.client_id and budget_ws.cell(index, 4).value == platform_label and budget_ws.cell(index, 5).value == item.period.year and budget_ws.cell(index, 6).value == MONTH_NAMES[item.period.month - 1] and budget_ws.cell(index, 10).value == item.account_id and budget_ws.cell(index, 11).value == item.campaign_id for item in matching_campaigns)]
                        allocation_refs = ",".join(f"'PRESUPUESTOS'!$G${index}" for index in allocation_rows)
                        cap_ref = f"'PRESUPUESTOS'!$G${cap_row}" if cap_row else None
                        if cap_ref and allocation_refs:
                            budget_formula = f'=IF(ISNUMBER({cap_ref}),{cap_ref},IF(COUNT({allocation_refs})>0,SUM({allocation_refs}),"Presupuesto no capturado"))'
                        elif cap_ref:
                            budget_formula = f'=IF(ISNUMBER({cap_ref}),{cap_ref},"Presupuesto no capturado")'
                        elif allocation_refs:
                            budget_formula = f'=IF(COUNT({allocation_refs})>0,SUM({allocation_refs}),"Presupuesto no capturado")'
                        else:
                            budget_formula = '="Presupuesto no capturado"'
                        row = [period.label, platform_label, budget_formula]
                        for metric in metrics_to_render:
                            value = metrics.get(metric)
                            row.append(value if value is not None else "—")
                            audit_ws.append([client.id, block.display_name, platform_label, PROFILE_METRIC_LABELS[metric], "DashboardService.platform_block", value, period.label, line.reporting_group_name, client_report_category(client), client.report_position, "ACTIVO"])
                        output_row = current
                        for column, value in enumerate(row, 1):
                            ws.cell(output_row, column, value)
                        ws.row_dimensions[output_row].height = 24
                        metric_col = {name: 4 + index for index, name in enumerate(metrics_to_render)}
                        if "spend" in metric_col and "budget_difference" in metric_col:
                            spend_col = get_column_letter(metric_col["spend"])
                            ws.cell(output_row, metric_col["budget_difference"], f'=IF(ISNUMBER(C{output_row}),C{output_row}-{spend_col}{output_row},"")')
                        if "spend" in metric_col and "expected_spend" in metric_col:
                            spend_col = get_column_letter(metric_col["spend"])
                            ws.cell(output_row, metric_col["expected_spend"], f'=IF(ISNUMBER(C{output_row}),C{output_row}*{line.period.applicable_days}/{line.period.days_in_month},"")')
                        if "spend" in metric_col and "pacing_difference" in metric_col and "expected_spend" in metric_col:
                            expected_col = get_column_letter(metric_col["expected_spend"])
                            spend_col = get_column_letter(metric_col["spend"])
                            ws.cell(output_row, metric_col["pacing_difference"], f'=IF(AND(ISNUMBER({expected_col}{output_row}),ISNUMBER({spend_col}{output_row})),{expected_col}{output_row}-{spend_col}{output_row},"")')
                        current += 1
                    # La fila TOTAL se recalcula desde bases; el costo/ROAS nunca es promedio de filas.
                    if len(candidates) > 1:
                        totals = _profile_metric_values(profile or ReportProfile(client.id, client.name, "GENERAL", "", blocks), block, candidates, stored, report_lines, data_lines)
                        ws.cell(current, 1, period.label)
                        ws.cell(current, 2, f"TOTAL {block.display_name}")
                        effective_budgets = [_effective_budget(item, stored, data_lines) for item in candidates]
                        ws.cell(current, 3, sum(item.budget for item in effective_budgets if item is not None) if effective_budgets and all(item is not None for item in effective_budgets) and len({item.currency for item in effective_budgets if item is not None}) == 1 else "Presupuesto no capturado")
                        for pos, metric in enumerate(metrics_to_render, 4):
                            ws.cell(current, pos, totals.get(metric))
                        for metric, value in totals.items():
                            if metric in PROFILE_METRIC_LABELS:
                                audit_ws.append([client.id, block.display_name, "TOTAL", PROFILE_METRIC_LABELS[metric], "Suma desde bases/dashboard KPI", value, period.label, "", client_report_category(client), client.report_position, "ACTIVO"])
                    for cell in ws[current]:
                        cell.font = Font(bold=True)
                        cell.fill = PatternFill("solid", fgColor="E2F0D9")
                    ws.row_dimensions[current].height = 25
                    current += 1
                    last_detail_row = current - 1
                    for row_index in range(first_row, last_detail_row + 1):
                        ws.cell(row_index, 3).number_format = MONEY_FORMAT
                        for index, metric in enumerate(metrics_to_render, 4):
                            if metric in {"spend", "budget_difference", "expected_spend", "pacing_difference", "conversion_value", "ticket_average", "cost_per_result", "cpc", "cpm"}:
                                ws.cell(row_index, index).number_format = MONEY_FORMAT
                            elif metric == "roas":
                                ws.cell(row_index, index).number_format = RATIO_FORMAT
                            elif metric == "ctr":
                                ws.cell(row_index, index).number_format = PERCENT_FORMAT
                            else:
                                ws.cell(row_index, index).number_format = NUMBER_FORMAT
                    current += 1
                    source_unavailable = bool(block.platforms == () or any(line.status.casefold() not in {"ok", "snapshot"} for line in candidates))
                    if source_unavailable or group_config_missing or (not candidates and assigned_platforms):
                        note = (
                            "Fuente no disponible" if source_unavailable else
                            ("Configuración de campañas pendiente" if group_config_missing else "Sin datos para el periodo")
                        )
                        ws.merge_cells(start_row=current, start_column=1, end_row=current, end_column=max(4, len(headers)))
                        note_cell = ws.cell(current, 1, note)
                        note_cell.font = Font(name="Poppins", size=9, italic=True, color="7F6000")
                        note_cell.alignment = Alignment(horizontal="left", vertical="center")
                        current += 1
                    elif len(blocks) > 1 and not block_groups:
                        ws.merge_cells(start_row=current, start_column=1, end_row=current, end_column=max(4, len(headers)))
                        note_cell = ws.cell(current, 1, "Grupo no configurado para este periodo")
                        note_cell.font = Font(name="Poppins", size=9, italic=True, color="7F6000")
                        note_cell.alignment = Alignment(horizontal="left", vertical="center")
                        current += 1
                    elif block_groups and not assigned_platforms:
                        ws.merge_cells(start_row=current, start_column=1, end_row=current, end_column=max(4, len(headers)))
                        note_cell = ws.cell(current, 1, "Sin plataformas activas en este grupo")
                        note_cell.font = Font(name="Poppins", size=9, italic=True, color="7F6000")
                        note_cell.alignment = Alignment(horizontal="left", vertical="center")
                        current += 1
                if profile and profile.uses_woosync:
                    # WooSync queda en una sección independiente bajo el bloque Ads.
                    current += 3
                    woo_row = current
                    ws.merge_cells(start_row=woo_row, start_column=1, end_row=woo_row, end_column=3)
                    cell = ws.cell(woo_row, 1, "SSIENTO - VENTAS WOOSYNC")
                    cell.fill = PatternFill("solid", fgColor=PROFILE_CLIENT_FILL)
                    cell.font = Font(name="Poppins", size=10, bold=True, color="FFFFFF")
                    cell.alignment = Alignment(horizontal="center", vertical="center")
                    ws.row_dimensions[woo_row].height = 25
                    for column, value in enumerate(("Sede", "Pedidos", "Monto de venta"), 1):
                        cell = ws.cell(woo_row + 1, column, value)
                        cell.fill = PatternFill("solid", fgColor=PROFILE_HEADER_FILL)
                        cell.font = Font(name="Poppins", size=10, bold=True)
                        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
                        cell.border = Border(
                            left=Side(style="medium" if column == 1 else "thin", color="000000"),
                            right=Side(style="medium" if column == 3 else "thin", color="000000"),
                            top=Side(style="medium", color="000000"), bottom=Side(style="thin", color="000000"),
                        )
                    ws.row_dimensions[woo_row + 1].height = 40
                    woosync = _validated_woosync_sales((woosync_data or {}).get(period.label))
                    if woosync:
                        report_period = woosync["applied_filters"].get("from")
                        report_end = woosync["applied_filters"].get("to")
                        audit_period = f"{report_period} → {report_end}" if report_period and report_end else period.label
                        body_start = woo_row + 2
                        for offset, branch in enumerate(woosync["branch_sales"]):
                            row_index = body_start + offset
                            _write_woosync_branch_row(ws, row_index, branch, total=False)
                            audit_ws.append([profile.display_name, "ECOMMERCE", "WooSync", "orders_count",
                                            "WooSync sales_overview filtrado por sede", branch["orders_count"], audit_period, branch["branch_name"]])
                            audit_ws.append([profile.display_name, "ECOMMERCE", "WooSync", "total_sales",
                                            "WooSync sales_overview filtrado por sede", branch["total_sales"], audit_period, branch["branch_name"]])
                        total_row = body_start + len(woosync["branch_sales"])
                        _write_woosync_branch_row(ws, total_row, {
                            "branch_name": "TOTAL", "orders_count": woosync["orders_count"],
                            "total_sales": woosync["total_sales"],
                        }, total=True)
                        ticket_row = total_row + 1
                        ws.cell(ticket_row, 1, "Ticket promedio")
                        ws.cell(ticket_row, 3, woosync["ticket_average"])
                        _style_woosync_body_cells(ws, ticket_row, total=True)
                        ws.cell(ticket_row, 3).number_format = MONEY_FORMAT
                        ws.row_dimensions[ticket_row].height = 24
                        for metric, value, source in (
                            ("orders_count", woosync["orders_count"], "WooSync sales_overview.orders_count"),
                            ("total_sales", woosync["total_sales"], "WooSync sales_overview.total_sales"),
                            ("average_order_value", woosync["ticket_average"], "WooSync total_sales / orders_count"),
                        ):
                            audit_ws.append([profile.display_name, "ECOMMERCE", "WooSync", metric, source, value, audit_period, "TOTAL"])
                        attribution_note = "Sin atribución por canal disponible en WooSync."
                    else:
                        ws.cell(woo_row + 2, 1, "Fuente no disponible")
                        _style_woosync_body_cells(ws, woo_row + 2)
                        ws.row_dimensions[woo_row + 2].height = 24
                        attribution_note = "Sin atribución por canal disponible en WooSync."
                    note_row = (woo_row + 2 + len(woosync["branch_sales"]) + 3) if woosync else woo_row + 3
                    ws.merge_cells(start_row=note_row, start_column=1, end_row=note_row, end_column=3)
                    note_cell = ws.cell(note_row, 1, attribution_note)
                    note_cell.font = Font(italic=True, color="7F6000", size=9)
                    note_cell.alignment = Alignment(horizontal="left", vertical="center")
                    ws.row_dimensions[note_row].height = 22
                    current = note_row + 2
        # Resumen KPI con el KPI declarado en el perfil; sin valor inventado.
        current += 1
        ws.merge_cells(start_row=current, start_column=1, end_row=current, end_column=13)
        ws.cell(current, 1, "CLIENTES GENERAL")
        ws.cell(current, 1).fill = PatternFill("solid", fgColor=PROFILE_SECTION_FILL)
        ws.cell(current, 1).font = Font(name="Poppins", size=15, bold=True)
        ws.cell(current, 1).alignment = Alignment(horizontal="center", vertical="center")
        current += 1
        for column, value in enumerate(["CLIENTE", "KPI", "VALOR", "PERFIL", "ESTADO"], 1):
            ws.cell(current, column, value)
        _style_header(ws, current, "D9D9D9")
        current += 1
        ordered_clients = sorted(renderable_clients, key=client_report_sort_key)
        for client in ordered_clients:
            profile = report_profile(client.id)
            lines = [line for line in report_lines if line.client_id == client.id and line.period.year == period.year and line.period.month == period.month]
            if profile and lines:
                block = profile.blocks[0]
                if len(profile.blocks) > 1:
                    if any(line.grouping_enabled for line in lines):
                        lines = [line for line in lines if block_for_group(profile, line.reporting_group_name) == block]
                    else:
                        lines = []
                values = _profile_metric_values(profile, block, lines, stored, report_lines, data_lines) if lines else {}
                metric = {"cpa": "cost_per_result", "cpl": "cost_per_result", "ctr": "ctr", "roas": "roas"}.get(profile.primary_kpi, profile.primary_kpi)
                value = values.get(metric)
                if value is not None:
                    status = "OK"
                elif len(profile.blocks) > 1 and not any(line.grouping_enabled for line in lines):
                    status = "CONFIGURACION_PENDIENTE"
                elif lines and any(line.status.casefold() not in {"ok", "snapshot"} for line in lines):
                    status = "FUENTE_NO_DISPONIBLE"
                elif lines and metric == "cost_per_result" and any(_profile_result(profile, line)[0] is None for line in lines):
                    status = "FUENTE_NO_DISPONIBLE"
                elif lines and metric == "ctr" and any(line.clicks is None or line.impressions is None for line in lines):
                    status = "FUENTE_NO_DISPONIBLE"
                elif lines and metric == "roas" and any(line.conversion_value is None or line.spend is None for line in lines):
                    status = "FUENTE_NO_DISPONIBLE"
                else:
                    status = "SIN_DATOS_PERIODO"
                kpi_name = profile.primary_kpi.upper()
            else:
                value = None
                status = "REPORT_PROFILE_NOT_CONFIGURED" if profile is None else "SIN_DATOS_PERIODO"
                kpi_name = profile.primary_kpi.upper() if profile else "GENERAL"
            for column, cell_value in enumerate([profile.display_name if profile else client.name, kpi_name, value, client_report_category(client), status], 1):
                ws.cell(current, column, "—" if column == 3 and cell_value is None else cell_value)
            ws.cell(current, 3).fill = PatternFill("solid", fgColor="FFFF00")
            ws.cell(current, 3).number_format = RATIO_FORMAT if profile and profile.primary_kpi == "roas" else MONEY_FORMAT
            current += 1
        ws.freeze_panes = "A5"
        for col in range(1, ws.max_column + 1):
            header_values = []
            for row in range(1, ws.max_row + 1):
                if ([ws.cell(row, index).value for index in (1, 2, 3)] == ["MES", "Medio", "Presupuesto"] or
                    [ws.cell(row, index).value for index in (1, 2, 3)] == ["Sede", "Pedidos", "Monto de venta"] or
                    [ws.cell(row, index).value for index in (1, 2, 3)] == ["CLIENTE", "KPI", "VALOR"]):
                    value = ws.cell(row, col).value
                    if value is not None:
                        header_values.append(str(value))
            best = max((PROFILE_COLUMN_WIDTHS.get(value, min(28, max(14, len(value) + 3))) for value in header_values), default=16)
            ws.column_dimensions[get_column_letter(col)].width = best
        for row in ws.iter_rows():
            for cell in row:
                if cell.value is not None:
                    cell.alignment = Alignment(
                        horizontal=cell.alignment.horizontal, vertical="center", wrap_text=True,
                    )
            if row and any(cell.value is not None for cell in row) and ws.row_dimensions[row[0].row].height is None:
                ws.row_dimensions[row[0].row].height = 23

    client_by_id = {client.id: client for client in clients}
    for line in data_lines:
        if line.platform != "pinterest_ads":
            continue
        click_cost = line.spend / line.clicks if line.spend is not None and line.clicks not in (None, 0) else None
        ctr = line.clicks / line.impressions if line.clicks is not None and line.impressions not in (None, 0) else None
        roas = line.conversion_value / line.spend if line.conversion_value is not None and line.spend not in (None, 0) else None
        for metric, value, source in (
            ("spend", line.spend, "PinterestAdsProvider"),
            ("impressions", line.impressions, "PinterestAdsProvider"),
            ("clicks", line.clicks, "PinterestAdsProvider"),
            ("CTR", ctr, "clicks / impressions"),
            ("CPC", click_cost, "spend / clicks"),
            ("checkouts", line.checkouts, "PinterestAdsProvider.TOTAL_CHECKOUT"),
            ("conversion_value", line.conversion_value, "PinterestAdsProvider.TOTAL_CHECKOUT_VALUE_IN_MICRO_DOLLAR"),
            ("ROAS", roas, "conversion_value / spend"),
        ):
            client = client_by_id.get(line.client_id)
            audit_ws.append([
                line.client_name, "Pinterest Ads", "Pinterest Ads", metric, source, value,
                f"{line.period.start} → {line.period.end}", line.reporting_group_name,
                client_report_category(client) if client else "GENERAL",
                client.report_position if client else None,
                "ACTIVO" if client and client.active else "INACTIVO",
            ])

    monthly_names = [period.label[:31] for period in month_slices(start, end)]
    auxiliary_order = [name for name in ("PRESUPUESTOS", "DATOS", "CONFIGURACION", "AUDITORIA") if name in wb.sheetnames]
    wb._sheets = [wb[name] for name in monthly_names + auxiliary_order]
    wb.active = 0
    return wb


def expected_accounts_for_clients(clients: list[Client]) -> dict[tuple[str, str], set[str]]:
    """Cuentas autorizadas del XLSX derivadas de las fuentes persistidas del cliente.

    Esta lista solo gobierna la validación de pertenencia de las filas exportadas;
    no amplía el acceso del provider ni acepta cuentas no vinculadas.
    """
    platforms = (*REPORT_PLATFORMS, "pinterest_ads")
    return {
        (client.id, platform): set(account_ids(client, platform))
        for client in clients
        for platform in platforms
    }


def _validate_workbook(content: bytes, expected_accounts: dict[tuple[str, str], set[str]]) -> None:
    workbook = load_workbook(BytesIO(content), data_only=False)
    if not workbook.sheetnames or not any(len(name.split()) > 1 and name.split()[-1].isdigit() and len(name.split()[-1]) == 4 for name in workbook.sheetnames):
        raise ReportValidationError("El reporte no contiene hojas mensuales.")
    if not {"PRESUPUESTOS", "DATOS", "CONFIGURACION", "AUDITORIA"}.issubset(workbook.sheetnames):
        raise ReportValidationError("La estructura de hojas del reporte es inválida.")
    forbidden = ("#REF!", "#DIV/0!", "#VALUE!", "#N/A")
    for sheet in workbook.worksheets:
        for row in sheet.iter_rows():
            for cell in row:
                if isinstance(cell.value, str) and (
                    any(token in cell.value for token in forbidden)
                    or cell.value.strip().casefold() in {"nan", "infinity", "-infinity"}
                ):
                    raise ReportValidationError(f"Se detectó una referencia inválida en {sheet.title}!{cell.coordinate}.")
    budget_ws = workbook["PRESUPUESTOS"]
    for row in range(2, budget_ws.max_row + 1):
        value = budget_ws.cell(row, 7).value
        if value is not None and not isinstance(value, (int, float)):
            raise ReportValidationError("Los presupuestos deben ser numéricos.")
    data_ws = workbook["DATOS"]
    for row in range(2, data_ws.max_row + 1):
        if data_ws.cell(row, 9).value != "CAMPAÑA":
            continue
        client_id = str(data_ws.cell(row, 4).value)
        platform_label = str(data_ws.cell(row, 6).value)
        platform = next((key for key, label in PLATFORM_LABELS.items() if label == platform_label), None)
        account = str(data_ws.cell(row, 7).value)
        if platform is None or account not in expected_accounts.get((client_id, platform), set()):
            raise ReportValidationError("El reporte contiene una cuenta ajena al cliente.")


def generate_performance_report(
    service,
    clients: list[Client],
    budget_repository: BudgetRepository,
    start: date,
    end: date,
    owners: dict,
    reporting_groups_by_client: dict[str, list[ReportingGroup]] | None = None,
    reporting_groups_by_client_month: dict[tuple[str, int, int], list[ReportingGroup]] | None = None,
    woosync_data: dict[str, dict] | None = None,
) -> GeneratedReport:
    report_lines, data_lines = collect_report_lines(
        service, clients, start, end, owners,
        reporting_groups_by_client=reporting_groups_by_client,
        reporting_groups_by_client_month=reporting_groups_by_client_month,
    )
    if not report_lines:
        raise ReportValidationError("No hay clientes activos con plataformas publicitarias asignadas.")
    workbook = _build_profile_workbook(
        report_lines, data_lines, budget_repository.list(), clients, start, end, woosync_data,
        reporting_groups_by_client, reporting_groups_by_client_month,
    )
    buffer = BytesIO()
    workbook.save(buffer)
    content = buffer.getvalue()
    _validate_workbook(content, expected_accounts_for_clients(clients))
    return GeneratedReport(
        content=content,
        filename=f"Reporte_Performance_{start.isoformat()}_al_{end.isoformat()}.xlsx",
    )

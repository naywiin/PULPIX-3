"""Genera XLSX post-cutover consultando la API productiva en modo solo lectura.

El script no escribe en la aplicación. Solo descarga JSON de dashboard,
presupuestos y snapshots para el periodo solicitado y guarda el XLSX local.
"""
from __future__ import annotations

import os
import sys
from collections import defaultdict
from datetime import date
from math import isfinite
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from app.budgets import MonthlyBudget  # noqa: E402
from app.models import Client, account_ids  # noqa: E402
from app.reporting import (  # noqa: E402
    DataLine, ReportLine, _build_profile_workbook, _validate_workbook, month_slices,
)
from app.report_profiles import REPORT_PROFILES  # noqa: E402
from app.woosync import ALL_BRANCHES, WOOSYNC_BRANCHES  # noqa: E402

API_ROOT = os.environ.get("MP_DASHBOARD_API_ROOT", "https://marketing-performance-tuzz23i7ka-uc.a.run.app/api").rstrip("/")
START = date(2026, 10, 1)
END = date(2026, 10, 5)
TARGET = ROOT / ".artifacts/Reporte_Marketing_Performance_POST_CUTOVER.xlsx"
PLATFORMS = ("google_ads", "meta_ads", "spotify_ads")


def _number(row: dict, key: str):
    value = row.get(key)
    if value is None or isinstance(value, bool):
        return None
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    return value if isfinite(value) else None


def _get(session: requests.Session, path: str, params: dict | None = None) -> dict:
    response = session.get(f"{API_ROOT}{path}", params=params, timeout=120)
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict):
        raise RuntimeError(f"Respuesta inesperada de {path}")
    return payload


def _data_line(client: Client, period, platform: str, block: dict, raw: dict,
               group_by_key: dict, group_configured: bool) -> DataLine:
    account_id = str(raw.get("source_account_id") or block.get("account_id") or "")
    campaign_id = str(raw.get("campaign_id") or "") or None
    campaign_group = group_by_key.get((platform, account_id, campaign_id)) if campaign_id else None
    return DataLine(
        period=period, client_id=client.id, client_name=client.name, platform=platform,
        account_id=account_id, account_name=raw.get("source_account_name"), level="CAMPAÑA",
        campaign_id=campaign_id, campaign_name=raw.get("campaign_name"), objective=raw.get("objective"),
        spend=_number(raw, "spend"), conversions=_number(raw, "conversions"),
        # A conversion count is not reinterpreted as a lead or purchase.
        leads=_number(raw, "leads"), purchases=_number(raw, "purchases"),
        conversion_value=_number(raw, "conversion_value"), clicks=_number(raw, "clicks"),
        impressions=_number(raw, "impressions"), reach=_number(raw, "reach"),
        currency=str(raw.get("currency") or block.get("currency") or client.currency),
        status=str(block.get("status") or "error"),
        reporting_group_id=campaign_group.get("id") if campaign_group else None,
        reporting_group_name=campaign_group.get("name") if campaign_group else None,
        reporting_group_type=campaign_group.get("type") if campaign_group else None,
        grouping_enabled=group_configured,
        configured_group_id=campaign_group.get("id") if campaign_group else None,
        configured_group_name=campaign_group.get("name") if campaign_group else None,
        configured_group_type=campaign_group.get("type") if campaign_group else None,
        configured_group_active=campaign_group.get("active") if campaign_group else None,
    )


def _sum_nullable(rows: list[DataLine], field: str):
    values = [getattr(row, field) for row in rows]
    return sum(values) if values and all(value is not None for value in values) else None


def main() -> None:
    session = requests.Session()
    clients_payload = _get(session, "/clients")
    clients = [Client.model_validate(item) for item in clients_payload.get("clients", [])]
    periods = month_slices(START, END)
    period = periods[0]
    dashboard_payloads = {}
    for client in clients:
        dashboard_payloads[client.id] = _get(
            session, f"/dashboard/{client.id}",
            {"start_date": START.isoformat(), "end_date": END.isoformat(), "refresh": "true"},
        )

    budgets = [MonthlyBudget.model_validate(item) for item in _get(session, "/budgets").get("budgets", [])]
    snapshots = {}
    for client in clients:
        profile = REPORT_PROFILES.get(client.id)
        if not profile or len(profile.blocks) < 2:
            continue
        snapshots[client.id] = _get(session, f"/reporting-group-snapshots/{client.id}/2026/10")

    report_lines: list[ReportLine] = []
    data_lines: list[DataLine] = []
    dashboard_diagnostics = []
    for client in clients:
        payload = dashboard_payloads.get(client.id)
        if payload is None:
            continue
        profile = REPORT_PROFILES.get(client.id)
        platform_payloads = payload.get("platforms", {})
        snapshot = snapshots.get(client.id, {})
        groups_by_id = {str(group.get("id")): group for group in snapshot.get("groups", [])}
        group_by_key = {}
        for assignment in snapshot.get("assignments", []):
            group = groups_by_id.get(str(assignment.get("reporting_group_id")))
            if group:
                group_by_key[(assignment.get("platform"), str(assignment.get("account_id")),
                              str(assignment.get("campaign_id")))] = group
        group_configured = snapshot.get("state") == "configured" and any(
            assignment.get("reporting_group_id") for assignment in snapshot.get("assignments", [])
        )
        client_campaign_rows: list[DataLine] = []
        for platform in PLATFORMS:
            allowed = account_ids(client, platform)
            if not allowed:
                continue
            block = platform_payloads.get(platform, {})
            current = block.get("current") if isinstance(block.get("current"), dict) else {}
            totals = current.get("totals") if isinstance(current.get("totals"), dict) else {}
            campaigns = current.get("campaigns") if isinstance(current.get("campaigns"), list) else []
            rows = [_data_line(client, period, platform, block, raw, group_by_key, group_configured) for raw in campaigns]
            client_campaign_rows.extend(rows)
            if not profile or len(profile.blocks) <= 1:
                report_lines.append(ReportLine(
                    period=period, client_id=client.id, client_name=client.name, platform=platform,
                    account_ids=list(allowed), currency=str(block.get("currency") or client.currency),
                    status=str(block.get("status") or "error"), spend=_number(totals, "spend"),
                    conversions=_number(totals, "conversions"), leads=_number(totals, "leads"),
                    purchases=_number(totals, "purchases"), conversion_value=_number(totals, "conversion_value"),
                    clicks=_number(totals, "clicks"), impressions=_number(totals, "impressions"),
                    reach=_number(totals, "reach"), reach_is_sum=bool(current.get("reach_is_sum")),
                    hidden_metrics=tuple(client.hidden_metrics or []),
                ))
            dashboard_diagnostics.append({
                "client_id": client.id, "platform": platform, "status": block.get("status"),
                "campaign_rows": len(campaigns), "spend": _number(totals, "spend"),
                "conversions": _number(totals, "conversions"),
                "leads": _number(totals, "leads"), "purchases": _number(totals, "purchases"),
            })
        data_lines.extend(client_campaign_rows)

        if profile and len(profile.blocks) > 1 and group_configured:
            # Agrega campañas asignadas por grupo. Los grupos configurados sin
            # filas de campañas reciben una línea null para conservar la tabla.
            assigned_groups = {}
            for assignment in snapshot.get("assignments", []):
                gid = assignment.get("reporting_group_id")
                group = groups_by_id.get(str(gid))
                if group:
                    assigned_groups.setdefault((str(gid), assignment.get("platform")), group)
            keys = {(row.reporting_group_id, row.platform) for row in client_campaign_rows if row.reporting_group_id}
            keys.update(assigned_groups)
            for group_id, platform in keys:
                matched = [row for row in client_campaign_rows if row.reporting_group_id == group_id and row.platform == platform]
                group = groups_by_id.get(str(group_id))
                if not group:
                    continue
                if matched:
                    source_ids = sorted({account for row in matched for account in [row.account_id] if account})
                    status = next((str(platform_payloads.get(platform, {}).get("status") or "error") for _ in [0]), "error")
                    totals = {
                        field: _sum_nullable(matched, field)
                        for field in ("spend", "conversions", "leads", "purchases", "conversion_value", "clicks", "impressions", "reach")
                    }
                    report_lines.append(ReportLine(
                        period=period, client_id=client.id, client_name=client.name, platform=platform,
                        account_ids=source_ids, currency=matched[0].currency, status=status,
                        spend=totals["spend"], conversions=totals["conversions"], leads=totals["leads"],
                        purchases=totals["purchases"], conversion_value=totals["conversion_value"],
                        clicks=totals["clicks"], impressions=totals["impressions"], reach=totals["reach"],
                        reach_is_sum=True, reporting_group_id=str(group_id), reporting_group_name=group["name"],
                        reporting_group_type=group.get("type"), grouping_enabled=True,
                    ))
                else:
                    assigned = [item for item in snapshot.get("assignments", [])
                                if str(item.get("reporting_group_id")) == str(group_id) and item.get("platform") == platform]
                    report_lines.append(ReportLine(
                        period=period, client_id=client.id, client_name=client.name, platform=platform,
                        account_ids=sorted({str(item["account_id"]) for item in assigned}),
                        currency=client.currency, status="ok", spend=None, conversions=None, leads=None,
                        purchases=None, conversion_value=None, clicks=None, impressions=None, reach=None,
                        reach_is_sum=False, reporting_group_id=str(group_id), reporting_group_name=group["name"],
                        reporting_group_type=group.get("type"), grouping_enabled=True,
                    ))
        elif profile and len(profile.blocks) > 1:
            # El dashboard devuelve datos generales, pero no se asignan a los
            # bloques sin snapshot mensual confirmado.
            for platform in PLATFORMS:
                if not account_ids(client, platform):
                    continue
                block = platform_payloads.get(platform, {})
                totals = (block.get("current") or {}).get("totals") or {}
                report_lines.append(ReportLine(
                    period=period, client_id=client.id, client_name=client.name, platform=platform,
                    account_ids=account_ids(client, platform), currency=str(block.get("currency") or client.currency),
                    status=str(block.get("status") or "error"), spend=_number(totals, "spend"),
                    conversions=_number(totals, "conversions"), leads=_number(totals, "leads"),
                    purchases=_number(totals, "purchases"), conversion_value=_number(totals, "conversion_value"),
                    clicks=_number(totals, "clicks"), impressions=_number(totals, "impressions"),
                    reach=_number(totals, "reach"), reach_is_sum=bool((block.get("current") or {}).get("reach_is_sum")),
                    grouping_enabled=False, hidden_metrics=tuple(client.hidden_metrics or []),
                ))

    # Consultar el agregado y cada sede desde la misma API productiva; no usar
    # ventas globales como atribución por canal ni reutilizar un fixture local.
    woosync_payload = _get(session, "/woosync/overview", {
        "from": START.isoformat(), "to": END.isoformat(),
    })
    woosync_payload["period"] = {"from": START.isoformat(), "to": END.isoformat()}
    woosync_payload["branch_sales"] = []
    for branch_id in ALL_BRANCHES:
        branch_payload = _get(session, "/woosync/overview", {
            "from": START.isoformat(), "to": END.isoformat(), "branches": branch_id,
        })
        branch_overview = branch_payload.get("sales_overview") or {}
        branch_filters = branch_payload.get("applied_filters") or {}
        woosync_payload["branch_sales"].append({
            "branch_id": branch_id,
            "branch_name": WOOSYNC_BRANCHES[branch_id],
            "orders_count": branch_overview.get("orders_count"),
            "total_sales": branch_overview.get("total_sales"),
            "applied_filters": branch_filters,
        })
    woosync_payload["channel_attribution_available"] = False
    woosync_data = {"Octubre 2026": woosync_payload}
    workbook = _build_profile_workbook(report_lines, data_lines, budgets, clients, START, END, woosync_data)
    TARGET.parent.mkdir(parents=True, exist_ok=True)
    workbook.save(TARGET)
    expected = {(client.id, platform): set(account_ids(client, platform))
                for client in clients for platform in PLATFORMS}
    _validate_workbook(TARGET.read_bytes(), expected)
    print(f"Generado {TARGET}; clientes consultados={len(dashboard_payloads)}; "
          f"líneas de plataforma={len(report_lines)}; campañas={len(data_lines)}; presupuestos={len(budgets)}")
    for item in dashboard_diagnostics:
        print("DASHBOARD", item)
    for client_id, snapshot in snapshots.items():
        print("SNAPSHOT", client_id, snapshot.get("state"),
              "grupos=", len(snapshot.get("groups", [])), "asignaciones=", len(snapshot.get("assignments", [])),
              "asignaciones_con_grupo=", sum(bool(item.get("reporting_group_id")) for item in snapshot.get("assignments", [])))


if __name__ == "__main__":
    main()

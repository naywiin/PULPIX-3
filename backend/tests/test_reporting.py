"""Presupuestos persistentes y XLSX sobre la misma capa de datos del dashboard."""
from __future__ import annotations

from datetime import date
from io import BytesIO
from pathlib import Path

from fastapi.testclient import TestClient
from openpyxl import load_workbook

import pytest

from app.budgets import (
    BudgetStoreError,
    FirestoreBudgetRepository,
    MemoryBudgetRepository,
    MonthlyBudget,
    create_budget_repository,
)
from app.config import Settings
from app.main import create_app
from app.metrics import derive
from app.models import ClientCreate
from app.providers.meta import MetaProvider
from app.registry import JsonClientRepository
from app.reporting import (
    PACING_GREEN_MAX,
    PACING_YELLOW_MAX,
    KPI_EXPRESSIONS,
    REPORT_COLUMNS,
    _formulae,
    build_performance_summary,
    calculate_performance_kpis,
    month_slices,
    _summary_record,
    expected_accounts_for_clients,
    MonthSlice,
    ReportLine,
    DataLine,
)
from app.reporting_groups import MemoryReportingGroupRepository, MemoryReportingGroupSnapshotRepository


class FakeGoogleAds:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, str]] = []

    def performance(self, customer_id, login_customer_id, start, end, campaign_ids=None):
        del login_customer_id, campaign_ids
        self.calls.append((str(customer_id), start, end))
        days = (date.fromisoformat(end) - date.fromisoformat(start)).days + 1
        factor = 1 if str(customer_id).endswith("1") else 2
        base = {
            "spend": float(days * 100 * factor),
            "conversions": float(days * factor),
            "conversion_value": float(days * 250 * factor),
            "clicks": days * 10 * factor,
            "impressions": days * 1000 * factor,
            "reach": days * 700 * factor,
        }
        campaign = {
            **derive(base),
            "campaign_id": f"campaign-{customer_id}",
            "campaign_name": f"Campaña {customer_id}",
            "objective": "LEADS",
        }
        return {
            "account_name": f"Cuenta {customer_id}",
            "currency": "MXN",
            "totals": derive(base),
            "campaigns": [campaign],
            "daily": [],
        }

    def list_campaigns(self, customer_id, login_customer_id=None):
        del customer_id, login_customer_id
        return []


class FakeMetaAds:
    def performance(self, account_id, start, end, campaign_ids=None):
        del campaign_ids
        days = (date.fromisoformat(end) - date.fromisoformat(start)).days + 1
        base = {
            "spend": days * 200.0,
            "conversions": days * 2.0,
            "leads": days * 2.0,
            "conversion_value": days * 400.0,
            "clicks": days * 15,
            "impressions": days * 2000,
            "reach": days * 900,
        }
        campaign = {
            **derive(base),
            "campaign_id": f"meta-campaign-{account_id}",
            "campaign_name": f"Meta {account_id}",
            "objective": "LEADS",
        }
        return {"currency": "MXN", "account_name": f"Meta {account_id}", "totals": derive(base), "campaigns": [campaign], "daily": []}

    def adsets(self, account_id, start, end, campaign_ids=None, adset_ids=None):
        del account_id, start, end, campaign_ids, adset_ids
        return []


class AssignmentAwareGoogleAds:
    campaigns = {
        "11": {"spend": 100.0, "conversions": 2.0, "conversion_value": 300.0, "clicks": 10, "impressions": 100},
        "22": {"spend": 200.0, "conversions": 4.0, "conversion_value": 800.0, "clicks": 20, "impressions": 200},
    }

    def __init__(self):
        self.calls = []

    def list_campaigns(self, customer_id, login_customer_id=None):
        del login_customer_id
        return [{"campaign_id": campaign_id, "campaign_name": f"Campaña {campaign_id}"} for campaign_id in self.campaigns]

    def performance(self, customer_id, login_customer_id, start, end, campaign_ids=None):
        del login_customer_id
        selected = list(campaign_ids) if campaign_ids is not None else list(self.campaigns)
        self.calls.append((customer_id, start, tuple(selected)))
        rows = [{
            **derive(self.campaigns[campaign_id]),
            "campaign_id": campaign_id,
            "campaign_name": f"Campaña {campaign_id}",
        } for campaign_id in selected]
        totals = {key: sum(row.get(key, 0) for row in rows) for key in self.campaigns["11"]}
        return {"currency": "MXN", "totals": derive(totals), "campaigns": rows, "daily": []}


def report_api(tmp_path: Path, group_repository=None, snapshot_repository=None):
    settings = Settings(clients_file=tmp_path / "clients.json", static_dir=tmp_path / "nodist")
    repo = JsonClientRepository(settings.clients_file)
    repo.create(ClientCreate(name="Alpha", currency="MXN", google_ads_sources=[{"customer_id": "1001"}]))
    repo.create(ClientCreate(name="Beta", currency="MXN", google_ads_sources=[{"customer_id": "2002"}]))
    provider = FakeGoogleAds()
    budgets = MemoryBudgetRepository([
        MonthlyBudget(client_id="alpha", platform="google_ads", year=2026, month=8, budget=31000, currency="MXN"),
        MonthlyBudget(client_id="alpha", platform="google_ads", year=2026, month=9, budget=30000, currency="MXN"),
    ])
    app = create_app(settings, repo, {"google_ads": provider}, budgets, group_repository, snapshot_repository)
    return TestClient(app), provider, budgets


def test_budget_endpoint_only_accepts_budget_and_upserts_unique_key(tmp_path):
    client, _, _ = report_api(tmp_path)
    path = "/api/budgets/alpha/google_ads/2026/9"
    assert client.put(path, json={"budget": 30000}).status_code == 200
    saved = client.put(path, json={"budget": 32000}).json()
    assert saved == {
        "client_id": "alpha", "platform": "google_ads", "year": 2026,
        "month": 9, "reporting_group_id": None, "budget": 32000.0, "currency": "MXN",
    }
    listed = client.get("/api/budgets?client_id=alpha").json()["budgets"]
    assert len([item for item in listed if item["month"] == 9]) == 1
    assert client.put(path, json={"budget": 1, "spend": 999}).status_code == 422
    assert client.put("/api/budgets/alpha/meta_ads/2026/9", json={"budget": 1}).status_code == 422
    assert client.put(path, json={"budget": -1}).status_code == 422


def test_campaign_budgets_are_scoped_by_account_campaign_month_and_feed_effective_summary(tmp_path):
    client, _, _ = report_api(tmp_path)
    base = "/api/budgets/alpha/google_ads/2026"
    october = client.put(f"{base}/10?account_id=1001&campaign_id=campaign-1001", json={"budget": 1200})
    assert october.status_code == 200
    assert october.json()["account_id"] == "1001"
    assert october.json()["campaign_id"] == "campaign-1001"
    assert client.put(f"{base}/10?account_id=2002&campaign_id=campaign-2002", json={"budget": 300}).status_code == 422

    september = client.put(f"{base}/9?account_id=1001&campaign_id=campaign-1001", json={"budget": 900})
    assert september.status_code == 200
    stored = client.get("/api/budgets?client_id=alpha").json()["budgets"]
    month_values = {(item.get("campaign_id"), item["month"]): item["budget"] for item in stored if item.get("campaign_id")}
    assert month_values[("campaign-1001", 10)] == 1200
    assert month_values[("campaign-1001", 9)] == 900

    summary = client.get("/api/reporting/summary", params={
        "client_id": "alpha", "start_date": "2026-10-01", "end_date": "2026-10-05",
    }).json()
    assert summary["monthly"][0]["monthly_budget"] == 1200
    report = client.get("/api/reports/performance.xlsx", params={"start_date": "2026-10-01", "end_date": "2026-10-05"})
    workbook = load_workbook(BytesIO(report.content), data_only=False)
    budgets_ws = workbook["PRESUPUESTOS"]
    allocation_row = next(
        row for row in range(2, budgets_ws.max_row + 1)
        if budgets_ws.cell(row, 9).value == "CAMPAÑA"
        and budgets_ws.cell(row, 5).value == 2026
        and budgets_ws.cell(row, 6).value == "Octubre"
        and budgets_ws.cell(row, 11).value == "campaign-1001"
    )
    monthly = workbook["Octubre 2026"]
    campaign_budget_ref = f"'PRESUPUESTOS'!$G${allocation_row}"
    assert any(
        isinstance(monthly.cell(row, column).value, str)
        and campaign_budget_ref in monthly.cell(row, column).value
        for row in range(1, monthly.max_row + 1)
        for column in range(1, monthly.max_column + 1)
    )


def test_group_budget_summary_sums_caps_once_and_ignores_unbudgeted_ungrouped_rows():
    period = MonthSlice(date(2026, 10, 1), date(2026, 10, 5), 2026, 10, 31, 5)
    def report_line(group_id, spend=100):
        return ReportLine(
            period=period, client_id="clarvi", client_name="Clarvi", platform="google_ads",
            account_ids=["acct"], currency="MXN", status="ok", spend=spend,
            conversions=1, leads=None, conversion_value=None, clicks=10, impressions=100,
            reach=None, reach_is_sum=False, reporting_group_id=group_id,
            reporting_group_name=group_id,
        )
    groups = [report_line("a"), report_line("b"), report_line("c"), report_line(None, spend=50)]
    budgets = [
        MonthlyBudget(client_id="clarvi", platform="google_ads", year=2026, month=10,
                      reporting_group_id="a", budget=10000, currency="MXN"),
        MonthlyBudget(client_id="clarvi", platform="google_ads", year=2026, month=10,
                      reporting_group_id="b", budget=20000, currency="MXN"),
        MonthlyBudget(client_id="clarvi", platform="google_ads", year=2026, month=10,
                      reporting_group_id="c", budget=5000, currency="MXN"),
        # Campaign allocations are a breakdown of group A's $10k cap.
        MonthlyBudget(client_id="clarvi", platform="google_ads", year=2026, month=10,
                      account_id="acct", campaign_id="c1", budget=4000, currency="MXN"),
        MonthlyBudget(client_id="clarvi", platform="google_ads", year=2026, month=10,
                      account_id="acct", campaign_id="c2", budget=6000, currency="MXN"),
    ]
    campaigns = [
        DataLine(period, "clarvi", "Clarvi", "google_ads", "acct", "Cuenta", "CAMPAÑA", cid, cid,
                 None, 10, 1, None, None, 1, 10, None, "MXN", "ok", reporting_group_id="a")
        for cid in ("c1", "c2")
    ]
    result = _summary_record(
        groups, {item.key: item for item in budgets}, level="period_total", period=period, data_lines=campaigns,
    )
    assert result["monthly_budget"] == 35000
    assert result["budget_captured"] is True
    assert result["budget_source"] == "group"
    assert result["budget_period"] == pytest.approx(35000 / 31 * 5)


class FakeDocument:
    def __init__(self, store: dict, document_id: str) -> None:
        self.store = store
        self.id = document_id

    def set(self, value):
        self.store[self.id] = dict(value)

    def delete(self):
        self.store.pop(self.id, None)

    def to_dict(self):
        return dict(self.store[self.id])


class FakeQuery:
    def __init__(self, store: dict, client_id: str | None = None) -> None:
        self.store = store
        self.client_id = client_id

    def where(self, _field, _operator, client_id):
        return FakeQuery(self.store, client_id)

    def stream(self):
        return [FakeDocument(self.store, key) for key, value in self.store.items() if self.client_id is None or value["client_id"] == self.client_id]

    def document(self, document_id):
        return FakeDocument(self.store, document_id)


class FakeFirestore:
    def __init__(self) -> None:
        self.collections: dict[str, dict] = {}

    def collection(self, name):
        return FakeQuery(self.collections.setdefault(name, {}))


def test_firestore_budget_repository_survives_repository_recreation_and_upserts():
    database = FakeFirestore()
    first = FirestoreBudgetRepository("performance_budgets")
    first._client = database
    first.upsert(MonthlyBudget(client_id="alpha", platform="google_ads", year=2026, month=9, budget=30000, currency="MXN"))
    first.upsert(MonthlyBudget(client_id="alpha", platform="google_ads", year=2026, month=9, budget=32000, currency="MXN"))

    restarted = FirestoreBudgetRepository("performance_budgets")
    restarted._client = database
    assert [(item.budget, item.currency) for item in restarted.list("alpha")] == [(32000, "MXN")]
    assert len(database.collections["performance_budgets"]) == 1


def test_file_mode_never_persists_budgets_to_ephemeral_storage():
    repository = create_budget_repository(Settings(store_backend="file"))
    assert repository.name == "unavailable"
    with pytest.raises(BudgetStoreError, match="firestore"):
        repository.list()


def test_month_slices_handle_partial_multiple_and_leap_months():
    slices = month_slices(date(2026, 8, 15), date(2026, 9, 15))
    assert [(item.month, item.applicable_days, item.days_in_month) for item in slices] == [(8, 17, 31), (9, 15, 30)]
    leap = month_slices(date(2028, 2, 1), date(2028, 2, 29))[0]
    assert leap.applicable_days == leap.days_in_month == 29


def test_xlsx_uses_dashboard_service_months_assignments_budgets_and_safe_formulas(tmp_path):
    client, provider, _ = report_api(tmp_path)
    response = client.get(
        "/api/reports/performance.xlsx",
        params={"start_date": "2026-08-01", "end_date": "2026-09-15"},
    )
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/vnd.openxmlformats")
    assert "Reporte_Performance_2026-08-01_al_2026-09-15.xlsx" in response.headers["content-disposition"]
    workbook = load_workbook(BytesIO(response.content), data_only=False)
    assert workbook.sheetnames[:2] == ["Agosto 2026", "Septiembre 2026"]
    assert {"PRESUPUESTOS", "DATOS", "CONFIGURACION", "AUDITORIA"}.issubset(workbook.sheetnames)
    assert workbook["DATOS"].sheet_state == "hidden"

    budget_sheet = workbook["PRESUPUESTOS"]
    budget_values = [budget_sheet.cell(row, 7).value for row in range(2, budget_sheet.max_row + 1)]
    assert 31000 in budget_values and 30000 in budget_values
    assert all(value is None or isinstance(value, (int, float)) for value in budget_values)
    assert all(budget_sheet.cell(row, 7).protection.locked is False for row in range(2, budget_sheet.max_row + 1))

    report = workbook["Septiembre 2026"]
    header_row = next(row for row in range(1, report.max_row + 1) if report.cell(row, 1).value == "MES" and report.cell(row, 2).value == "Medio")
    headers = {report.cell(header_row, col).value: col for col in range(1, report.max_column + 1)}
    platform_rows = [row for row in range(header_row + 1, report.max_row + 1) if report.cell(row, headers["Medio"]).value == "Google Ads"]
    assert len(platform_rows) == 2
    alpha_row = next(row for row in platform_rows if report.cell(row, headers["Gasto mensual a hoy"]).value == 1500)
    beta_row = next(row for row in platform_rows if report.cell(row, headers["Gasto mensual a hoy"]).value == 3000)
    assert report.cell(alpha_row, headers["Presupuesto"]).value.startswith("=IF(ISNUMBER('PRESUPUESTOS'!")
    assert "Presupuesto no capturado" in report.cell(beta_row, headers["Presupuesto"]).value

    forbidden = ("#REF!", "#DIV/0!", "#VALUE!", "#N/A")
    formulas = [cell.value for sheet in workbook.worksheets for row in sheet.iter_rows() for cell in row if isinstance(cell.value, str) and cell.value.startswith("=")]
    assert all(not any(error in formula for error in forbidden) for formula in formulas)

    data = workbook["DATOS"]
    campaign_accounts: dict[str, set[str]] = {}
    for row in range(2, data.max_row + 1):
        if data.cell(row, 9).value == "CAMPAÑA":
            campaign_accounts.setdefault(str(data.cell(row, 4).value), set()).add(str(data.cell(row, 7).value))
    assert campaign_accounts == {"alpha": {"1001"}, "beta": {"2002"}}
    assert set(provider.calls) == {
        ("1001", "2026-08-01", "2026-08-31"),
        ("1001", "2026-09-01", "2026-09-15"),
        ("2002", "2026-08-01", "2026-08-31"),
        ("2002", "2026-09-01", "2026-09-15"),
    }
    assert PACING_GREEN_MAX == 0.10 and PACING_YELLOW_MAX == 0.20


def test_xlsx_sorts_general_and_group_budget_rows_without_mixed_none_comparison(tmp_path):
    settings = Settings(clients_file=tmp_path / "clients.json", static_dir=tmp_path / "nodist")
    repo = JsonClientRepository(settings.clients_file)
    repo.create(ClientCreate(name="Alpha", currency="MXN", google_ads_sources=[{"customer_id": "9001"}]))
    provider = AssignmentAwareGoogleAds()
    snapshots = MemoryReportingGroupSnapshotRepository()
    client = TestClient(create_app(
        settings,
        repo,
        {"google_ads": provider},
        MemoryBudgetRepository([
            MonthlyBudget(client_id="alpha", platform="google_ads", year=2026, month=9,
                          budget=1500, currency="MXN"),
            MonthlyBudget(client_id="alpha", platform="google_ads", year=2026, month=9,
                          reporting_group_id="conversion", budget=900, currency="MXN"),
        ]),
        reporting_group_snapshot_repository=snapshots,
    ))
    configured = client.put("/api/reporting-group-snapshots/alpha/2026/9", json={
        "groups": [{"id": "conversion", "name": "Conversión", "type": "conversion", "active": True}],
        "assignments": [{"platform": "google_ads", "account_id": "9001", "campaign_id": "22",
                         "reporting_group_id": "conversion"}],
    })
    assert configured.status_code == 200, configured.text

    response = client.get("/api/reports/performance.xlsx", params={
        "start_date": "2026-09-01", "end_date": "2026-09-30",
    })
    assert response.status_code == 200, response.text
    workbook = load_workbook(BytesIO(response.content), data_only=False)
    budget_rows = list(workbook["PRESUPUESTOS"].iter_rows(min_row=2, values_only=True))
    assert {row[2] for row in budget_rows} == {None, "Conversión"}
    assert {row[6] for row in budget_rows} == {1500, 900}


def test_canonical_meta_purchase_reaches_local_dashboard_reporting_and_xlsx(tmp_path):
    settings = Settings(clients_file=tmp_path / "clients.json", static_dir=tmp_path / "nodist")
    repo = JsonClientRepository(settings.clients_file)
    accounts = {
        "2048742145193546": {
            "campaign_id": "joya-campaign", "campaign_name": "JOYÀ", "adset_id": "joya-set",
            "adset_name": "JOYÀ set", "ad_id": "joya-ad", "ad_name": "JOYÀ ad",
            "objective": "OUTCOME_SALES", "account_currency": "MXN", "spend": "1400",
            "impressions": "1000", "clicks": "100", "reach": "500",
            "actions": [
                {"action_type": "offsite_conversion.custom.1366991912161544", "value": "7"},
                {"action_type": "omni_purchase", "value": "99"},
                {"action_type": "offsite_conversion.fb_pixel_purchase", "value": "99"},
            ],
            "action_values": [
                {"action_type": "offsite_conversion.custom.1366991912161544", "value": "700"},
                {"action_type": "omni_purchase", "value": "9900"},
            ],
        },
        "303": {
            "campaign_id": "jungala-campaign", "campaign_name": "Jungala", "adset_id": "jungala-set",
            "adset_name": "Jungala set", "ad_id": "jungala-ad", "ad_name": "Jungala ad",
            "objective": "OUTCOME_SALES", "account_currency": "MXN", "spend": "1200",
            "impressions": "1000", "clicks": "100", "reach": "500",
            "actions": [
                {"action_type": "omni_purchase", "value": "3"},
                {"action_type": "purchase", "value": "3"},
            ],
            "action_values": [
                {"action_type": "omni_purchase", "value": "600"},
                {"action_type": "purchase", "value": "6000"},
            ],
        },
    }
    for name, slug, account in (
        ("Cirque du Soleil JOYÀ", "cirque-du-soleil-joya", "2048742145193546"),
        ("Jungala", "jungala", "303"),
    ):
        repo.create(ClientCreate(name=name, slug=slug, meta_ad_account_id=account))
    meta = MetaProvider(settings)
    meta._insights = lambda account_id, *args, **kwargs: [dict(accounts[str(account_id)])]
    app = create_app(
        settings, repo, {"meta_ads": meta}, MemoryBudgetRepository(),
        MemoryReportingGroupRepository(), MemoryReportingGroupSnapshotRepository(),
    )
    client = TestClient(app)
    dates = {"start_date": "2026-10-01", "end_date": "2026-10-05"}
    expected = {
        "cirque-du-soleil-joya": (7, 1400, 200, 700, 0.5),
        "jungala": (3, 1200, 400, 600, 0.5),
    }
    for client_id, (purchases, spend, cost, value, roas) in expected.items():
        response = client.get(f"/api/dashboard/{client_id}", params=dates)
        assert response.status_code == 200
        totals = response.json()["platforms"]["meta_ads"]["current"]["totals"]
        assert totals["purchases"] == purchases
        assert totals["spend"] == spend
        assert totals["cost_per_purchase"] == cost
        assert totals["conversion_value"] == value
        assert totals["roas"] == roas

    exported = client.get("/api/reports/performance.xlsx", params=dates)
    assert exported.status_code == 200, exported.text
    workbook = load_workbook(BytesIO(exported.content), data_only=False)
    sheet = workbook["Octubre 2026"]
    for title, (purchases, _spend, cost, value, roas) in (
        ("JOYÀ", expected["cirque-du-soleil-joya"]),
        ("JUNGALA", expected["jungala"]),
    ):
        title_row = next(row for row in range(1, sheet.max_row + 1) if sheet.cell(row, 1).value == title)
        data_row = next(row for row in range(title_row + 2, sheet.max_row + 1) if sheet.cell(row, 2).value == "Meta Ads")
        assert sheet.cell(data_row, 5).value == purchases
        assert sheet.cell(data_row, 9).value == value
        assert sheet.cell(data_row, 13).value == cost
        assert sheet.cell(data_row, 14).value == roas

def test_reporting_summary_and_xlsx_reconcile_for_same_normalized_dashboard_data(tmp_path):
    client, provider, _ = report_api(tmp_path)
    params = {"client_id": "alpha", "start_date": "2026-09-01", "end_date": "2026-09-15"}
    summary_response = client.get("/api/reporting/summary", params=params)
    assert summary_response.status_code == 200, summary_response.text
    summary = summary_response.json()
    workbook_response = client.get("/api/reports/performance.xlsx", params={
        "start_date": params["start_date"], "end_date": params["end_date"],
    })
    assert workbook_response.status_code == 200
    workbook = load_workbook(BytesIO(workbook_response.content), data_only=False)
    report = workbook["Septiembre 2026"]
    line = summary["monthly"][0]
    row = next(row for row in range(1, report.max_row + 1) if report.cell(row, 2).value == "Google Ads" and report.cell(row, 4).value == line["spend"])
    assert line["spend"] == report.cell(row, 4).value
    assert line["conversions"] == report.cell(row, 5).value
    assert line["clicks"] == report.cell(row, 6).value
    assert line["impressions"] == report.cell(row, 7).value
    assert line["budget_period"] == pytest.approx(15000)
    assert line["expected_spend"] == pytest.approx(15000)
    assert line["budget_difference"] == pytest.approx(28500)
    assert line["pacing_difference"] == pytest.approx(13500)
    assert line["budget_consumed"] == pytest.approx(0.05)
    assert provider.calls == [("1001", "2026-09-01", "2026-09-15"), ("2002", "2026-09-01", "2026-09-15")]


def test_multiplatform_dashboard_and_xlsx_reconcile_from_base_metrics(tmp_path):
    settings = Settings(clients_file=tmp_path / "clients.json", static_dir=tmp_path / "nodist")
    repo = JsonClientRepository(settings.clients_file)
    repo.create(ClientCreate(
        name="Alpha", currency="MXN",
        google_ads_sources=[{"customer_id": "1001"}],
        meta_ads_sources=[{"ad_account_id": "3003"}],
    ))
    budgets = MemoryBudgetRepository([
        MonthlyBudget(client_id="alpha", platform="google_ads", year=2026, month=9, budget=30000, currency="MXN"),
        MonthlyBudget(client_id="alpha", platform="meta_ads", year=2026, month=9, budget=20000, currency="MXN"),
    ])
    client = TestClient(create_app(settings, repo, {"google_ads": FakeGoogleAds(), "meta_ads": FakeMetaAds()}, budgets))
    params = {"client_id": "alpha", "start_date": "2026-09-01", "end_date": "2026-09-15"}
    summary_response = client.get("/api/reporting/summary", params=params)
    assert summary_response.status_code == 200
    summary = summary_response.json()
    total = summary["period_total"]
    assert {line["platform"] for line in summary["monthly"]} == {"google_ads", "meta_ads"}
    assert total["spend"] == pytest.approx(4500)
    assert total["conversions"] == pytest.approx(45)
    assert total["leads"] == pytest.approx(30)
    assert total["daily_conversions"] == pytest.approx(3)
    assert total["budget_period"] == pytest.approx(25000)
    assert total["budget_difference"] == pytest.approx(45500)
    assert total["cpa"] == pytest.approx(100)  # total de bases, no promedio de CPA.
    assert total["reach"] is None and total["frequency"] is None

    xlsx_response = client.get("/api/reports/performance.xlsx", params={
        "start_date": params["start_date"], "end_date": params["end_date"],
    })
    workbook = load_workbook(BytesIO(xlsx_response.content), data_only=False)
    report = workbook["Septiembre 2026"]
    row_by_platform = {
        report.cell(row, 2).value: row
        for row in range(1, report.max_row + 1)
        if report.cell(row, 2).value in ("Google Ads", "Meta Ads")
    }
    assert set(row_by_platform) == {"Google Ads", "Meta Ads"}
    assert report.cell(row_by_platform["Google Ads"], 4).value == 1500
    assert report.cell(row_by_platform["Meta Ads"], 4).value == 3000
    total_row = next(row for row in range(1, report.max_row + 1) if report.cell(row, 2).value == "TOTAL Alpha")
    assert report.cell(total_row, 4).value == 4500
    assert total["cpa"] == pytest.approx(4500 / 45)


def test_summary_multimonth_uses_month_specific_budgets_and_inclusive_days(tmp_path):
    client, _, _ = report_api(tmp_path)
    response = client.get("/api/reporting/summary", params={
        "client_id": "alpha", "start_date": "2026-08-15", "end_date": "2026-09-15",
    })
    assert response.status_code == 200
    summary = response.json()
    alpha_rows = summary["monthly"]
    august = next(row for row in alpha_rows if row["month"] == 8)
    september = next(row for row in alpha_rows if row["month"] == 9)
    assert (august["monthly_budget"], august["applicable_days"], august["budget_period"]) == pytest.approx((31000, 17, 31000 / 31 * 17))
    assert (september["monthly_budget"], september["applicable_days"], september["budget_period"]) == pytest.approx((30000, 15, 15000))
    assert summary["period_total"]["monthly_budget"] == 61000
    assert summary["period_total"]["budget_period"] == pytest.approx(31000 / 31 * 17 + 15000)


@pytest.mark.parametrize(("allocation", "alpha_spend"), [("exclusive", 100.0), ("shared", 300.0)])
def test_reporting_respects_partial_shared_campaign_assignments(tmp_path, allocation, alpha_spend):
    settings = Settings(clients_file=tmp_path / "clients.json", static_dir=tmp_path / "nodist")
    repo = JsonClientRepository(settings.clients_file)
    repo.create(ClientCreate(name="Alpha", currency="MXN", google_ads_sources=[{"customer_id": "9001"}]))
    repo.create(ClientCreate(name="Beta", currency="MXN", google_ads_sources=[{
        "customer_id": "9001", "campaign_ids": ["22"], "allocation": allocation,
    }]))
    budgets = MemoryBudgetRepository([
        MonthlyBudget(client_id="alpha", platform="google_ads", year=2026, month=9, budget=1000, currency="MXN"),
        MonthlyBudget(client_id="beta", platform="google_ads", year=2026, month=9, budget=1000, currency="MXN"),
    ])
    provider = AssignmentAwareGoogleAds()
    group_repo = MemoryReportingGroupRepository()
    snapshot_repo = MemoryReportingGroupSnapshotRepository()
    client = TestClient(create_app(settings, repo, {"google_ads": provider}, budgets, group_repo, snapshot_repo))
    grouping = client.put("/api/reporting-group-snapshots/beta/2026/9", json={
        "groups": [{"id": "conversion", "name": "Conversión", "type": "conversion", "active": True}],
        "assignments": [{"platform": "google_ads", "account_id": "9001", "campaign_id": "22", "reporting_group_id": "conversion"}],
    })
    assert grouping.status_code == 200, grouping.text
    summary_alpha = client.get("/api/reporting/summary", params={
        "client_id": "alpha", "start_date": "2026-09-01", "end_date": "2026-09-30",
    }).json()
    summary_beta = client.get("/api/reporting/summary", params={
        "client_id": "beta", "start_date": "2026-09-01", "end_date": "2026-09-30",
    }).json()
    assert summary_alpha["monthly"][0]["spend"] == alpha_spend
    assert summary_beta["monthly"][0]["spend"] == 200.0
    assert summary_beta["grouping_active"] is True
    assert summary_beta["monthly"][0]["reporting_group_id"] == "conversion"
    if allocation == "exclusive":
        assert all(call[2] != ("11", "22") for call in provider.calls)
    else:
        assert ("9001", "2026-09-01", ("11", "22")) in provider.calls

    xlsx = client.get("/api/reports/performance.xlsx", params={
        "start_date": "2026-09-01", "end_date": "2026-09-30",
    })
    workbook = load_workbook(BytesIO(xlsx.content), data_only=False)
    report = workbook["Septiembre 2026"]
    spend_values = [report.cell(row, 4).value for row in range(1, report.max_row + 1) if report.cell(row, 2).value == "Google Ads"]
    assert alpha_spend in spend_values
    assert 200.0 in spend_values


@pytest.mark.parametrize(
    ("overrides", "expected"),
    [
        ({}, {"budget_period": 15000, "budget_difference": 29250, "expected_spend": 15000, "pacing_difference": 14250, "budget_consumed": 0.025}),
        ({"conversions": 0}, {"cpa": None, "daily_conversions": 0, "projected_conversions": 0, "ticket_average": None}),
        ({"leads": 0}, {"cpl": None}),
        ({"clicks": 0}, {"cpc": None}),
        ({"impressions": 0}, {"ctr": None}),
        ({"spend": 0}, {"roas": None}),
        ({"reach": 0}, {"frequency": None}),
        ({"spend": None}, {"cpa": None, "cpl": None, "roas": None, "cpc": None}),
        ({"conversions": None}, {"cpa": None, "daily_conversions": None, "projected_conversions": None}),
        ({"leads": None}, {"cpl": None}),
        ({"clicks": None}, {"cpc": None, "ctr": None}),
        ({"impressions": None}, {"ctr": None, "frequency": None}),
        ({"conversion_value": None}, {"roas": None, "ticket_average": None}),
        ({"monthly_budget": None}, {"budget_period": None, "budget_difference": None, "expected_spend": None, "pacing_difference": None, "budget_consumed": None}),
        ({"monthly_budget": 0}, {"budget_period": 0, "budget_difference": -750, "expected_spend": 0, "pacing_difference": -750, "budget_consumed": None}),
        ({"applicable_days": 16, "days_in_month": 30}, {"budget_period": 16000}),
        ({"observed_days": 0}, {"daily_conversions": None, "projected_conversions": None}),
    ],
)
def test_shared_kpi_calculator_handles_reconciliation_and_divisor_cases(overrides, expected):
    bases = {
        "spend": 750.0,
        "conversions": 15.0,
        "leads": 8.0,
        "conversion_value": 1500.0,
        "clicks": 300.0,
        "impressions": 10000.0,
        "reach": 5000.0,
    }
    args = {
        "monthly_budget": 30000.0,
        "applicable_days": 15,
        "days_in_month": 30,
        "observed_days": 15,
    }
    for key in list(bases):
        if key in overrides:
            bases[key] = overrides[key]
    args.update({key: value for key, value in overrides.items() if key not in bases})
    result = calculate_performance_kpis(bases, **args)
    for key, value in expected.items():
        if value is None:
            assert result[key] is None
        else:
            assert result[key] == pytest.approx(value)
    assert all(value is None or (value == value and abs(value) != float("inf")) for value in result.values())


def test_month_slices_include_midmonth_through_month_end():
    period = month_slices(date(2026, 9, 15), date(2026, 9, 30))[0]
    assert period.applicable_days == 16
    kpis = calculate_performance_kpis(
        {"spend": 10000, "conversions": 0, "leads": 0, "conversion_value": 0, "clicks": 0, "impressions": 0, "reach": 0},
        monthly_budget=30000,
        applicable_days=period.applicable_days,
        days_in_month=period.days_in_month,
    )
    assert kpis["budget_period"] == pytest.approx(16000)


def test_excel_formulas_and_dashboard_values_share_kpi_definitions():
    bases = {
        "spend": 750.0, "conversions": 15.0, "leads": 8.0,
        "conversion_value": 1500.0, "clicks": 300.0,
        "impressions": 10000.0, "reach": 5000.0,
    }
    kpis = calculate_performance_kpis(
        bases, monthly_budget=30000, applicable_days=15, days_in_month=30,
    )
    formulas = _formulae(12)
    assert set(formulas) == {REPORT_COLUMNS[key] for key in KPI_EXPRESSIONS}
    assert len(KPI_EXPRESSIONS) == 14
    assert kpis["budget_period"] == pytest.approx(15000)
    assert kpis["cpa"] == pytest.approx(50)


def test_export_hides_client_metric_families_without_hiding_other_clients(tmp_path):
    client, _, _ = report_api(tmp_path)
    assert client.put("/api/clients/alpha", json={"hidden_metrics": ["purchase_roas", "cost_per_conversion"]}).status_code == 200
    response = client.get("/api/reports/performance.xlsx", params={"start_date": "2026-09-15", "end_date": "2026-09-30"})
    assert response.status_code == 200
    workbook = load_workbook(BytesIO(response.content))
    config = workbook["CONFIGURACION"]
    rows = [tuple(config.cell(row, col).value for col in range(1, config.max_column + 1)) for row in range(2, config.max_row + 1)]
    assert sum(1 for row in rows if row[1] == "alpha" and row[17] == "REPORT_PROFILE_NOT_CONFIGURED") == 1
    assert sum(1 for row in rows if row[1] == "beta" and row[17] == "REPORT_PROFILE_NOT_CONFIGURED") == 1


@pytest.mark.parametrize("budget", [float("inf"), float("nan"), -1])
def test_budget_model_rejects_nonfinite_and_negative_inputs(budget):
    from pydantic import ValidationError
    from app.budgets import BudgetValue
    with pytest.raises(ValidationError):
        BudgetValue(budget=budget)
    with pytest.raises(ValidationError):
        MonthlyBudget(client_id="alpha", platform="google_ads", year=2026, month=9, budget=budget, currency="MXN")


def test_monthly_budget_and_pacing_use_distinct_bases_in_partial_period():
    kpis = calculate_performance_kpis({"spend": 1600}, monthly_budget=30000, applicable_days=16, days_in_month=30)
    assert kpis["budget_difference"] == 28400
    assert kpis["expected_spend"] == 16000
    assert kpis["pacing_difference"] == 14400
    assert kpis["budget_consumed"] == pytest.approx(1600 / 30000)
    assert "E12" in _formulae(12)[REPORT_COLUMNS["budget_difference"]]
    assert "F12" not in _formulae(12)[REPORT_COLUMNS["budget_consumed"]]


def test_grouped_export_preserves_account_scoped_campaign_ids_and_ungrouped_totals():
    from app.models import Client
    from app.reporting import collect_report_lines
    from app.reporting_groups import ReportingGroup

    alpha = Client(id="alpha", slug="alpha", name="Alpha", google_ads_sources=[{"customer_id": "1001"}, {"customer_id": "1002"}])
    beta = Client(id="beta", slug="beta", name="Beta", google_ads_sources=[{"customer_id": "2002"}])
    group = ReportingGroup(client_id="alpha", id="conversion", name="Conversión", type="conversion", assignments=[{"platform": "google_ads", "account_id": "1001", "campaign_id": "same-id"}])

    class Service:
        def platform_block(self, client, *args):
            campaigns = [{"campaign_id": "same-id", "source_account_id": account, "spend": 100, "conversions": 2, "clicks": 10, "impressions": 100, "objective": "LEADS"} for account in ("1001", "1002")] if client.id == "alpha" else []
            return {"status": "ok", "currency": "MXN", "current": {"totals": {"spend": 200 if client.id == "alpha" else 700}, "campaigns": campaigns}}

    report, data = collect_report_lines(Service(), [alpha, beta], date(2026, 9, 15), date(2026, 9, 30), {}, reporting_groups_by_client={"alpha": [group]})
    alpha_rows = [row for row in report if row.client_id == "alpha"]
    assert sum(row.spend for row in alpha_rows) == 200
    assert {row.reporting_group_id for row in alpha_rows} == {None, "conversion"}
    beta_rows = [row for row in report if row.client_id == "beta"]
    assert len(beta_rows) == 1 and beta_rows[0].spend == 700
    assert beta_rows[0].grouping_enabled is False
    assert {row.account_id for row in data if row.client_id == "alpha" and row.level == "CAMPAÑA"} == {"1001", "1002"}


def test_pinterest_is_kept_in_xlsx_data_and_audit_without_changing_main_profile_blocks():
    from io import BytesIO
    from openpyxl import load_workbook
    from app.models import Client
    from app.reporting import collect_report_lines, _build_profile_workbook

    client = Client(id="ssiento", slug="ssiento", name="Ssiento", pinterest_ads_sources=[{
        "source_id": "549763101458", "ad_account_id": "549763101458", "display_name": "SSIENTO COMERCIAL", "currency": "MXN",
    }])

    class Service:
        def platform_block(self, client, platform, rng, *_args):
            if platform != "pinterest_ads":
                return {"platform": platform, "status": "not_linked", "current": None}
            campaign = {"campaign_id": "p1", "campaign_name": "Checkout", "source_account_id": "549763101458",
                        "spend": 100, "impressions": 2000, "clicks": 100, "checkouts": 5,
                        "conversion_value": 500, "currency": "MXN", "status": "ACTIVE"}
            return {"platform": platform, "status": "ok", "currency": "MXN", "current": {
                "totals": {key: campaign.get(key) for key in ("spend", "impressions", "clicks", "checkouts", "conversion_value")},
                "campaigns": [campaign],
            }}

    report, data = collect_report_lines(Service(), [client], date(2026, 10, 1), date(2026, 10, 1), {})
    assert all(line.platform != "pinterest_ads" for line in report)
    pinterest = [line for line in data if line.platform == "pinterest_ads"]
    assert len(pinterest) == 2
    assert pinterest[0].checkouts == 5
    workbook = _build_profile_workbook(report, data, [], [client], date(2026, 10, 1), date(2026, 10, 1))
    output = BytesIO()
    workbook.save(output)
    from app.reporting import _validate_workbook
    _validate_workbook(output.getvalue(), expected_accounts_for_clients([client]))
    reopened = load_workbook(BytesIO(output.getvalue()), data_only=False)
    sheet = reopened["DATOS"]
    headers = [cell.value for cell in sheet[1]]
    assert "CHECKOUTS" in headers
    assert any(row[headers.index("PLATAFORMA")] == "Pinterest Ads" and row[headers.index("CHECKOUTS")] == 5
               for row in sheet.iter_rows(min_row=2, values_only=True))
    audit = reopened["AUDITORIA"]
    assert any(audit.cell(row, 4).value == "checkouts" and audit.cell(row, 6).value == 5
               for row in range(2, audit.max_row + 1))

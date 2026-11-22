from __future__ import annotations

import calendar
from datetime import date, timedelta
from io import BytesIO

from openpyxl import load_workbook
import pytest

from app.reporting_groups import (
    CampaignGroupAssignment,
    MemoryReportingGroupSnapshotRepository,
    FirestoreReportingGroupRepository,
    MemoryReportingGroupRepository,
    ReportingGroupSnapshot,
    ReportingGroupSnapshotDraft,
    ReportingGroupSnapshotGroup,
    MonthlyCampaignAssignment,
    enabled_platforms_for_group,
    ReportingGroupDraft,
    validate_monthly_snapshot,
    validate_reporting_groups,
)
from app.models import Client
from tests.test_reporting import report_api


def _snapshot_payload(group_id: str, name: str, active: bool = True):
    return {
        "groups": [{"id": group_id, "name": name, "type": group_id, "active": active}],
        "assignments": [{
            "platform": "google_ads", "account_id": "1001",
            "campaign_id": "campaign-1001", "reporting_group_id": group_id,
        }],
    }


def test_group_platform_switch_is_monthly_and_turning_off_keeps_assignments():
    client = Client(
        id="alpha", name="Alpha", slug="alpha",
        google_ads_sources=[{"customer_id": "1001"}],
        meta_ads_sources=[{"ad_account_id": "2002"}],
    )
    snapshot = ReportingGroupSnapshotDraft(
        groups=[ReportingGroupSnapshotGroup(
            id="traffic", name="Tráfico", type="traffic", active=True,
            enabled_platforms={"google_ads": True, "meta_ads": False, "spotify_ads": False},
        )],
        assignments=[
            MonthlyCampaignAssignment(platform="google_ads", account_id="1001", campaign_id="g1", reporting_group_id="traffic"),
            MonthlyCampaignAssignment(platform="meta_ads", account_id="2002", campaign_id="m1", reporting_group_id="traffic"),
        ],
    )
    validate_monthly_snapshot(client, snapshot)
    persisted = ReportingGroupSnapshot(client_id="alpha", year=2026, month=10, **snapshot.model_dump())
    group = persisted.as_groups()[0]
    assert enabled_platforms_for_group(group) == {"google_ads": True, "meta_ads": False, "spotify_ads": False, "pinterest_ads": False}
    assert {(item.platform, item.campaign_id) for item in group.assignments} == {
        ("google_ads", "g1"), ("meta_ads", "m1"),
    }


def test_legacy_group_platform_fallback_uses_existing_assignments():
    legacy = ReportingGroupDraft(
        id="traffic", name="Tráfico", type="traffic",
        assignments=[CampaignGroupAssignment(platform="google_ads", account_id="1001", campaign_id="g1")],
    )
    assert enabled_platforms_for_group(legacy) == {"google_ads": True, "meta_ads": False, "spotify_ads": False, "pinterest_ads": False}


def test_monthly_snapshots_keep_group_and_budget_independent_and_reconcile_xlsx(tmp_path):
    snapshots = MemoryReportingGroupSnapshotRepository()
    client, _provider, _budgets = report_api(tmp_path, snapshot_repository=snapshots)
    september = client.put("/api/reporting-group-snapshots/alpha/2026/9", json=_snapshot_payload("conversion", "Conversión"))
    october = client.put("/api/reporting-group-snapshots/alpha/2026/10", json=_snapshot_payload("traffic", "Tráfico"))
    assert september.status_code == october.status_code == 200
    assert september.json()["groups"][0]["active"] is True
    assert client.put("/api/budgets/alpha/google_ads/2026/9?reporting_group_id=conversion", json={"budget": 9000}).status_code == 200
    assert client.put("/api/budgets/alpha/google_ads/2026/10?reporting_group_id=traffic", json={"budget": 21000}).status_code == 200

    def summary(start, end):
        response = client.get("/api/reporting/summary", params={"client_id": "alpha", "start_date": start, "end_date": end})
        assert response.status_code == 200, response.text
        return response.json()

    september_row = summary("2026-09-01", "2026-09-30")["monthly"][0]
    october_row = summary("2026-10-01", "2026-10-31")["monthly"][0]
    assert (september_row["reporting_group_id"], september_row["monthly_budget"]) == ("conversion", 9000)
    assert (october_row["reporting_group_id"], october_row["monthly_budget"]) == ("traffic", 21000)
    multi = summary("2026-09-15", "2026-10-15")["monthly"]
    assert [(row["month"], row["reporting_group_id"], row["monthly_budget"]) for row in multi] == [
        (9, "conversion", 9000), (10, "traffic", 21000),
    ]
    assert multi[0]["budget_period"] == 9000 / 30 * 16
    assert multi[1]["budget_period"] == 21000 / 31 * 15

    response = client.get("/api/reports/performance.xlsx", params={"start_date": "2026-09-15", "end_date": "2026-10-15"})
    assert response.status_code == 200
    workbook = load_workbook(BytesIO(response.content), data_only=False)
    assert workbook.sheetnames[:2] == ["Septiembre 2026", "Octubre 2026"]
    assert {"PRESUPUESTOS", "DATOS", "CONFIGURACION", "AUDITORIA"}.issubset(workbook.sheetnames)
    config = workbook["CONFIGURACION"]
    assert [cell.value for cell in config[1]][:12] == [
        "CLIENTE", "CLIENTE_ID", "AÑO", "MES", "GRUPO", "TIPO_GRUPO",
        "PLATAFORMA", "CUENTA_ID", "CAMPAÑA_ID", "CAMPAÑA", "OBJETIVO", "ACTIVA",
    ]
    campaign_rows = [row for row in config.iter_rows(min_row=2, values_only=True) if row[1] == "alpha" and row[8]]
    assert {(row[2], row[3], row[4], row[8]) for row in campaign_rows} == {
        (2026, "Septiembre", "Conversión", "campaign-1001"),
        (2026, "Octubre", "Tráfico", "campaign-1001"),
    }
    assert len(campaign_rows) == 2


def test_group_budget_keys_are_independent_and_empty_groups_do_not_poison_summary_or_xlsx(tmp_path):
    snapshots = MemoryReportingGroupSnapshotRepository()
    client, _provider, budgets = report_api(tmp_path, snapshot_repository=snapshots)
    payload = {
        "groups": [
            {"id": "conversion", "name": "Conversión", "type": "conversion", "active": True},
            {"id": "traffic", "name": "Tráfico", "type": "traffic", "active": True},
            {"id": "custom-awareness", "name": "Grupo personalizado", "type": "custom", "active": True},
        ],
        "assignments": [{
            "platform": "google_ads", "account_id": "1001",
            "campaign_id": "campaign-1001", "reporting_group_id": "conversion",
        }],
    }
    assert client.put("/api/reporting-group-snapshots/alpha/2026/10", json=payload).status_code == 200
    assert client.put("/api/reporting-group-snapshots/alpha/2026/9", json=payload).status_code == 200

    # Misma interfaz de escritura, claves diferentes por grupo y mes.
    assert client.put("/api/budgets/alpha/google_ads/2026/10?reporting_group_id=conversion", json={"budget": 80000}).status_code == 200
    assert client.put("/api/budgets/alpha/google_ads/2026/10?reporting_group_id=traffic", json={"budget": 30000}).status_code == 200
    assert client.put("/api/budgets/alpha/google_ads/2026/10?reporting_group_id=custom-awareness", json={"budget": 10000}).status_code == 200
    assert client.put("/api/budgets/alpha/google_ads/2026/9?reporting_group_id=conversion", json={"budget": 35000}).status_code == 200

    listed = client.get("/api/budgets?client_id=alpha").json()["budgets"]
    key_values = {
        (item["client_id"], item["platform"], item["year"], item["month"], item["reporting_group_id"]): item["budget"]
        for item in listed
    }
    assert key_values[("alpha", "google_ads", 2026, 10, "conversion")] == 80000
    assert key_values[("alpha", "google_ads", 2026, 10, "traffic")] == 30000
    assert key_values[("alpha", "google_ads", 2026, 10, "custom-awareness")] == 10000
    assert key_values[("alpha", "google_ads", 2026, 9, "conversion")] == 35000

    params = {"client_id": "alpha", "start_date": "2026-10-01", "end_date": "2026-10-05"}
    summary_response = client.get("/api/reporting/summary", params=params)
    assert summary_response.status_code == 200
    summary = summary_response.json()
    # Traffic y el grupo personalizado no tienen campañas en octubre: su
    # presupuesto capturado/no capturado no debe cambiar el total del periodo.
    assert [(row["reporting_group_id"], row["monthly_budget"]) for row in summary["monthly"]] == [("conversion", 80000)]
    assert summary["period_total"]["budget_captured"] is True
    assert summary["period_total"]["monthly_budget"] == 80000
    assert summary["period_total"]["budget_period"] == pytest.approx(80000 * 5 / 31)

    # Un cero explícito sigue siendo capturado; el ratio sobre presupuesto cero
    # queda null en vez de NaN/Infinity.
    assert client.put("/api/budgets/alpha/google_ads/2026/10?reporting_group_id=conversion", json={"budget": 0}).status_code == 200
    zero_summary = client.get("/api/reporting/summary", params=params).json()
    zero_row = zero_summary["monthly"][0]
    assert zero_row["budget_captured"] is True
    assert zero_row["monthly_budget"] == zero_row["budget_period"] == 0
    assert zero_row["budget_consumed"] is None
    assert zero_row["pacing_status"] == "unavailable"

    # Volver a capturar el importe deja el XLSX vinculado a la fila correcta.
    assert client.put("/api/budgets/alpha/google_ads/2026/10?reporting_group_id=conversion", json={"budget": 80000}).status_code == 200
    response = client.get("/api/reports/performance.xlsx", params={"start_date": params["start_date"], "end_date": params["end_date"]})
    assert response.status_code == 200
    workbook = load_workbook(BytesIO(response.content), data_only=False)
    budgets_ws = workbook["PRESUPUESTOS"]
    budget_row = next(row for row in range(2, budgets_ws.max_row + 1) if budgets_ws.cell(row, 3).value == "Conversión")
    assert [budgets_ws.cell(budget_row, col).value for col in (1, 2, 3, 4, 5, 6, 7, 8)] == [
        "alpha", "Alpha", "Conversión", "Google Ads", 2026, "Octubre", 80000, "MXN",
    ]
    report_ws = workbook["Octubre 2026"]
    report_row = next(row for row in range(1, report_ws.max_row + 1) if report_ws.cell(row, 2).value == "Google Ads")
    assert f"'PRESUPUESTOS'!$G${budget_row}" in report_ws.cell(report_row, 3).value


def test_duplicate_campaign_assignment_across_groups_is_rejected(tmp_path):
    client, *_ = report_api(tmp_path)
    model_client = client.app.state.repo.get("alpha")
    campaign = CampaignGroupAssignment(platform="google_ads", account_id="1001", campaign_id="campaign-1001")
    groups = [
        ReportingGroupDraft(id="conversion", name="Conversión", type="conversion", assignments=[campaign]),
        ReportingGroupDraft(id="traffic", name="Tráfico", type="traffic", assignments=[campaign]),
    ]
    try:
        validate_reporting_groups(model_client, groups)
    except ValueError as exc:
        assert "ya pertenece" in str(exc)
    else:
        raise AssertionError("Una campaña no puede estar activa en dos grupos")


def test_legacy_config_is_explicitly_imported_only_to_current_month(tmp_path):
    groups = MemoryReportingGroupRepository()
    snapshots = MemoryReportingGroupSnapshotRepository()
    client, *_ = report_api(tmp_path, groups, snapshots)
    campaign = {"platform": "google_ads", "account_id": "1001", "campaign_id": "campaign-1001"}
    legacy = client.put("/api/reporting-groups/alpha", json={"groups": [{
        "id": "conversion", "name": "Conversión", "type": "conversion", "active": True,
        "assignments": [campaign],
    }]})
    assert legacy.status_code == 200
    today = date.today()
    prior_month_end = date(today.year, today.month, 1) - timedelta(days=1)
    historical_start = date(prior_month_end.year, prior_month_end.month, 1)
    historical_path = f"/api/reporting-group-snapshots/alpha/{historical_start.year}/{historical_start.month}/initialize-from-current"
    assert client.post(historical_path).status_code == 409
    missing = client.get("/api/reporting/summary", params={
        "client_id": "alpha", "start_date": historical_start.isoformat(), "end_date": prior_month_end.isoformat(),
    })
    assert missing.status_code == 409
    assert missing.json()["detail"]["code"] == "missing_group_configuration"

    imported = client.post(f"/api/reporting-group-snapshots/alpha/{today.year}/{today.month}/initialize-from-current")
    assert imported.status_code == 200, imported.text
    start = date(today.year, today.month, 1)
    end = date(today.year, today.month, calendar.monthrange(today.year, today.month)[1])
    current_before = client.get("/api/reporting/summary", params={
        "client_id": "alpha", "start_date": start.isoformat(), "end_date": end.isoformat(),
    }).json()["monthly"][0]
    assert current_before["reporting_group_id"] == "conversion"

    changed_legacy = client.put("/api/reporting-groups/alpha", json={"groups": [{
        "id": "traffic", "name": "Tráfico", "type": "traffic", "active": True,
        "assignments": [campaign],
    }]})
    assert changed_legacy.status_code == 200
    current_after = client.get("/api/reporting/summary", params={
        "client_id": "alpha", "start_date": start.isoformat(), "end_date": end.isoformat(),
    }).json()["monthly"][0]
    assert current_after["reporting_group_id"] == "conversion"


def test_copy_previous_copies_only_snapshot_and_not_monthly_budgets(tmp_path):
    snapshots = MemoryReportingGroupSnapshotRepository()
    client, _, budgets = report_api(tmp_path, snapshot_repository=snapshots)
    saved = client.put("/api/reporting-group-snapshots/alpha/2026/9", json=_snapshot_payload("conversion", "Conversión"))
    assert saved.status_code == 200
    assert client.put("/api/budgets/alpha/google_ads/2026/9?reporting_group_id=conversion", json={"budget": 12345}).status_code == 200
    copied = client.post("/api/reporting-group-snapshots/alpha/2026/10/copy-previous")
    assert copied.status_code == 200, copied.text
    assert copied.json()["assignments"][0]["reporting_group_id"] == "conversion"
    assert not [item for item in budgets.list("alpha") if item.year == 2026 and item.month == 10 and item.reporting_group_id]
    # A second copy cannot overwrite the destination snapshot.
    assert client.post("/api/reporting-group-snapshots/alpha/2026/10/copy-previous").status_code == 409


def test_group_rename_and_deactivation_are_month_scoped(tmp_path):
    snapshots = MemoryReportingGroupSnapshotRepository()
    client, *_ = report_api(tmp_path, snapshot_repository=snapshots)
    assert client.put("/api/reporting-group-snapshots/alpha/2026/9", json=_snapshot_payload("conversion", "Conversión", True)).status_code == 200
    october_config = _snapshot_payload("conversion", "Ventas", True)
    assert client.put("/api/reporting-group-snapshots/alpha/2026/10", json=october_config).status_code == 200

    def summary(year, month):
        start = f"{year}-{month:02d}-01"
        end = f"{year}-{month:02d}-28"
        return client.get("/api/reporting/summary", params={"client_id": "alpha", "start_date": start, "end_date": end}).json()["monthly"][0]

    assert summary(2026, 9)["reporting_group_name"] == "Conversión"
    assert summary(2026, 10)["reporting_group_name"] == "Ventas"
    october_config["groups"][0]["active"] = False
    deactivated = client.put("/api/reporting-group-snapshots/alpha/2026/10", json=october_config)
    assert deactivated.status_code == 200
    assert deactivated.json()["groups"][0]["active"] is False
    assert summary(2026, 10)["reporting_group_id"] is None
    assert summary(2026, 9)["reporting_group_name"] == "Conversión"
    report = client.get("/api/reports/performance.xlsx", params={
        "start_date": "2026-09-01", "end_date": "2026-10-31",
    })
    assert report.status_code == 200
    workbook = load_workbook(BytesIO(report.content), data_only=False)
    config_rows = [row for row in workbook["CONFIGURACION"].iter_rows(min_row=2, values_only=True)
                   if row[1] == "alpha"]
    september_row = next(row for row in config_rows if row[2:4] == (2026, "Septiembre") and row[8] == "campaign-1001")
    october_row = next(row for row in config_rows if row[2:4] == (2026, "Octubre") and row[8] == "campaign-1001")
    assert (september_row[4], september_row[11]) == ("Conversión", True)
    assert (october_row[4], october_row[11]) == ("Ventas", False)


def test_missing_month_snapshot_blocks_summary_and_xlsx_but_unconfigured_client_still_works(tmp_path):
    groups = MemoryReportingGroupRepository()
    snapshots = MemoryReportingGroupSnapshotRepository()
    client, *_ = report_api(tmp_path, groups, snapshots)
    assert client.put("/api/reporting-group-snapshots/alpha/2026/9", json=_snapshot_payload("conversion", "Conversión")).status_code == 200
    summary = client.get("/api/reporting/summary", params={
        "client_id": "alpha", "start_date": "2026-09-15", "end_date": "2026-10-15",
    })
    assert summary.status_code == 409
    assert summary.json()["detail"]["months_by_client"]["alpha"] == ["Octubre 2026"]
    assert client.get("/api/reports/performance.xlsx", params={
        "start_date": "2026-09-15", "end_date": "2026-10-15",
    }).status_code == 409
    # Beta has never used groups, so missing snapshots do not block its reports.
    beta = client.get("/api/reporting/summary", params={
        "client_id": "beta", "start_date": "2026-10-01", "end_date": "2026-10-15",
    })
    assert beta.status_code == 200
    assert beta.json()["grouping_active"] is False


def test_snapshot_assignment_validation_rejects_duplicate_campaigns_and_allows_general(tmp_path):
    snapshots = MemoryReportingGroupSnapshotRepository()
    client, *_ = report_api(tmp_path, snapshot_repository=snapshots)
    assigned = {"platform": "google_ads", "account_id": "1001", "campaign_id": "campaign-1001"}
    duplicate = {"groups": [{"id": "conversion", "name": "Conversión", "type": "conversion"}], "assignments": [
        {**assigned, "reporting_group_id": "conversion"}, {**assigned, "reporting_group_id": None},
    ]}
    assert client.put("/api/reporting-group-snapshots/alpha/2026/9", json=duplicate).status_code == 422
    partial = {"groups": [{"id": "conversion", "name": "Conversión", "type": "conversion"}], "assignments": [
        {**assigned, "reporting_group_id": "conversion"},
        {"platform": "google_ads", "account_id": "1001", "campaign_id": "new-campaign", "reporting_group_id": None},
    ]}
    saved = client.put("/api/reporting-group-snapshots/alpha/2026/9", json=partial)
    assert saved.status_code == 200, saved.text
    assert saved.json()["assignments"][1]["reporting_group_id"] is None


def test_group_id_reserved_and_general_campaigns_remain_unassigned():
    try:
        ReportingGroupDraft(id="general", name="General", type="custom")
    except ValueError as exc:
        assert "reservado" in str(exc)
    else:
        raise AssertionError("general es una etiqueta de presentación, no un ID de grupo")
    draft = ReportingGroupDraft(id="custom-segment", name="Segmento especial", type="custom")
    assert draft.assignments == []


class _Doc:
    def __init__(self, store, doc_id): self.store, self.id = store, doc_id
    def set(self, value): self.store[self.id] = dict(value)
    def create(self, value):
        if self.id in self.store: raise ValueError("document already exists")
        self.store[self.id] = dict(value)
    def get(self): return self
    @property
    def exists(self): return self.id in self.store
    def to_dict(self): return dict(self.store[self.id])


class _Query:
    def __init__(self, store, client_id=None): self.store, self.client_id = store, client_id
    def where(self, _field, _operator, client_id): return _Query(self.store, client_id)
    def stream(self): return [_Doc(self.store, key) for key, value in self.store.items() if self.client_id is None or value["client_id"] == self.client_id]
    def document(self, doc_id): return _Doc(self.store, doc_id)


class _Batch:
    def __init__(self): self.operations = []
    def set(self, doc, value): self.operations.append((doc, value))
    def commit(self):
        for doc, value in self.operations: doc.set(value)


class _Db:
    def __init__(self): self.data = {}
    def collection(self, name): return _Query(self.data.setdefault(name, {}))
    def batch(self): return _Batch()


def test_firestore_groups_use_deterministic_upsert_and_inactivate_removed_groups():
    database = _Db()
    first = FirestoreReportingGroupRepository("performance_reporting_groups")
    first._client = database
    group = ReportingGroupDraft(id="conversion", name="Conversión", type="conversion")
    first.save_client_groups("blumia", [group])
    restarted = FirestoreReportingGroupRepository("performance_reporting_groups")
    restarted._client = database
    assert restarted.list("blumia")[0].name == "Conversión"
    assert set(database.data["performance_reporting_groups"]) == {"blumia__conversion"}
    restarted.save_client_groups("blumia", [])
    item = restarted.list("blumia")[0]
    assert item.active is False and item.assignments == []


def test_firestore_monthly_snapshot_repository_uses_deterministic_per_month_documents():
    from app.reporting_groups import (
        FirestoreReportingGroupSnapshotRepository,
        MonthlyCampaignAssignment,
        ReportingGroupSnapshotGroup,
    )

    repo = FirestoreReportingGroupSnapshotRepository("performance_reporting_group_snapshots")
    db = _Db()
    repo._client = db
    september = ReportingGroupSnapshot(
        client_id="alpha", year=2026, month=9,
        groups=[ReportingGroupSnapshotGroup(id="conversion", name="Conversión", type="conversion")],
        assignments=[MonthlyCampaignAssignment(platform="google_ads", account_id="1001", campaign_id="x", reporting_group_id="conversion")],
    )
    october = ReportingGroupSnapshot(
        client_id="alpha", year=2026, month=10,
        groups=[ReportingGroupSnapshotGroup(id="traffic", name="Tráfico", type="traffic")],
        assignments=[MonthlyCampaignAssignment(platform="google_ads", account_id="1001", campaign_id="x", reporting_group_id="traffic")],
    )
    repo.save(september)
    repo.save(october)
    assert repo.get("alpha", 2026, 9).assignments[0].reporting_group_id == "conversion"
    assert repo.get("alpha", 2026, 10).assignments[0].reporting_group_id == "traffic"
    assert set(db.data["performance_reporting_group_snapshots"]) == {"alpha__2026__09", "alpha__2026__10"}
    try:
        repo.create_if_absent(september)
    except Exception as exc:
        assert "Ya existe" in str(exc)
    else:
        raise AssertionError("copy must not overwrite an existing month snapshot")

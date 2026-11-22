"""Genera un XLSX local de aceptación con fixture Blumia, sin tocar servicios reales."""
from __future__ import annotations

from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory

from app.budgets import MemoryBudgetRepository, MonthlyBudget
from app.cache import TTLCache
from app.config import Settings
from app.metrics import derive
from app.models import ClientCreate, exclusive_claims
from app.registry import JsonClientRepository
from app.reporting import generate_performance_report
from app.reporting_groups import ReportingGroup, CampaignGroupAssignment
from app.service import DashboardService


class GoogleFixture:
    def performance(self, customer_id, login_customer_id, start, end, campaign_ids=None):
        del login_customer_id, start, end
        rows = [
            {**derive({"spend": 1200, "conversions": 5, "conversion_value": 2500, "clicks": 100, "impressions": 1800}), "campaign_id": "1101", "campaign_name": "Marca institucional", "objective": "AWARENESS"},
            {**derive({"spend": 2400, "conversions": 12, "conversion_value": 9600, "clicks": 240, "impressions": 4200}), "campaign_id": "1102", "campaign_name": "Campaña X", "objective": "SALES"},
        ]
        if campaign_ids is not None:
            rows = [row for row in rows if row["campaign_id"] in set(campaign_ids)]
        totals = {key: sum(row.get(key, 0) for row in rows) for key in ("spend", "conversions", "conversion_value", "clicks", "impressions")}
        return {"currency": "MXN", "account_name": f"Fixture {customer_id}", "totals": derive(totals), "campaigns": rows, "daily": []}

    def list_campaigns(self, customer_id, login_customer_id=None):
        del customer_id, login_customer_id
        return []


class MetaFixture:
    def performance(self, account_id, start, end, campaign_ids=None):
        del start, end
        rows = [
            {**derive({"spend": 1800, "conversions": 9, "conversion_value": 7200, "clicks": 180, "impressions": 3600, "reach": 2100}), "campaign_id": "2201", "campaign_name": "Ventas catálogo", "objective": "SALES"},
            {**derive({"spend": 900, "conversions": 1, "conversion_value": 400, "clicks": 260, "impressions": 5100, "reach": 3000}), "campaign_id": "2202", "campaign_name": "Awareness tráfico", "objective": "TRAFFIC"},
        ]
        if campaign_ids is not None:
            rows = [row for row in rows if row["campaign_id"] in set(campaign_ids)]
        totals = {key: sum(row.get(key, 0) for row in rows) for key in ("spend", "conversions", "conversion_value", "clicks", "impressions", "reach")}
        return {"currency": "MXN", "account_name": f"Fixture {account_id}", "totals": derive(totals), "campaigns": rows, "daily": []}

    def adsets(self, account_id, start, end, campaign_ids=None, adset_ids=None):
        del account_id, start, end, campaign_ids, adset_ids
        return []


def main() -> None:
    output = Path(__file__).resolve().parents[2] / ".artifacts" / "Reporte_Performance_BLUMIA_2026-09-01_al_2026-10-31_SNAPSHOT_MENSUAL.xlsx"
    output.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix="reporting-groups-fixture-") as temporary:
        settings = Settings(store_backend="memory", clients_file=Path(temporary) / "clients.json", static_dir=Path(temporary) / "dist")
        clients_repo = JsonClientRepository(settings.clients_file)
        client = clients_repo.create(ClientCreate(
            name="Blumia", currency="MXN",
            google_ads_sources=[{"customer_id": "1001", "campaign_ids": [], "allocation": "exclusive"}],
            meta_ads_sources=[{"ad_account_id": "2002", "campaign_ids": [], "allocation": "exclusive"}],
        ))
        campaign_x = CampaignGroupAssignment(platform="google_ads", account_id="1001", campaign_id="1102")
        september_groups = [ReportingGroup(client_id=client.id, id="campaign-purpose", name="Conversión", type="conversion", assignments=[campaign_x])]
        october_groups = [ReportingGroup(client_id=client.id, id="campaign-purpose", name="Tráfico", type="traffic", assignments=[campaign_x])]
        budgets = MemoryBudgetRepository([
            MonthlyBudget(client_id=client.id, platform="google_ads", year=2026, month=9, reporting_group_id="campaign-purpose", budget=35000, currency="MXN"),
            MonthlyBudget(client_id=client.id, platform="google_ads", year=2026, month=10, reporting_group_id="campaign-purpose", budget=10000, currency="MXN"),
        ])
        service = DashboardService(
            {"google_ads": GoogleFixture(), "meta_ads": MetaFixture()},
            TTLCache(0), TTLCache(0),
        )
        result = generate_performance_report(
            service, [client], budgets, date(2026, 9, 1), date(2026, 10, 31),
            exclusive_claims([client]), reporting_groups_by_client_month={
                (client.id, 2026, 9): september_groups,
                (client.id, 2026, 10): october_groups,
            },
        )
        output.write_bytes(result.content)
    print(output)


if __name__ == "__main__":
    main()

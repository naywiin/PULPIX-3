from __future__ import annotations

from datetime import date

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.models import Client, PinterestAdsSource, account_ids
from app.main import create_app
from app.registry import JsonClientRepository
from app.providers.pinterest import (
    PinterestAdsProvider,
    PinterestAuthError,
    PinterestPermissionError,
)


class Response:
    def __init__(self, payload, status=200, headers=None):
        self._payload = payload
        self.status_code = status
        self.headers = headers or {}

    def json(self):
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


class Session:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self.responses.pop(0)


def provider(monkeypatch, responses):
    monkeypatch.setenv("PINTEREST_ACCESS_TOKEN", "test-token-never-log")
    session = Session(responses)
    return PinterestAdsProvider(Settings(), session), session


def test_lists_real_ad_accounts_and_campaign_status_mapping(monkeypatch):
    api, session = provider(monkeypatch, [
        Response({"items": [{"id": "549763101458", "name": "SSIENTO COMERCIAL", "currency": "MXN",
                             "status": "ACTIVE", "permissions": ["ANALYST"]}]}),
        Response({"items": [{"id": "c1", "name": "Checkout", "status": "PAUSED", "objective_type": "WEB_CONVERSION"}]}),
    ])
    accounts = api.list_accounts()
    campaigns = api.list_campaigns("549763101458")
    assert accounts[0]["id"] == "549763101458"
    assert accounts[0]["currency"] == "MXN"
    assert accounts[0]["permissions"] == ["ANALYST"]
    assert campaigns[0]["campaign_id"] == "c1"
    assert campaigns[0]["status"] == "PAUSED"
    assert session.calls[0][1]["headers"]["Authorization"] == "Bearer test-token-never-log"


def test_normalizes_pinterest_analytics_micro_values_once_and_derives_ratios(monkeypatch):
    api, _ = provider(monkeypatch, [
        Response({"items": [{"id": "c1", "name": "Checkout", "status": "ACTIVE", "currency": "MXN"}]}),
        Response({"items": [{
            "SPEND_IN_MICRO_DOLLAR": 100_000_000, "IMPRESSION_1": 2_000,
            "CLICKTHROUGH_1": 100, "TOTAL_CHECKOUT": 5,
            "TOTAL_CHECKOUT_VALUE_IN_MICRO_DOLLAR": 500_000_000,
        }]}),
        Response({"items": [{"CAMPAIGN_ID": "c1", "SPEND_IN_MICRO_DOLLAR": 100_000_000,
            "IMPRESSION_1": 2_000, "CLICKTHROUGH_1": 100, "TOTAL_CHECKOUT": 5,
            "TOTAL_CHECKOUT_VALUE_IN_MICRO_DOLLAR": 500_000_000}]}),
        Response({"items": [{
            "DATE": "2026-10-01",
            "SPEND_IN_MICRO_DOLLAR": 100_000_000, "IMPRESSION_1": 2_000,
            "CLICKTHROUGH_1": 100, "TOTAL_CHECKOUT": 5,
            "TOTAL_CHECKOUT_VALUE_IN_MICRO_DOLLAR": 500_000_000,
        }]}),
    ])
    result = api.performance("549763101458", "2026-10-01", "2026-10-01")
    totals = result["totals"]
    assert totals["spend"] == 100
    assert totals["impressions"] == 2000
    assert totals["clicks"] == 100
    assert totals["ctr"] == pytest.approx(0.05)
    assert totals["cpc"] == pytest.approx(1)
    assert totals["checkouts"] == 5
    assert totals["conversions"] is None
    assert totals["cost_per_checkout"] == pytest.approx(20)
    assert totals["conversion_value"] == 500
    assert totals["roas"] == pytest.approx(5)
    assert result["daily"][0]["date"] == "2026-10-01"
    assert result["warnings"] == []


def test_analytics_sends_required_campaign_ids_and_correct_checkout_columns(monkeypatch):
    api, session = provider(monkeypatch, [
        Response({"items": [{"id": "c1", "name": "Checkout", "status": "ACTIVE", "currency": "MXN"}]}),
        Response({"items": [{"CAMPAIGN_ID": "c1", "TOTAL_CHECKOUT": 0}]}),
        Response({"items": []}),
        Response({"items": []}),
    ])
    api.performance("549763101458", "2026-10-01", "2026-10-14")
    total_params = session.calls[1][1]["params"]
    campaign_params = session.calls[2][1]["params"]
    daily_params = session.calls[3][1]["params"]
    assert session.calls[1][0] == "https://api.pinterest.com/v5/ad_accounts/549763101458/analytics"
    assert session.calls[1][1]["headers"]["Accept"] == "application/json"
    assert session.calls[1][1]["headers"]["Authorization"].startswith("Bearer ")
    for params, granularity in ((total_params, "TOTAL"), (daily_params, "DAY")):
        assert params["start_date"] == "2026-10-01"
        assert params["end_date"] == "2026-10-14"
        assert params["granularity"] == granularity
        assert "TOTAL_CHECKOUT" in params["columns"]
        assert "TOTAL_CHECKOUT_VALUE_IN_MICRO_DOLLAR" in params["columns"]
        assert "TOTAL_CONVERSIONS_VALUE_IN_MICRO_DOLLAR" not in params["columns"]
        assert "CAMPAIGN_ID" not in params["columns"]
        assert "DATE" not in params["columns"]
        assert "campaign_ids" not in params
    assert session.calls[2][0] == "https://api.pinterest.com/v5/ad_accounts/549763101458/campaigns/analytics"
    assert campaign_params["campaign_ids"] == "c1"
    assert campaign_params["granularity"] == "TOTAL"


def test_total_summary_survives_daily_series_failure(monkeypatch):
    api, _ = provider(monkeypatch, [
        Response({"items": [{"id": "c1", "name": "Checkout", "status": "ACTIVE", "currency": "MXN"}]}),
        Response({"items": [{"CAMPAIGN_ID": "c1", "SPEND_IN_MICRO_DOLLAR": 100_000_000,
                              "IMPRESSION_1": 2000, "CLICKTHROUGH_1": 100, "TOTAL_CHECKOUT": 5,
                              "TOTAL_CHECKOUT_VALUE_IN_MICRO_DOLLAR": 500_000_000}]}),
        Response({"items": [{"CAMPAIGN_ID": "c1", "SPEND_IN_MICRO_DOLLAR": 100_000_000,
                              "TOTAL_CHECKOUT": 5, "TOTAL_CHECKOUT_VALUE_IN_MICRO_DOLLAR": 500_000_000}]}),
        Response({"message": "daily unavailable"}, status=400),
    ])
    result = api.performance("549763101458", "2026-10-01", "2026-10-14")
    assert result["totals"]["spend"] == 100
    assert result["totals"]["checkouts"] == 5
    assert result["totals"]["conversion_value"] == 500
    assert result["daily"] == []
    assert result["warnings"][0]["status"] == "daily_series_partial"


def test_campaign_without_analytics_is_kept_null_and_summary_remains_available(monkeypatch):
    api, _ = provider(monkeypatch, [
        Response({"items": [
            {"id": "c1", "name": "Has data", "status": "ACTIVE", "currency": "MXN"},
            {"id": "c2", "name": "No row", "status": "PAUSED", "currency": "MXN"},
        ]}),
        Response({"items": [{"SPEND_IN_MICRO_DOLLAR": 50_000_000, "TOTAL_CHECKOUT": 2}]}),
        Response({"items": [{"CAMPAIGN_ID": "c1", "SPEND_IN_MICRO_DOLLAR": 50_000_000, "TOTAL_CHECKOUT": 2}]}),
        Response({"items": [{"DATE": "2026-10-01", "SPEND_IN_MICRO_DOLLAR": 50_000_000}]}),
    ])
    result = api.performance("549763101458", "2026-10-01", "2026-10-14")
    assert result["totals"]["spend"] == 50
    assert len(result["campaigns"]) == 2
    missing = next(row for row in result["campaigns"] if row["campaign_id"] == "c2")
    assert missing["spend"] is None
    assert missing["checkouts"] is None
    assert result["warnings"] == []


def test_campaign_analytics_error_keeps_summary_available(monkeypatch):
    api, _ = provider(monkeypatch, [
        Response({"items": [{"id": "c1", "name": "Checkout"}]}),
        Response({"items": [{"SPEND_IN_MICRO_DOLLAR": 100_000_000, "TOTAL_CHECKOUT": 5}]}),
        Response({"message": "Parameter 'campaign_ids' is required."}, status=400),
        Response({"items": [{"DATE": "2026-10-01", "SPEND_IN_MICRO_DOLLAR": 100_000_000}]}),
    ])
    result = api.performance("549763101458", "2026-10-01", "2026-10-14")
    assert result["totals"]["spend"] == 100
    assert result["totals"]["checkouts"] == 5
    assert result["warnings"][0]["status"] == "campaign_analytics_partial"


def test_missing_metrics_stay_null_and_measured_zero_stays_zero(monkeypatch):
    api, _ = provider(monkeypatch, [
        Response({"items": [{"id": "c1", "name": "No data", "status": "UNKNOWN", "currency": "MXN"}]}),
        Response({"items": [{"SPEND_IN_MICRO_DOLLAR": 0, "TOTAL_CHECKOUT": 0}]}),
        Response({"items": [{"CAMPAIGN_ID": "c1", "SPEND_IN_MICRO_DOLLAR": 0, "TOTAL_CHECKOUT": 0}]}),
        Response({"items": [{"DATE": "2026-10-01",
                              "SPEND_IN_MICRO_DOLLAR": 0, "TOTAL_CHECKOUT": 0}]}),
    ])
    metrics = api.performance("1", "2026-10-01", "2026-10-01")["totals"]
    assert metrics["spend"] == 0
    assert metrics["checkouts"] == 0
    assert metrics["impressions"] is None
    assert metrics["clicks"] is None
    assert metrics["conversion_value"] is None
    assert metrics["cpc"] is None
    assert metrics["cost_per_checkout"] is None


def test_auth_and_permission_errors_are_distinct_and_sanitized(monkeypatch):
    api, _ = provider(monkeypatch, [Response({"message": "invalid"}, status=401)])
    with pytest.raises(PinterestAuthError):
        api.list_accounts()
    api, _ = provider(monkeypatch, [Response({"message": "not allowed"}, status=403)])
    with pytest.raises(PinterestPermissionError):
        api.list_accounts()


def test_rate_limit_uses_bounded_retry_after(monkeypatch):
    api, session = provider(monkeypatch, [
        Response({}, status=429, headers={"Retry-After": "0"}),
        Response({"items": []}),
    ])
    waits = []
    monkeypatch.setattr("app.providers.pinterest.time.sleep", waits.append)
    assert api.list_accounts() == []
    assert len(session.calls) == 2
    assert waits == [0.25]


def test_source_is_persistable_and_client_account_ids_include_pinterest():
    client = Client.model_validate({
        "id": "ssiento", "name": "Ssiento", "slug": "ssiento",
        "pinterest_ads_sources": [{"source_id": "549763101458", "ad_account_id": "549763101458",
                                    "display_name": "SSIENTO COMERCIAL", "currency": "MXN"}],
    })
    assert account_ids(client, "pinterest_ads") == ["549763101458"]
    restored = Client.model_validate(client.model_dump())
    assert restored.pinterest_ads_sources[0].currency == "MXN"


def test_provider_requires_runtime_secret_without_echoing_values(monkeypatch):
    monkeypatch.delenv("PINTEREST_ACCESS_TOKEN", raising=False)
    api = PinterestAdsProvider(Settings(), Session([]))
    from app.providers.base import ProviderNotConfigured
    with pytest.raises(ProviderNotConfigured):
        api.list_accounts()


def test_pinterest_source_persists_and_dashboard_and_api_expose_normalized_metrics(tmp_path):
    class Pinterest:
        def __init__(self):
            self.calls = []
            self.partial = False

        def list_accounts(self):
            return [{"platform": "pinterest_ads", "id": "549763101458", "name": "SSIENTO COMERCIAL",
                     "currency": "MXN", "status": "ACTIVE", "permissions": ["ANALYST"], "account_type": "ad_account"}]

        def list_campaigns(self, account_id):
            assert account_id == "549763101458"
            return [{"platform": "pinterest_ads", "campaign_id": "p1", "campaign_name": "Checkout", "status": "ACTIVE"}]

        def performance(self, account_id, start, end, campaign_ids=None):
            self.calls.append((account_id, start, end, campaign_ids))
            row = {"campaign_id": "p1", "campaign_name": "Checkout", "status": "ACTIVE", "spend": 100.0,
                   "impressions": 2000, "clicks": 100, "ctr": .05, "cpc": 1, "checkouts": 5,
                   "cost_per_checkout": 20, "conversion_value": 500, "roas": 5, "currency": "MXN"}
            return {"currency": "MXN", "totals": {key: row.get(key) for key in
                    ("spend", "impressions", "clicks", "ctr", "cpc", "checkouts", "cost_per_checkout", "conversion_value", "roas", "currency")},
                    "campaigns": [row], "daily": [{"date": start, **row}], "sources_count": 1,
                    "warnings": ([{"status": "daily_series_partial", "message": "Serie diaria incompleta."}]
                                 if self.partial else [])}

    pinterest = Pinterest()
    providers = {"google_ads": object(), "meta_ads": object(), "spotify_ads": object(), "pinterest_ads": pinterest, "ga4": object()}
    settings = Settings(clients_file=tmp_path / "clients.json", static_dir=tmp_path / "nodist")
    api = TestClient(create_app(settings, JsonClientRepository(settings.clients_file), providers))
    api.post("/api/clients", json={"name": "Ssiento"})
    account = api.get("/api/accounts/pinterest-ads").json()
    assert account["status"] == "ok"
    assert account["accounts"][0]["id"] == "549763101458"
    campaigns = api.get("/api/accounts/pinterest-ads/549763101458/campaigns").json()
    assert campaigns["campaigns"][0]["campaign_id"] == "p1"
    linked = api.put("/api/clients/ssiento", json={"pinterest_ads_sources": [{
        "source_id": "549763101458", "ad_account_id": "549763101458", "display_name": "SSIENTO COMERCIAL",
        "currency": "MXN", "status": "ACTIVE", "campaign_ids": [], "allocation": "exclusive",
    }]})
    assert linked.status_code == 200
    client_payload = api.get("/api/clients/ssiento").json()
    assert client_payload["pinterest_linked"] is True
    assert client_payload["pinterest_source_id"] == "549763101458"
    dashboard = api.get("/api/dashboard/ssiento", params={"start_date": "2026-10-01", "end_date": "2026-10-01"}).json()
    pinterest_block = dashboard["platforms"]["pinterest_ads"]
    assert pinterest_block["status"] == "ok"
    assert pinterest_block["current"]["totals"]["checkouts"] == 5
    assert pinterest_block["current"]["totals"]["conversion_value"] == 500
    assert pinterest.calls
    pinterest.partial = True
    partial_response = api.get("/api/pinterest-ads/performance", params={
        "client_id": "ssiento", "start_date": "2026-10-01", "end_date": "2026-10-01", "refresh": "true",
    })
    assert partial_response.status_code == 200
    assert partial_response.json()["status"] == "partial"
    assert partial_response.json()["current"]["totals"]["checkouts"] == 5
    assert partial_response.json()["warnings"][0]["status"] == "daily_series_partial"
    # La fuente sigue persistida al recrear el repositorio desde el mismo archivo.
    restored = JsonClientRepository(settings.clients_file).get("ssiento")
    assert account_ids(restored, "pinterest_ads") == ["549763101458"]

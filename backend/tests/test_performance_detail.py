from types import SimpleNamespace

import pytest

from app.performance_detail import build_detail_response, current_entity_status, filter_detail_rows


def test_meta_cost_per_result_uses_leads_and_zero_is_null():
    row = {"effective_status": "ACTIVE", "spend": 1000, "leads": 10,
           "conversions": 2, "clicks": 100, "impressions": 1000, "conversion_value": 500}
    shown = filter_detail_rows([row])[0]
    assert shown["cpl"] == 100
    assert shown["cpa"] == 500  # compra histórica: distinta del costo por resultado
    assert shown["status_label"] == "Activo"
    assert filter_detail_rows([{**row, "leads": 0}])[0]["cpl"] is None
    assert filter_detail_rows([{**row, "value_available": False}])[0]["roas"] is None
from app.providers.google_ads import GoogleAdsProvider
from app.providers.meta import MetaProvider
from app.config import Settings


def row(status, spend, **extra):
    return {
        "status": status,
        "spend": spend,
        "impressions": 100,
        "clicks": 10,
        "conversions": 2,
        "leads": None,
        "conversion_value": 200,
        **extra,
    }


@pytest.mark.parametrize("status,expected", [
    ("all", {"enabled", "paused", "removed", "active", "campaign_paused", "adset_paused"}),
    ("active", {"enabled", "active"}),
    ("inactive", {"paused", "removed", "campaign_paused", "adset_paused"}),
])
def test_status_filter_uses_current_platform_status(status, expected):
    rows = [
        row("ENABLED", 1, campaign_id="1"),
        row("PAUSED", 2, campaign_id="2"),
        row("REMOVED", 3, campaign_id="3"),
        row("ACTIVE", 4, effective_status="ACTIVE", campaign_id="4"),
        row("ACTIVE", 5, effective_status="CAMPAIGN_PAUSED", campaign_id="5"),
        row("ACTIVE", 6, effective_status="ADSET_PAUSED", campaign_id="6"),
    ]
    assert {current_entity_status(item).lower() for item in filter_detail_rows(rows, status=status)} == expected


@pytest.mark.parametrize("status,spend_filter,expected", [
    ("all", "all", {"active_spend", "active_zero", "paused_spend", "paused_zero", "unknown"}),
    ("active", "all", {"active_spend", "active_zero", "unknown"}),
    ("inactive", "all", {"paused_spend", "paused_zero"}),
    ("all", "with_spend", {"active_spend", "paused_spend"}),
    ("all", "without_spend", {"active_zero", "paused_zero"}),
    ("active", "with_spend", {"active_spend"}),
    ("active", "without_spend", {"active_zero"}),
    ("inactive", "with_spend", {"paused_spend"}),
    ("inactive", "without_spend", {"paused_zero"}),
])
def test_status_and_spend_filters_are_independent(status, spend_filter, expected):
    rows = [
        row("ENABLED", 10, id="active_spend"),
        row("ENABLED", 0, id="active_zero"),
        row("PAUSED", 5, id="paused_spend"),
        row("PAUSED", 0, id="paused_zero"),
        row("ACTIVE", None, id="unknown"),
    ]
    assert {item["id"] for item in filter_detail_rows(rows, status=status, spend=spend_filter)} == expected


def test_missing_status_is_not_assumed_inactive():
    unknown = row(None, 25, campaign_id="unknown")
    assert filter_detail_rows([unknown], status="all", spend="with_spend")
    assert not filter_detail_rows([unknown], status="active", spend="all")
    assert not filter_detail_rows([unknown], status="inactive", spend="all")


@pytest.mark.parametrize("platform_status", [
    "PAUSED", "REMOVED", "ARCHIVED", "DELETED", "CAMPAIGN_PAUSED", "ADSET_PAUSED", "WITH_ISSUES",
])
def test_non_active_provider_states_can_retain_historical_spend(platform_status):
    item = row(platform_status, 500)
    assert filter_detail_rows([item], status="inactive", spend="with_spend")
    assert not filter_detail_rows([item], status="active", spend="with_spend")


def test_totals_use_only_visible_base_metrics_and_protect_zero_divisors():
    result = build_detail_response(
        [row("PAUSED", 500, id="one"), row("ENABLED", 200, id="two")],
        client_id="client", platform="google_ads", level="campaign",
        status="inactive", spend="with_spend", start_date="2026-10-01", end_date="2026-10-05",
    )
    assert result["count"] == 1
    assert result["totals"]["spend"] == 500
    assert result["totals"]["cpc"] == 50
    assert result["totals"]["ctr"] == 0.1
    assert result["totals"]["cpa"] == 250
    assert result["totals"]["cpl"] is None

    zero = build_detail_response(
        [row("ENABLED", 0, impressions=0, clicks=0, conversions=0, conversion_value=0)],
        client_id="client", platform="meta_ads", level="ad",
        status="all", spend="without_spend", start_date="2026-10-01", end_date="2026-10-05",
    )
    assert zero["count"] == 1
    assert all(zero["totals"][field] is None for field in ("cpc", "ctr", "cpa", "cpl", "roas"))


def test_daily_campaign_metrics_survive_detail_filtering_for_overview_charts():
    daily = [
        {"date": "2026-10-01", "spend": 30, "conversions": 2, "purchases": 1, "leads": 4},
        {"date": "2026-10-02", "spend": 0, "conversions": 0, "purchases": 0, "leads": 0},
    ]
    response = build_detail_response(
        [row("ENABLED", 30, campaign_id="10", conversions=2, purchases=1, leads=4, daily=daily)],
        client_id="cirque-du-soleil-joya", platform="meta_ads", level="campaign",
        status="all", spend="all", start_date="2026-10-01", end_date="2026-10-02",
    )
    assert response["rows"][0]["daily"] == daily


class FakeGoogleRow:
    def __init__(self, ad_id, ad_status, campaign_status="ENABLED", spend=0):
        self.campaign = SimpleNamespace(id=10, name="Campaign", status=campaign_status)
        self.ad_group = SimpleNamespace(id=20, name="Ad group")
        self.ad_group_ad = SimpleNamespace(
            ad=SimpleNamespace(id=ad_id, name=f"Ad {ad_id}", type=SimpleNamespace(name="RESPONSIVE_SEARCH_AD")),
            status=SimpleNamespace(name=ad_status),
        )
        self.customer = SimpleNamespace(currency_code="MXN")
        self.metrics = SimpleNamespace(
            impressions=100 if spend else 0, clicks=10 if spend else 0,
            cost_micros=spend * 1_000_000, conversions=1 if spend else 0,
            conversions_value=50 if spend else 0,
        )


def test_google_ads_detail_uses_ad_group_ad_status_and_keeps_zero_spend_ads():
    provider = GoogleAdsProvider(Settings())
    calls = []
    provider._search = lambda account, query, login=None: calls.append(query) or (
        [FakeGoogleRow("301", "ENABLED", spend=10), FakeGoogleRow("302", "PAUSED")]
        if "metrics.cost_micros" in query else
        [FakeGoogleRow("301", "ENABLED", spend=10), FakeGoogleRow("302", "PAUSED")]
    )
    ads = provider.detail_ads("1110000111", "7954113535", "2026-10-01", "2026-10-05", ["10"])
    assert len(ads) == 2
    assert {item["ad_id"]: item["status"] for item in ads} == {"301": "ENABLED", "302": "PAUSED"}
    assert next(item for item in ads if item["ad_id"] == "302")["spend"] == 0
    assert all("campaign.id IN (10)" in query for query in calls if "FROM conversion_action" not in query)
    assert "WHERE campaign.id IN (10)" in calls[0]
    assert "WHERE segments.date BETWEEN '2026-10-01' AND '2026-10-05'" in calls[1]


def test_mixed_currency_does_not_add_money_or_money_ratios():
    response = build_detail_response(
        [row("ENABLED", 100, currency="MXN"), row("PAUSED", 10, currency="USD")],
        client_id="client", platform="google_ads", level="campaign",
        status="all", spend="all", start_date="2026-10-01", end_date="2026-10-05",
    )
    assert response["mixed_currency"] is True
    assert response["totals"]["spend"] is None
    assert response["totals"]["conversion_value"] is None
    assert response["totals"]["ctr"] == 0.1
    assert response["totals"]["cpc"] is None
    assert response["totals"]["roas"] is None


class FakeMeta:
    def __init__(self):
        self.insight_calls = []
        self.catalog_params = None

    def _paged(self, path, params):
        self.catalog_params = (path, params)
        return [{
            "id": "ad-1", "name": "Creative", "status": "ACTIVE", "effective_status": "CAMPAIGN_PAUSED",
            "campaign": {"id": "campaign-1", "name": "Sales", "status": "ACTIVE", "effective_status": "PAUSED"},
            "adset": {"id": "set-1", "name": "Set", "status": "ACTIVE", "effective_status": "ACTIVE"},
        }]

    def _insights(self, account, start, end, level, fields, campaign_ids=None, adset_ids=None, **extra):
        self.insight_calls.append((level, campaign_ids, adset_ids))
        return [{
            "ad_id": "ad-1", "ad_name": "Creative", "campaign_id": "campaign-1", "campaign_name": "Sales",
            "adset_id": "set-1", "adset_name": "Set", "spend": "20", "impressions": "50", "clicks": "4",
            "actions": [], "action_values": [], "account_currency": "MXN",
        }]


def test_meta_detail_queries_level_ad_and_prefers_effective_status():
    provider = MetaProvider(Settings())
    fake = FakeMeta()
    provider._paged = fake._paged
    provider._insights = fake._insights
    catalog = provider.list_ads("123", campaign_ids=["campaign-1"], adset_ids=["set-1"])
    insight = provider.performance_ads("123", "2026-10-01", "2026-10-05", ["campaign-1"], ["set-1"])
    assert fake.catalog_params[0] == "act_123/ads"
    assert "effective_status" in fake.catalog_params[1]["fields"]
    assert fake.insight_calls == [("ad", ["campaign-1"], ["set-1"])]
    assert catalog[0]["effective_status"] == "CAMPAIGN_PAUSED"
    assert insight[0]["spend"] == 20
    assert filter_detail_rows([{**catalog[0], **insight[0]}], status="inactive", spend="with_spend")
    assert not filter_detail_rows([{**catalog[0], **insight[0]}], status="active", spend="all")


def test_provider_errors_are_not_converted_to_empty_rows():
    from app.providers.base import ProviderError

    provider = FakeMeta()
    provider.performance_ads = lambda *args, **kwargs: (_ for _ in ()).throw(ProviderError("offline"))
    with pytest.raises(ProviderError):
        provider.performance_ads("123", "2026-10-01", "2026-10-05")

from types import SimpleNamespace as NS

from app.config import Settings
from app.providers.google_ads import GoogleAdsProvider


def _row(campaign_id, day, action, conversions, *, impressions=10, clicks=2, spend=5):
    return NS(
        metrics=NS(
            impressions=impressions, clicks=clicks, cost_micros=spend * 1_000_000,
            conversions=conversions, all_conversions=conversions,
            conversions_value=20, all_conversions_value=20,
        ),
        customer=NS(currency_code="MXN"),
        campaign=NS(id=campaign_id, name=f"Campaign {campaign_id}", status=NS(name="ENABLED"), advertising_channel_type=NS(name="SEARCH")),
        segments=NS(date=day, conversion_action=action),
    )


def test_google_leads_count_only_primary_acquisition_actions_and_keep_general_conversions():
    provider = GoogleAdsProvider(Settings())
    actions = [
        NS(conversion_action=NS(resource_name="actions/lead", category=NS(name="SUBMIT_LEAD_FORM"), primary_for_goal=True)),
        NS(conversion_action=NS(resource_name="actions/contact", category=NS(name="CONTACT"), primary_for_goal=True)),
        NS(conversion_action=NS(resource_name="actions/qualified", category=NS(name="QUALIFIED_LEAD"), primary_for_goal=True)),
        NS(conversion_action=NS(resource_name="actions/secondary", category=NS(name="LEAD"), primary_for_goal=False)),
    ]
    results = [
        _row("11", "2026-10-01", "actions/lead", 2),
        _row("11", "2026-10-01", "actions/contact", 3),
        _row("11", "2026-10-01", "actions/qualified", 100),
        _row("11", "2026-10-01", "actions/secondary", 200),
    ]
    def search(_customer, query, _login):
        if "FROM conversion_action" in query:
            return actions
        if "segments.conversion_action" in query:
            return results
        return [_row("11", "2026-10-01", "", 9)]
    provider._search = search

    report = provider.performance("123", None, "2026-10-01", "2026-10-05")

    assert report["totals"]["conversions"] == 9
    assert report["totals"]["leads"] == 5
    assert report["campaigns"][0]["conversions"] == 9
    assert report["campaigns"][0]["leads"] == 5
    assert report["daily"][0]["leads"] == 5


def test_google_leads_are_null_when_no_acquisition_action_is_configured():
    provider = GoogleAdsProvider(Settings())
    def search(_customer, query, _login):
        if "FROM conversion_action" in query:
            return [NS(conversion_action=NS(resource_name="actions/qualified", category=NS(name="QUALIFIED_LEAD"), primary_for_goal=True))]
        if "segments.conversion_action" in query:
            return []
        return [_row("11", "2026-10-01", "", 4)]
    provider._search = search

    report = provider.performance("123", None, "2026-10-01", "2026-10-05")

    assert report["totals"]["leads"] is None
    assert report["campaigns"][0]["leads"] is None

from datetime import date

from app.budgets import MonthlyBudget
from app.reporting import DataLine, MonthSlice, ReportLine, _effective_budget, _summary_record, _budget_key, observed_days_in_period


def _line(platform, spend, conversions, leads, purchases, value, clicks=10, client_id="cirque-du-soleil-joya"):
    return ReportLine(
        period=MonthSlice(date(2026, 10, 1), date(2026, 10, 5), 2026, 10, 31, 5),
        client_id=client_id, client_name="JOYÀ" if client_id == "cirque-du-soleil-joya" else "Ssiento", platform=platform,
        account_ids=["account"], currency="MXN", status="ok", spend=spend,
        conversions=conversions, leads=leads, purchases=purchases,
        conversion_value=value, clicks=clicks, impressions=100, reach=None,
        reach_is_sum=False,
    )


def test_summary_uses_google_conversions_plus_canonical_meta_purchase_once():
    summary = _summary_record([
        _line("google_ads", 1000, 10, None, None, 5000),
        _line("meta_ads", 1400, 99, 25, 7, 7000),
    ], {}, level="period_total", period=MonthSlice(date(2026, 10, 1), date(2026, 10, 5), 2026, 10, 31, 5))

    assert summary["total_events"] == 17
    assert summary["total_events_available"] is True
    assert summary["total_cpa"] == 2400 / 17
    assert summary["total_cpc"] == 2400 / 20
    assert summary["projected_sales_value"] == 12000 / 5 * 31


def test_summary_keeps_available_events_and_marks_missing_meta_result_partial():
    summary = _summary_record([
        _line("google_ads", 1000, 10, None, None, 5000),
        _line("meta_ads", 1400, 99, 25, None, 7000),
    ], {}, level="period_total", period=MonthSlice(date(2026, 10, 1), date(2026, 10, 5), 2026, 10, 31, 5))

    assert summary["total_events"] == 10
    assert summary["total_events_available"] is True
    assert summary["total_events_partial"] is True
    assert summary["total_event_sources"] == ["google_ads"]
    assert summary["total_cpa"] == 100
    assert summary["total_cpa_partial"] is True
    assert summary["total_cpa_sources"] == ["google_ads"]


def test_ssiento_totals_google_conversions_plus_meta_purchases_and_purchase_value():
    summary = _summary_record([
        _line("google_ads", 10976.46, 38.58816, None, None, 77356.84, client_id="ssiento"),
        # Generic Meta conversions are ignored; canonical purchases and their value count once.
        _line("meta_ads", 27222.97, 99, None, 28, 61445.00, client_id="ssiento"),
    ], {}, level="period_total", period=MonthSlice(date(2026, 10, 1), date(2026, 10, 5), 2026, 10, 31, 5))

    assert summary["total_events"] == 66.58816
    assert summary["total_events_partial"] is False
    assert summary["total_cpa"] == (10976.46 + 27222.97) / (38.58816 + 28)
    assert summary["conversion_value"] == 138801.84
    assert summary["conversion_value_partial"] is False
    assert summary["conversion_value_sources"] == ["google_ads", "meta_ads"]
    assert summary["roas"] == 138801.84 / (10976.46 + 27222.97)
    assert summary["roas_partial"] is False


def test_ssiento_missing_meta_purchase_is_partial_and_does_not_use_leads_or_generic_conversions():
    summary = _summary_record([
        _line("google_ads", 1000, 10, None, None, 100000, client_id="ssiento"),
        _line("meta_ads", 2000, 99, 20, None, 30000, client_id="ssiento"),
    ], {}, level="period_total", period=MonthSlice(date(2026, 10, 1), date(2026, 10, 5), 2026, 10, 31, 5))

    assert summary["total_events"] == 10
    assert summary["total_events_partial"] is True
    assert summary["total_cpa"] == 100
    assert summary["total_cpa_partial"] is True
    assert summary["conversion_value"] == 100000
    assert summary["conversion_value_partial"] is True
    assert summary["roas_partial"] is True
    assert summary["roas"] == 100


def test_blumia_uses_meta_purchases_even_when_leads_are_missing():
    summary = _summary_record([
        _line("google_ads", 1000, 10, None, None, 5000, client_id="blumia"),
        _line("meta_ads", 2000, 999, None, 5, 3000, client_id="blumia"),
    ], {}, level="period_total", period=MonthSlice(date(2026, 10, 1), date(2026, 10, 5), 2026, 10, 31, 5))
    assert summary["total_events"] == 15
    assert summary["total_events_partial"] is False
    assert summary["total_cpa"] == 200
    assert summary["conversion_value"] == 8000
    assert summary["roas"] == 8000 / 3000


def test_group_budget_is_cap_and_campaign_budgets_are_fallback_allocations():
    line = _line("google_ads", 500, 3, None, None, 0)
    line.reporting_group_id = "conversion"
    data = [
        DataLine(period=line.period, client_id=line.client_id, client_name=line.client_name, platform="google_ads",
                 account_id="acct", account_name=None, level="CAMPAÑA", campaign_id="c1", campaign_name="One",
                 objective=None, spend=200, conversions=1, leads=None, conversion_value=0, clicks=2, impressions=20,
                 reach=None, currency="MXN", status="ok", reporting_group_id="conversion"),
        DataLine(period=line.period, client_id=line.client_id, client_name=line.client_name, platform="google_ads",
                 account_id="acct", account_name=None, level="CAMPAÑA", campaign_id="c2", campaign_name="Two",
                 objective=None, spend=300, conversions=2, leads=None, conversion_value=0, clicks=3, impressions=30,
                 reach=None, currency="MXN", status="ok", reporting_group_id="conversion"),
    ]
    campaign_budgets = {
        (line.client_id, "google_ads", 2026, 10, None, "acct", "c1"): MonthlyBudget(
            client_id=line.client_id, platform="google_ads", year=2026, month=10,
            account_id="acct", campaign_id="c1", budget=100, currency="MXN"),
        (line.client_id, "google_ads", 2026, 10, None, "acct", "c2"): MonthlyBudget(
            client_id=line.client_id, platform="google_ads", year=2026, month=10,
            account_id="acct", campaign_id="c2", budget=200, currency="MXN"),
    }
    effective = _effective_budget(line, campaign_budgets, data)
    assert effective is not None and effective.budget == 300

    explicit_group = MonthlyBudget(client_id=line.client_id, platform="google_ads", year=2026, month=10,
                                   reporting_group_id="conversion", budget=250, currency="MXN")
    campaign_budgets[_budget_key(line.client_id, "google_ads", 2026, 10, "conversion")] = explicit_group
    effective = _effective_budget(line, campaign_budgets, data)
    assert effective is explicit_group


def test_group_cap_is_counted_once_in_multi_campaign_summary():
    first = _line("google_ads", 500, 3, None, None, 0)
    second = _line("google_ads", 300, 2, None, None, 0)
    first.reporting_group_id = second.reporting_group_id = "conversion"
    cap = MonthlyBudget(client_id=first.client_id, platform="google_ads", year=2026, month=10,
                       reporting_group_id="conversion", budget=250, currency="MXN")
    summary = _summary_record([first, second], {cap.key: cap}, level="group_total", period=first.period)
    assert summary["monthly_budget"] == 250
    assert summary["budget_period"] == 250 / 31 * 5


def test_observed_days_excludes_future_dates_and_future_only_periods():
    period = MonthSlice(date(2026, 10, 10), date(2026, 10, 20), 2026, 10, 31, 11)
    assert observed_days_in_period(period, date(2026, 10, 12)) == 3
    future = MonthSlice(date(2026, 10, 15), date(2026, 10, 20), 2026, 10, 31, 6)
    assert observed_days_in_period(future, date(2026, 10, 12)) == 0

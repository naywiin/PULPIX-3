from datetime import date

from app.reporting import MonthSlice, ReportLine, _summary_record


def _line(client_id: str, purchases: float | None, leads: float | None) -> ReportLine:
    return ReportLine(
        period=MonthSlice(date(2026, 10, 1), date(2026, 10, 5), 2026, 10, 31, 5),
        client_id=client_id, client_name=client_id, platform="meta_ads",
        account_ids=["123"], currency="MXN", status="ok", spend=1000,
        conversions=purchases or 0, purchases=purchases, leads=leads,
        conversion_value=1500, clicks=100, impressions=1000, reach=500,
        reach_is_sum=False,
    )


def test_pacing_keeps_purchase_and_lead_results_distinct():
    ecommerce = _summary_record([_line("jungala", 10, None)], {}, level="platform_total")
    assert ecommerce["purchases"] == 10
    assert ecommerce["cost_per_purchase"] == 100
    assert ecommerce["leads"] is None
    assert ecommerce["cpl"] is None

    lead_client = _summary_record([_line("blumia", None, 10)], {}, level="platform_total")
    assert lead_client["purchases"] is None
    assert lead_client["cost_per_purchase"] is None
    assert lead_client["leads"] == 10
    assert lead_client["cpl"] == 100

    zero = _summary_record([_line("jungala", 0, None)], {}, level="platform_total")
    assert zero["purchases"] == 0
    assert zero["cost_per_purchase"] is None

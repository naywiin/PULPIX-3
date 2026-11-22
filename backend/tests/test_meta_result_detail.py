"""Compras y leads reales mantienen su identidad al filtrar el detalle."""
import pytest

from app.performance_detail import build_detail_response


@pytest.mark.parametrize("level", ["campaign", "ad"])
def test_filtered_meta_purchase_totals(level):
    rows = [
        {"status": "ACTIVE", "spend": 600, "purchases": 3, "leads": None,
         "campaign_id": "1", "ad_id": "a", "currency": "MXN"},
        {"status": "PAUSED", "spend": 400, "purchases": 7, "leads": None,
         "campaign_id": "2", "ad_id": "b", "currency": "MXN"},
        {"status": "ACTIVE", "spend": 0, "purchases": 0, "leads": None,
         "campaign_id": "3", "ad_id": "c", "currency": "MXN"},
    ]
    def result(status="all", spend="all"):
        return build_detail_response(rows, client_id="jungala", platform="meta_ads", level=level,
                                     status=status, spend=spend, start_date="2026-10-01", end_date="2026-10-05")
    assert result()["totals"]["cost_per_purchase"] == 100
    assert result("active", "with_spend")["totals"]["cost_per_purchase"] == 200
    assert result("inactive", "with_spend")["totals"]["cost_per_purchase"] == pytest.approx(400 / 7)
    zero = result("active", "without_spend")
    assert zero["count"] == 1
    assert zero["totals"]["purchases"] == 0
    assert zero["totals"]["cost_per_purchase"] is None
    assert zero["rows"][0]["purchases"] == 0


@pytest.mark.parametrize("level", ["campaign", "ad"])
def test_filtered_meta_lead_totals(level):
    rows = [
        {"status": "ACTIVE", "spend": 600, "leads": 3, "purchases": None, "currency": "MXN"},
        {"status": "PAUSED", "spend": 400, "leads": 7, "purchases": None, "currency": "MXN"},
    ]
    data = build_detail_response(rows, client_id="blumia", platform="meta_ads", level=level,
                                 status="all", spend="all", start_date="2026-10-01", end_date="2026-10-05")
    assert data["totals"]["leads"] == 10
    assert data["totals"]["cpl"] == 100
    assert data["totals"]["purchases"] is None
    assert data["totals"]["cost_per_purchase"] is None

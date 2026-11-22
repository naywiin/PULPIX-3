from datetime import date
from io import BytesIO

import pytest
from openpyxl import load_workbook

from app.budgets import MemoryBudgetRepository, MonthlyBudget
from app.models import Client
from app.models import SPOTIFY_SANTUARIO_AD_ACCOUNT_ID, SPOTIFY_SANTUARIO_BUSINESS_ID
from app.report_order import CATEGORY_ORDER, client_report_category, client_report_sort_key
from app.reporting import (
    DataLine, MonthSlice, ReportLine, ReportValidationError, _build_profile_workbook, collect_report_lines,
    expected_accounts_for_clients,
    _profile_metric_values, _profile_result, _validate_workbook, _validated_woosync_sales,
)
from app.reporting_groups import CampaignGroupAssignment, ReportingGroup
from app.report_profiles import REPORT_PROFILES, block_for_group, report_profile


def line(client_id, platform, *, spend=1400, clicks=100, impressions=1000, purchases=None, leads=None, conversions=None, value=700, group=None):
    return ReportLine(
        period=MonthSlice(date(2026, 9, 1), date(2026, 9, 30), 2026, 9, 30, 30),
        client_id=client_id, client_name=client_id, platform=platform,
        account_ids=["account"], currency="MXN", status="ok", spend=spend,
        conversions=conversions, leads=leads, purchases=purchases,
        conversion_value=value, clicks=clicks, impressions=impressions, reach=500,
        reach_is_sum=False, reporting_group_name=group, grouping_enabled=group is not None,
    )


def test_profiles_cover_initial_historical_blocks_and_fallback_is_explicit():
    assert report_profile("cirque-du-soleil-joya").section == "ENTRETENIMIENTO"
    assert report_profile("jungala").primary_kpi == "cpa"
    assert [block.id for block in report_profile("blumia").blocks] == ["conversion", "traffic"]
    assert report_profile("doc-towers").section == "REAL ESTATE"
    assert report_profile("ssiento").section == "ECOMMERCE"
    assert report_profile("new-client") is None
    assert set(REPORT_PROFILES) == {
        "cirque-du-soleil-joya", "jungala", "blumia", "doc-towers", "bambino",
        "santuario-residencial", "dorado-center", "maternalia", "clarvi", "grupo-regio",
        "sign-factory", "tachuela", "ssiento", "iupi",
    }


def test_client_report_category_position_fallback_ties_and_fixed_order():
    clients = [
        Client(id="inactive", name="Hidden", slug="hidden", active=False, report_category="ENTRETENIMIENTO", report_position=1),
        Client(id="ent-two", name="Zulu", slug="zulu", report_category="ENTRETENIMIENTO", report_position=2),
        Client(id="ent-one", name="Beta", slug="beta", report_category="ENTRETENIMIENTO", report_position=1),
        Client(id="ent-tie", name="Alpha", slug="alpha", report_category="ENTRETENIMIENTO", report_position=2),
        Client(id="b2b", name="Corporate", slug="corporate", report_category="B2B / CORPORATIVO", report_position=1),
        Client(id="ecommerce", name="Shop", slug="shop", report_category="ECOMMERCE", report_position=1),
        Client(id="legacy", name="Legacy", slug="legacy"),
        Client(id="general-pos", name="General A", slug="general-a", report_category="GENERAL", report_position=1),
    ]
    active = sorted((client for client in clients if client.active), key=client_report_sort_key)
    assert CATEGORY_ORDER == {"ENTRETENIMIENTO": 0, "B2B / CORPORATIVO": 1, "ECOMMERCE": 2, "GENERAL": 3}
    assert [client.name for client in active] == ["Beta", "Alpha", "Zulu", "Corporate", "Shop", "General A", "Legacy"]
    assert client_report_category(clients[6]) == "GENERAL"
    workbook = _build_profile_workbook([], [], [], clients, date(2026, 10, 1), date(2026, 10, 5))
    sheet = workbook["Octubre 2026"]
    titles = {sheet.cell(row, 1).value: row for row in range(1, sheet.max_row + 1)}
    assert titles["Beta"] < titles["Alpha"] < titles["Zulu"] < titles["Corporate"] < titles["Shop"] < titles["General A"] < titles["Legacy"]
    assert "Hidden" not in titles


def _xlsx_with_campaign_row(client_id: str, platform_label: str, account_id: str) -> bytes:
    from openpyxl import Workbook

    wb = Workbook()
    wb.active.title = "Octubre 2026"
    for name in ("PRESUPUESTOS", "DATOS", "CONFIGURACION", "AUDITORIA"):
        wb.create_sheet(name)
    data = wb["DATOS"]
    row = [None] * 9
    row[3], row[5], row[6], row[8] = client_id, platform_label, account_id, "CAMPAÑA"
    data.append([None] * 9)
    data.append(row)
    output = BytesIO()
    wb.save(output)
    return output.getvalue()


def test_expected_accounts_uses_each_persisted_client_source_and_rejects_foreign_pinterest():
    ssiento = Client(id="ssiento", name="Ssiento", slug="ssiento", pinterest_ads_sources=[{
        "source_id": "549763101458", "ad_account_id": "549763101458", "display_name": "SSIENTO COMERCIAL",
    }], google_ads_sources=[{"customer_id": "2183992705"}],
       meta_ads_sources=[{"ad_account_id": "1465882777215540"}])
    modern_mexico = Client(id="modern-mexico", name="Modern Mexico", slug="modern-mexico", pinterest_ads_sources=[{
        "source_id": "998877665544", "ad_account_id": "998877665544", "display_name": "Modern Mexico",
    }])
    santuario = Client(id="santuario-residencial", name="Santuario", slug="santuario-residencial",
       spotify_ads_sources=[{"business_id": SPOTIFY_SANTUARIO_BUSINESS_ID,
                             "ad_account_id": SPOTIFY_SANTUARIO_AD_ACCOUNT_ID}])
    accounts = expected_accounts_for_clients([ssiento, modern_mexico, santuario])

    assert accounts[("ssiento", "pinterest_ads")] == {"549763101458"}
    assert accounts[("modern-mexico", "pinterest_ads")] == {"998877665544"}
    assert accounts[("ssiento", "google_ads")] == {"2183992705"}
    assert accounts[("ssiento", "meta_ads")] == {"1465882777215540"}
    assert accounts[("santuario-residencial", "spotify_ads")] == {SPOTIFY_SANTUARIO_AD_ACCOUNT_ID}

    _validate_workbook(_xlsx_with_campaign_row("ssiento", "Pinterest Ads", "549763101458"), accounts)
    with pytest.raises(ReportValidationError, match="cuenta ajena"):
        _validate_workbook(_xlsx_with_campaign_row("ssiento", "Pinterest Ads", "998877665544"), accounts)
    without_pinterest = Client(id="empty", name="Empty", slug="empty")
    empty_accounts = expected_accounts_for_clients([without_pinterest])
    assert empty_accounts[("empty", "pinterest_ads")] == set()
    with pytest.raises(ReportValidationError, match="cuenta ajena"):
        _validate_workbook(_xlsx_with_campaign_row("empty", "Pinterest Ads", "549763101458"), empty_accounts)


def test_expected_accounts_keeps_google_meta_spotify_and_inactive_exclusion_independent():
    from app.reporting import _build_profile_workbook

    active = Client(
        id="active", name="Active", slug="active", active=True,
        google_ads_sources=[{"customer_id": "1234567890"}],
        meta_ads_sources=[{"ad_account_id": "123456789012345"}],
    )
    inactive = Client(id="inactive", name="Inactive", slug="inactive", active=False,
                      pinterest_ads_sources=[{"source_id": "123456789012", "ad_account_id": "123456789012"}])
    santuario = Client(id="santuario-residencial", name="Santuario", slug="santuario-residencial",
                       spotify_ads_sources=[{"business_id": SPOTIFY_SANTUARIO_BUSINESS_ID,
                                             "ad_account_id": SPOTIFY_SANTUARIO_AD_ACCOUNT_ID}])
    accounts = expected_accounts_for_clients([active, inactive, santuario])
    assert accounts[("active", "google_ads")] == {"1234567890"}
    assert accounts[("active", "meta_ads")] == {"123456789012345"}
    assert accounts[("santuario-residencial", "spotify_ads")] == {SPOTIFY_SANTUARIO_AD_ACCOUNT_ID}
    # La validación conserva fuentes del cliente inactivo, pero el renderer decide su exclusión.
    assert accounts[("inactive", "pinterest_ads")] == {"123456789012"}
    report, data = [], []
    wb = _build_profile_workbook(report, data, [], [active, inactive], date(2026, 10, 1), date(2026, 10, 1))
    text = [cell.value for sheet in wb.worksheets for row in sheet.iter_rows() for cell in row]
    assert "Inactive" not in text


def test_pinterest_xlsx_validation_allows_assigned_account_with_null_checkout_metrics():
    client = Client(id="ssiento", name="Ssiento", slug="ssiento", pinterest_ads_sources=[{
        "source_id": "549763101458", "ad_account_id": "549763101458",
        "display_name": "SSIENTO COMERCIAL", "currency": "MXN",
    }])
    period = MonthSlice(date(2026, 10, 1), date(2026, 10, 14), 2026, 10, 31, 14)
    data = [DataLine(
        period=period, client_id="ssiento", client_name="Ssiento", platform="pinterest_ads",
        account_id="549763101458", account_name="SSIENTO COMERCIAL", level="CAMPAÑA",
        campaign_id="campaign-1", campaign_name="Checkout", objective="WEB_CONVERSION",
        spend=3170.26, conversions=None, leads=None, conversion_value=None, clicks=3284,
        impressions=614703, reach=None, currency="MXN", status="ACTIVE", checkouts=None,
    )]
    workbook = _build_profile_workbook([], data, [], [client], period.start, period.end)
    output = BytesIO()
    workbook.save(output)
    content = output.getvalue()
    _validate_workbook(content, expected_accounts_for_clients([client]))
    reopened = load_workbook(BytesIO(content), data_only=False)
    values = [cell.value for row in reopened["DATOS"].iter_rows() for cell in row]
    assert "549763101458" in values
    assert "Pinterest Ads" in values
    assert 3170.26 in values
    assert 614703 in values
    assert 3284 in values


def test_historical_profiles_use_canonical_ids_and_preserve_distinct_clarvi_blocks():
    assert report_profile("bambino").section == "REAL ESTATE"
    assert report_profile("santuario-residencial").display_name == "Santuario"
    assert report_profile("dorado-center").primary_kpi == "cpl"
    assert report_profile("maternalia").blocks[0].historical_rows == (146, 152)
    clarvi = report_profile("clarvi")
    assert [block.id for block in clarvi.blocks] == ["leads", "traffic", "ecommerce"]
    assert block_for_group(clarvi, "Clarvi - Leads").id == "leads"
    assert block_for_group(clarvi, "Clarvi Tráfico").id == "traffic"
    assert block_for_group(clarvi, "Ecommerce").id == "ecommerce"
    assert set(clarvi.blocks[1].platforms) == {"google_ads", "meta_ads", "spotify_ads"}
    assert block_for_group(report_profile("blumia"), "Trafico").id == "traffic"
    assert "cpm" in clarvi.blocks[2].metrics
    assert report_profile("modern-mexico") is None
    assert report_profile("iupi").display_name == "Torre Aurelio"
    assert report_profile("iupi").section == "REAL ESTATE"
    assert report_profile("hodela") is None


@pytest.mark.parametrize("client_id", ["cirque-du-soleil-joya", "jungala"])
def test_purchase_mode_does_not_add_equivalent_metrics(client_id):
    meta = line(client_id, "meta_ads", purchases=7, conversions=7)
    profile = report_profile(client_id)
    assert _profile_result(profile, meta) == (7, "Compras")
    values = _profile_metric_values(profile, profile.blocks[0], [meta], {}, [meta])
    assert values["result"] == 7
    assert values["cost_per_result"] == 200
    assert values["conversion_value"] == 700
    assert values["roas"] == 0.5
    assert values["ticket_average"] == 100


@pytest.mark.parametrize("client_id", ["ssiento", "blumia"])
def test_ssiento_and_blumia_profiles_use_purchase_even_when_leads_exist(client_id):
    meta = line(client_id, "meta_ads", purchases=7, conversions=99, leads=5, spend=1000)
    profile = report_profile(client_id)
    assert _profile_result(profile, meta) == (7, "Compras")
    block = profile.blocks[0] if client_id == "ssiento" else profile.blocks[0]
    values = _profile_metric_values(profile, block, [meta], {}, [meta])
    assert values["result"] == 7
    assert values["cost_per_result"] == 1000 / 7
    assert values["conversion_value"] == 700
    assert values["roas"] == 0.7
    if client_id == "blumia":
        assert block_for_group(profile, "Conversión").id == "conversion"
        assert block_for_group(profile, "Tráfico").id == "traffic"
        assert block_for_group(profile, None) is None


def test_ssiento_combines_google_conversion_value_with_meta_purchase_value():
    profile = report_profile("ssiento")
    google = line("ssiento", "google_ads", spend=1000, conversions=10, value=100000)
    meta = line("ssiento", "meta_ads", spend=2000, leads=20, purchases=28, value=30000)
    values = _profile_metric_values(profile, profile.blocks[0], [google, meta], {}, [google, meta])
    assert values["result"] == 38
    assert values["spend"] == 3000
    assert values["conversion_value"] == 130000
    assert values["roas"] == 130000 / 3000


def test_google_result_remains_conversions_and_ratios_keep_null_vs_zero():
    profile = report_profile("cirque-du-soleil-joya")
    google = line("cirque-du-soleil-joya", "google_ads", purchases=None, conversions=7)
    assert _profile_result(profile, google) == (7, "Conversiones")
    zero = line("jungala", "meta_ads", purchases=0, spend=1400, value=0)
    values = _profile_metric_values(report_profile("jungala"), profile.blocks[0], [zero], {}, [zero])
    assert values["result"] == 0
    assert values["cost_per_result"] is None
    missing = line("jungala", "meta_ads", purchases=None, spend=1400, value=None)
    values = _profile_metric_values(report_profile("jungala"), profile.blocks[0], [missing], {}, [missing])
    assert values["result"] is None
    assert values["cost_per_result"] is None
    assert values["conversion_value"] is None


def test_purchase_profile_does_not_show_meta_value_or_roas_without_selected_purchase_action():
    profile = report_profile("cirque-du-soleil-joya")
    meta = line("cirque-du-soleil-joya", "meta_ads", purchases=None, conversions=0, spend=1000, value=0)
    values = _profile_metric_values(profile, profile.blocks[0], [meta], {}, [meta])
    assert values["result"] is None
    assert values["cost_per_result"] is None
    assert values["conversion_value"] is None
    assert values["roas"] is None


def test_real_estate_result_is_leads_for_meta_and_conversions_for_google():
    profile = report_profile("doc-towers")
    assert _profile_result(profile, line("doc-towers", "meta_ads", leads=4, purchases=100)) == (4, "Clientes potenciales")
    assert _profile_result(profile, line("doc-towers", "google_ads", conversions=3)) == (3, "Conversiones")
    assert profile.blocks[0].requires_value_metrics is False


def test_clarvi_keeps_lead_mode_when_four_special_clients_use_purchase():
    profile = report_profile("clarvi")
    assert profile.meta_result_mode == "lead"
    assert _profile_result(profile, line("clarvi", "meta_ads", leads=5, purchases=99)) == (5, "Clientes potenciales")


def test_clarvi_cpm_uses_normalized_dashboard_definition():
    profile = report_profile("clarvi")
    row = line("clarvi", "google_ads", spend=1000, conversions=2, value=None)
    values = _profile_metric_values(profile, profile.blocks[2], [row], {}, [row])
    assert values["cpm"] == 1000


def test_google_clicks_and_cpc_remain_available_when_conversions_are_zero():
    profile = report_profile("santuario-residencial")
    google = line("santuario-residencial", "google_ads", spend=600, clicks=120, conversions=0, value=0)
    values = _profile_metric_values(profile, profile.blocks[0], [google], {}, [google])
    assert values["clicks"] == 120
    assert values["cpc"] == 5
    assert values["cost_per_result"] is None


def test_clarvi_group_aggregates_observed_meta_leads_without_fabricating_missing_campaign_zeroes():
    client = Client(
        id="clarvi", name="Clarvi", slug="clarvi", currency="MXN",
        google_ads_sources=[{"customer_id": "1001"}],
        meta_ads_sources=[{"ad_account_id": "2002"}],
    )

    class Service:
        def platform_block(self, _client, platform, _rng, _cmp, _refresh, _owners):
            if platform == "meta_ads":
                campaigns = [
                    {"campaign_id": "m1", "source_account_id": "2002", "spend": 100, "clicks": 10, "leads": 53},
                    {"campaign_id": "m2", "source_account_id": "2002", "spend": 50, "clicks": 5, "leads": None},
                ]
                totals = {"spend": 150, "clicks": 15, "leads": 53, "conversions": 0}
            elif platform == "google_ads":
                campaigns, totals = [], {"spend": 0, "clicks": 0, "conversions": 0}
            else:
                return {"status": "not_linked", "current": None}
            return {"status": "ok", "currency": "MXN", "current": {"totals": totals, "campaigns": campaigns}}

    group = ReportingGroup(
        client_id="clarvi", id="leads", name="LEADS", type="leads", active=True,
        assignments=[
            CampaignGroupAssignment(platform="meta_ads", account_id="2002", campaign_id="m1"),
            CampaignGroupAssignment(platform="meta_ads", account_id="2002", campaign_id="m2"),
        ],
    )
    lines, data = collect_report_lines(
        Service(), [client], date(2026, 10, 1), date(2026, 10, 5), owners={},
        reporting_groups_by_client_month={("clarvi", 2026, 10): [group]},
    )
    meta_line = next(line for line in lines if line.platform == "meta_ads" and line.reporting_group_name == "LEADS")
    assert meta_line.leads == 53
    profile = report_profile("clarvi")
    values = _profile_metric_values(profile, profile.blocks[0], [meta_line], {}, lines, data)
    assert values["result"] == 53
    assert values["cost_per_result"] == pytest.approx(150 / 53)


def test_purchase_profile_keeps_dashboard_platform_totals_when_campaign_actions_are_sparse():
    client = Client(
        id="cirque-du-soleil-joya", name="JOYÀ", slug="joya", currency="MXN",
        google_ads_sources=[{"customer_id": "1001"}],
        meta_ads_sources=[{"ad_account_id": "2002"}],
    )

    class Service:
        def platform_block(self, _client, platform, _rng, _cmp, _refresh, _owners):
            if platform == "google_ads":
                campaign = {"campaign_id": "g1", "source_account_id": "1001", "spend": 100, "clicks": 20, "conversions": 4, "conversion_value": 200}
                totals = {"spend": 100, "clicks": 20, "conversions": 4, "conversion_value": 200}
            elif platform == "meta_ads":
                campaign = {"campaign_id": "m1", "source_account_id": "2002", "spend": 50, "clicks": 10, "conversions": 7, "purchases": 7, "leads": None, "conversion_value": 700}
                sparse_campaign = {"campaign_id": "m2", "source_account_id": "2002", "spend": 25, "clicks": 5, "conversions": 0, "purchases": None, "leads": None, "conversion_value": None}
                totals = {"spend": 75, "clicks": 15, "conversions": 7, "purchases": 7, "conversion_value": 700}
                campaigns = [campaign, sparse_campaign]
                return {"status": "ok", "currency": "MXN", "current": {"totals": totals, "campaigns": campaigns}}
            else:
                return {"status": "not_configured", "current": None}
            return {"status": "ok", "currency": "MXN", "current": {"totals": totals, "campaigns": [campaign]}}

    group = ReportingGroup(
        client_id=client.id, id="traffic", name="Tráfico", type="traffic", active=True,
        enabled_platforms={"google_ads": True, "meta_ads": True, "spotify_ads": False},
        assignments=[CampaignGroupAssignment(platform="meta_ads", account_id="2002", campaign_id="m1")],
    )
    report_lines, _ = collect_report_lines(
        Service(), [client], date(2026, 10, 1), date(2026, 10, 5), owners={},
        reporting_groups_by_client_month={(client.id, 2026, 10): [group]},
    )
    meta = next(row for row in report_lines if row.platform == "meta_ads")
    assert meta.purchases == 7
    assert meta.conversions == 7
    assert meta.conversion_value == 700
    assert meta.spend == 75
    workbook = _build_profile_workbook(
        report_lines, [], [], [client], date(2026, 10, 1), date(2026, 10, 5),
        reporting_groups_by_client_month={(client.id, 2026, 10): [group]},
    )
    sheet = workbook["Octubre 2026"]
    platform_rows = {sheet.cell(row, 2).value: row for row in range(1, sheet.max_row + 1)
                     if sheet.cell(row, 2).value in {"Google Ads", "Meta Ads"}}
    assert sheet.cell(platform_rows["Google Ads"], 5).value == 4
    assert sheet.cell(platform_rows["Meta Ads"], 5).value == 7


@pytest.mark.parametrize(("google_on", "meta_on", "expected"), [
    (True, False, {"google_ads"}),
    (False, True, {"meta_ads"}),
    (True, True, {"google_ads", "meta_ads"}),
    (False, False, set()),
])
def test_traffic_group_renders_per_enabled_platform_even_without_conversion_metrics(google_on, meta_on, expected):
    client = Client(
        id="blumia", name="Blumia", slug="blumia", currency="MXN",
        google_ads_sources=[{"customer_id": "1001"}], meta_ads_sources=[{"ad_account_id": "2002"}],
    )

    class Service:
        def platform_block(self, _client, platform, _rng, _cmp, _refresh, _owners):
            campaign = ({"campaign_id": "g1", "source_account_id": "1001", "campaign_name": "Google tráfico",
                         "spend": 500, "clicks": 100, "impressions": 2000, "conversions": None}
                        if platform == "google_ads" else
                        {"campaign_id": "m1", "source_account_id": "2002", "campaign_name": "Meta tráfico",
                         "spend": 300, "clicks": 150, "impressions": 3000, "conversions": None,
                         "purchases": None, "leads": None})
            totals = {"spend": campaign["spend"], "clicks": campaign["clicks"], "impressions": campaign["impressions"], "conversions": None}
            return {"status": "ok", "currency": "MXN", "current": {"totals": totals, "campaigns": [campaign]}}

    group = ReportingGroup(
        client_id="blumia", id="trafico", name="Trafico", type="traffic", active=True,
        enabled_platforms={"google_ads": google_on, "meta_ads": meta_on, "spotify_ads": False},
        assignments=[
            CampaignGroupAssignment(platform="google_ads", account_id="1001", campaign_id="g1"),
            CampaignGroupAssignment(platform="meta_ads", account_id="2002", campaign_id="m1"),
        ],
    )
    lines, data = collect_report_lines(
        Service(), [client], date(2026, 10, 1), date(2026, 10, 5), owners={},
        reporting_groups_by_client_month={("blumia", 2026, 10): [group]},
    )
    traffic = [row for row in lines if block_for_group(report_profile("blumia"), row.reporting_group_name) == report_profile("blumia").blocks[1]]
    assert {row.platform for row in traffic} == expected
    assert all(row.spend is not None and row.clicks is not None for row in traffic)
    assert all(row.conversions is None for row in traffic)
    assert len(data) >= 4  # Platform totals and campaign assignments remain intact.
    for row in traffic:
        metric_values = _profile_metric_values(report_profile("blumia"), report_profile("blumia").blocks[1], [row], {}, data)
        assert metric_values["cpc"] is not None


def test_monthly_workbook_uses_client_layouts_and_keeps_blumia_blocks_separate():
    clients = [
        Client(id=client_id, name=name, slug=client_id)
        for client_id, name in (
            ("cirque-du-soleil-joya", "Cirque du Soleil JOYÀ"),
            ("jungala", "Jungala"), ("blumia", "Blumia"),
            ("doc-towers", "Doc Towers"), ("ssiento", "Ssiento"), ("new-client", "Cliente sin perfil"),
        )
    ]
    lines = [
        line("cirque-du-soleil-joya", "meta_ads", purchases=7, conversions=7, group=None),
        line("cirque-du-soleil-joya", "google_ads", conversions=3),
        line("jungala", "meta_ads", purchases=3, conversions=3),
        line("blumia", "meta_ads", leads=5, purchases=99, group="Conversión"),
        line("blumia", "meta_ads", leads=0, purchases=99, group="Tráfico"),
        line("doc-towers", "google_ads", conversions=4),
        line("doc-towers", "meta_ads", leads=3, purchases=20),
        line("ssiento", "meta_ads", leads=2, purchases=30),
    ]
    for row in lines:
        row.grouping_enabled = row.client_id == "blumia"
    workbook = _build_profile_workbook(lines, [], [], clients, date(2026, 9, 1), date(2026, 9, 30), {
        "Septiembre 2026": {"sales_overview": {"orders_count": 2, "total_sales": 1500}, "applied_filters": {"branches": ["monterrey"]}}
    })
    reopened = load_workbook(BytesIO(_workbook_bytes(workbook)), data_only=False)
    assert reopened.sheetnames[0] == "Septiembre 2026"
    sheet = reopened["Septiembre 2026"]
    texts = [str(cell.value) for row in sheet.iter_rows() for cell in row if cell.value is not None]
    assert "BLUMIA - CONVERSIÓN" in texts
    assert "BLUMIA - TRÁFICO" in texts
    assert any(isinstance(text, str) and "Meta: Compras" in text for text in texts)
    assert "Clientes potenciales (Meta) / Conversiones (Google)" in texts
    assert "SSIENTO - PERFORMANCE" in texts
    assert "CLIENTES GENERAL" in texts
    assert "REPORT_PROFILE_NOT_CONFIGURED" in [cell.value for row in reopened["CONFIGURACION"].iter_rows() for cell in row]
    assert sheet.max_column <= 15
    assert sheet.max_column >= 13
    assert sheet["A4"].fill.fgColor.rgb in {"FFC9DAF8", "00C9DAF8"}


def test_blumia_without_reporting_groups_is_not_attributed_to_conversion_or_traffic():
    client = Client(id="blumia", name="Blumia", slug="blumia")
    meta = line("blumia", "meta_ads", spend=500, leads=5, group=None)
    workbook = _build_profile_workbook([meta], [], [], [client], date(2026, 9, 1), date(2026, 9, 30))
    sheet = workbook["Septiembre 2026"]
    texts = [str(cell.value) for row in sheet.iter_rows() for cell in row if cell.value is not None]
    assert "BLUMIA - CONVERSIÓN" in texts
    assert "BLUMIA - TRÁFICO" in texts
    assert texts.count("Configuración de campañas pendiente") == 2
    assert not any(cell.value == 500 for row in sheet.iter_rows() for cell in row)
    assert any(row[5] == "BLUMIA_REPORTING_GROUP_DATA_MISSING" for row in workbook["AUDITORIA"].iter_rows(min_row=2, values_only=True))


def test_every_configured_profile_renders_full_monthly_block_without_data():
    clients = [
        Client(id=client_id, name=name, slug=client_id, active=active,
               google_ads_customer_id="1234567890" if google else None,
               meta_ad_account_id="123456789012345" if meta else None)
        for client_id, name, active, google, meta in (
            ("cirque-du-soleil-joya", "JOYÀ", True, True, True),
            ("jungala", "Jungala", True, True, True),
            ("blumia", "Blumia", True, True, True),
            ("doc-towers", "Doc Towers", True, True, True),
            ("bambino", "Bambino", False, True, False),
            ("santuario-residencial", "Santuario", True, True, False),
            ("dorado-center", "Dorado Center", True, True, True),
            ("maternalia", "Maternalia", True, False, True),
            ("clarvi", "Clarvi", True, True, True),
            ("grupo-regio", "Grupo Regio", True, True, True),
            ("sign-factory", "Sign Factory", True, True, True),
            ("tachuela", "Tachuela", True, True, True),
            ("ssiento", "Ssiento", True, True, True),
            ("iupi", "Torre Aurelio", True, True, True),
            ("modern-mexico", "Modern Mexico", False, True, True),
        )
    ]
    wb = _build_profile_workbook([], [], [], clients, date(2026, 10, 1), date(2026, 10, 5))
    sheet = wb["Octubre 2026"]
    texts = [cell.value for row in sheet.iter_rows() for cell in row if cell.value is not None]
    active_client_ids = {client.id for client in clients if client.active}
    for profile in (profile for client_id, profile in REPORT_PROFILES.items() if client_id in active_client_ids):
        for block in profile.blocks:
            assert block.display_name.upper() in texts
    assert "BAMBINO" not in texts
    assert "MODERN MEXICO" not in texts
    assert "REPORT_PROFILE_NOT_CONFIGURED" not in [
        row[-2] for row in wb["CONFIGURACION"].iter_rows(min_row=2, values_only=True)
    ]
    assert "Grupo sin configurar" in texts
    assert any(row[5] == "REPORTING_GROUP_NOT_CONFIGURED"
               for row in wb["AUDITORIA"].iter_rows(min_row=2, values_only=True))
    assert "Google Ads" in texts and "Meta Ads" in texts
    assert "Presupuesto no capturado" in texts
    assert sheet.max_row > 100


def test_inactive_clients_are_excluded_from_new_report_sheets_without_touching_storage():
    clients = [
        Client(id="doc-towers", name="Activo A", slug="activo-a", active=True),
        Client(id="bambino", name="Inactivo B", slug="inactivo-b", active=False),
        Client(id="jungala", name="Activo C", slug="activo-c", active=True),
    ]
    active_lines = [line("doc-towers", "google_ads"), line("jungala", "meta_ads", purchases=3)]
    inactive_budget = MonthlyBudget(
        client_id="bambino", platform="google_ads", year=2026, month=9,
        budget=12345, currency="MXN",
    )
    wb = _build_profile_workbook(
        active_lines + [line("bambino", "google_ads")], [], [inactive_budget], clients,
        date(2026, 9, 1), date(2026, 9, 30),
    )
    for sheet_name in ("Septiembre 2026", "PRESUPUESTOS", "CONFIGURACION"):
        values = [cell.value for row in wb[sheet_name].iter_rows() for cell in row if cell.value is not None]
        assert "Inactivo B" not in values
        assert "bambino" not in values
    monthly_values = [cell.value for row in wb["Septiembre 2026"].iter_rows() for cell in row if cell.value is not None]
    assert "DOC TOWERS" in monthly_values
    assert "JUNGALA" in monthly_values
    # Filtering the generated workbook does not mutate the caller's source data.
    assert inactive_budget.budget == 12345


def test_empty_configured_profile_statuses_are_distinct_and_technical_codes_are_off_monthly_sheet():
    clients = [
        Client(id="blumia", name="Blumia", slug="blumia"),
        Client(id="clarvi", name="Clarvi", slug="clarvi"),
        Client(id="doc-towers", name="Doc Towers", slug="doc-towers"),
    ]
    wb = _build_profile_workbook([], [], [], clients, date(2026, 10, 1), date(2026, 10, 5))
    month_values = [cell.value for row in wb["Octubre 2026"].iter_rows() for cell in row if cell.value is not None]
    assert "BLUMIA_REPORTING_GROUP_DATA_MISSING" not in month_values
    statuses = [row[4] for row in wb["Octubre 2026"].iter_rows(min_row=1, values_only=True) if row[0] == "Doc Towers"]
    assert "SIN_DATOS_PERIODO" in statuses
    config_values = [cell.value for row in wb["CONFIGURACION"].iter_rows() for cell in row if cell.value is not None]
    assert "BLUMIA_REPORTING_GROUP_DATA_MISSING" in config_values
    audit_values = [cell.value for row in wb["AUDITORIA"].iter_rows() for cell in row if cell.value is not None]
    assert "BLUMIA_REPORTING_GROUP_DATA_MISSING" in audit_values


def test_client_summary_distinguishes_missing_semantic_metric_from_zero_results():
    clients = [
        Client(id="cirque-du-soleil-joya", name="JOYÀ", slug="joya"),
        Client(id="doc-towers", name="Doc Towers", slug="doc-towers"),
    ]
    lines = [
        line("cirque-du-soleil-joya", "google_ads", spend=100, conversions=2, value=0),
        line("cirque-du-soleil-joya", "meta_ads", spend=50, purchases=None, value=None),
        line("doc-towers", "google_ads", spend=0, conversions=0, value=0),
    ]
    workbook = _build_profile_workbook(lines, [], [], clients, date(2026, 9, 1), date(2026, 9, 30))
    sheet = workbook["Septiembre 2026"]
    summary = next(row for row in range(1, sheet.max_row + 1) if sheet.cell(row, 1).value == "CLIENTES GENERAL")
    statuses = {sheet.cell(row, 1).value: sheet.cell(row, 5).value for row in range(summary + 2, sheet.max_row + 1) if sheet.cell(row, 1).value}
    assert statuses["JOYÀ"] == "FUENTE_NO_DISPONIBLE"
    assert statuses["Doc Towers"] == "SIN_DATOS_PERIODO"


def _woosync_period():
    start, end = "2026-10-01", "2026-10-05"
    branches = [
        ("monterrey", "Monterrey", 20, 49366.60),
        ("saltillo", "Saltillo", 5, 10810.00),
        ("leon", "León", 5, 7340.00),
        ("chihuahua", "Chihuahua", 1, 935.00),
        ("cdmx", "CDMX", 9, 15710.00),
    ]
    return {
        "period": {"from": start, "to": end},
        "sales_overview": {"orders_count": 40, "total_sales": 84161.60},
        "applied_filters": {"from": start, "to": end, "branches": [b[0] for b in branches]},
        "branch_sales": [
            {"branch_id": branch_id, "branch_name": name, "orders_count": orders, "total_sales": sales,
             "applied_filters": {"from": start, "to": end, "branches": [branch_id]}}
            for branch_id, name, orders, sales in branches
        ],
        "channel_attribution_available": False,
    }


def test_woosync_branch_sales_reconcile_and_ticket_average_is_safe():
    data = _woosync_period()
    result = _validated_woosync_sales(data)
    assert sum(branch["orders_count"] for branch in result["branch_sales"]) == 40
    assert sum(branch["total_sales"] for branch in result["branch_sales"]) == pytest.approx(84161.60)
    assert result["orders_count"] == 40
    assert result["total_sales"] == pytest.approx(84161.60)
    assert result["ticket_average"] == pytest.approx(2104.04)
    assert result["channel_attribution_available"] is False
    zero = _validated_woosync_sales({"sales_overview": {"orders_count": 0, "total_sales": 0}})
    assert zero["ticket_average"] is None


def test_woosync_does_not_accept_inconsistent_branch_totals_or_periods():
    bad_total = _woosync_period()
    bad_total["sales_overview"]["total_sales"] += 1
    with pytest.raises(ReportValidationError, match="ventas por sede"):
        _validated_woosync_sales(bad_total)
    bad_period = _woosync_period()
    bad_period["branch_sales"][0]["applied_filters"]["from"] = "2026-09-30"
    with pytest.raises(ReportValidationError, match="periodo"):
        _validated_woosync_sales(bad_period)


def test_woosync_xlsx_has_real_sedes_totals_audit_and_no_channel_attribution():
    from io import BytesIO

    clients = [
        Client(id="ssiento", name="Ssiento", slug="ssiento"),
        Client(id="blumia", name="Blumia", slug="blumia"),
    ]
    workbook = _build_profile_workbook(
        [], [], [], clients, date(2026, 10, 1), date(2026, 10, 5),
        {"Octubre 2026": _woosync_period()},
    )
    content = _workbook_bytes(workbook)
    _validate_workbook(content, {})
    reopened = load_workbook(BytesIO(content), data_only=False)
    sheet = reopened["Octubre 2026"]
    title_row = next(row for row in range(1, sheet.max_row + 1) if sheet.cell(row, 1).value == "SSIENTO - VENTAS WOOSYNC")
    performance_title = next(row for row in range(1, title_row) if sheet.cell(row, 1).value == "SSIENTO - PERFORMANCE")
    assert title_row - performance_title >= 5
    first_branch = title_row + 2
    assert [sheet.cell(first_branch + offset, 1).value for offset in range(5)] == [
        "Monterrey", "Saltillo", "León", "Chihuahua", "CDMX",
    ]
    total_row = first_branch + 5
    assert sheet.cell(total_row, 1).value == "TOTAL"
    assert sheet.cell(total_row, 2).value == 40
    assert sheet.cell(total_row, 3).value == pytest.approx(84161.60)
    assert sheet.cell(total_row + 1, 1).value == "Ticket promedio"
    assert sheet.cell(total_row + 1, 3).value == pytest.approx(2104.04)
    assert sheet.cell(first_branch, 2).number_format == "#,##0"
    assert sheet.cell(first_branch, 3).number_format == "$#,##0.00"
    assert sheet.cell(first_branch, 3).fill.fgColor.rgb == "FFC9DAF8"
    note_row = total_row + 3
    assert "Sin atribución por canal disponible" in str(sheet.cell(note_row, 1).value)
    assert sheet.max_column == 15
    assert sheet.column_dimensions["A"].width >= 18
    assert sheet.column_dimensions["C"].width >= 20
    assert sheet.row_dimensions[title_row + 1].height >= 35
    assert all(sheet.cell(title_row + 1, col).alignment.wrap_text for col in range(1, 4))

    audit = reopened["AUDITORIA"]
    audit_rows = list(audit.iter_rows(min_row=2, values_only=True))
    woo_rows = [row for row in audit_rows if row[0] == "Ssiento" and row[4].startswith("WooSync")]
    assert len(woo_rows) == 13
    assert all(row[6] == "2026-10-01 → 2026-10-05" for row in woo_rows)
    assert any(row[3] == "orders_count" and row[5] == 40 and row[7] == "TOTAL" for row in woo_rows)
    assert any(row[3] == "total_sales" and row[5] == pytest.approx(84161.60) and row[7] == "TOTAL" for row in woo_rows)

    config_rows = list(reopened["CONFIGURACION"].iter_rows(min_row=2, values_only=True))
    assert sum(row[17] == "BLUMIA_REPORTING_GROUP_DATA_MISSING" for row in config_rows if row[1] == "blumia") == 4
    assert sum(row[5] == "BLUMIA_REPORTING_GROUP_DATA_MISSING" for row in audit_rows if row[0] == "Blumia") == 2
    monthly_texts = [str(cell.value) for row in sheet.iter_rows() for cell in row if cell.value is not None]
    assert monthly_texts.count("Configuración de campañas pendiente") == 2
    assert not any(text in {"Meta Ads", "Google Ads", "SEO", "SEM", "Pinterest"} for text in monthly_texts)

    invalid = {"#REF!", "#DIV/0!", "#VALUE!", "#N/A", "NaN", "Infinity"}
    assert not [cell.value for ws in reopened.worksheets for row in ws.iter_rows() for cell in row
                if isinstance(cell.value, str) and any(token in cell.value for token in invalid)]


def test_purchase_profile_xlsx_separates_platform_rows_and_calculates_semantic_totals():
    clients = [Client(id="cirque-du-soleil-joya", name="JOYÀ", slug="joya")]
    meta = line("cirque-du-soleil-joya", "meta_ads", spend=1400, purchases=7, conversions=7, value=700)
    google = line("cirque-du-soleil-joya", "google_ads", spend=500, purchases=None, conversions=10, value=250)
    wb = _build_profile_workbook([meta, google], [], [], clients, date(2026, 9, 1), date(2026, 9, 30))
    sheet = wb["Septiembre 2026"]
    title = next(row for row in range(1, sheet.max_row + 1) if sheet.cell(row, 1).value == "JOYÀ")
    header_row = title + 1
    headers = {sheet.cell(header_row, col).value: col for col in range(1, sheet.max_column + 1)}
    assert "Resultados\nMeta: Compras · Google: Conversiones" in headers
    assert "CPA (Google) / Costo por resultado (Meta)" in headers
    result_col = headers["Resultados\nMeta: Compras · Google: Conversiones"]
    cost_col = headers["CPA (Google) / Costo por resultado (Meta)"]
    value_col = headers["Valor de conversión"]
    roas_col = headers["ROAS"]
    data_rows = [row for row in range(header_row + 1, sheet.max_row + 1) if sheet.cell(row, 2).value in {"Meta Ads", "Google Ads"}]
    meta_row = next(row for row in data_rows if sheet.cell(row, 2).value == "Meta Ads")
    google_row = next(row for row in data_rows if sheet.cell(row, 2).value == "Google Ads")
    assert sheet.cell(meta_row, result_col).value == 7
    assert sheet.cell(meta_row, cost_col).value == pytest.approx(200)
    assert sheet.cell(meta_row, value_col).value == 700
    assert sheet.cell(meta_row, roas_col).value == pytest.approx(0.5)
    assert sheet.cell(google_row, result_col).value == 10
    assert sheet.cell(google_row, cost_col).value == pytest.approx(50)
    assert sheet.cell(google_row, value_col).value == 250
    assert sheet.cell(google_row, roas_col).value == pytest.approx(0.5)
    total_row = next(row for row in range(google_row + 1, sheet.max_row + 1) if str(sheet.cell(row, 2).value or "").startswith("TOTAL"))
    assert sheet.cell(total_row, result_col).value == pytest.approx(17)
    assert sheet.cell(total_row, cost_col).value == pytest.approx(1900 / 17)
    assert sheet.cell(total_row, value_col).value == pytest.approx(950)
    assert sheet.cell(total_row, roas_col).value == pytest.approx(0.5)
    assert sheet.cell(header_row, result_col).alignment.wrap_text is True
    assert sheet.row_dimensions[header_row].height >= 42
    assert sheet.column_dimensions["C"].width >= 20


def _workbook_bytes(workbook):
    from io import BytesIO
    stream = BytesIO()
    workbook.save(stream)
    return stream.getvalue()

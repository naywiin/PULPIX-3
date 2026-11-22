from datetime import date

import pytest

from app.cache import TTLCache, perf_key
from app.dates import compare_range, parse_date
from app.metrics import derive
from app.config import Settings
from app.exclusions import EXCLUDED_CLIENT_PATTERNS, filter_accounts, is_excluded
from app.ga4_consolidate import consolidate, currencies
from app.models import MAX_GA4_PROPERTIES, Client, ClientFields, account_ids
from app.providers.base import ProviderError, sanitize
from app.providers.ga4 import GA4_SCOPES, GA4Provider
from app.providers.google_ads import GoogleAdsProvider
from app.providers.meta import MetaProvider, normalize_row, pick_action, pick_leads, pick_purchases
from app.ads_consolidate import merge_daily, sum_rows


def d(s: str) -> date:
    return date.fromisoformat(s)


class FakePropertySummary:
    def __init__(self, property_id: str, name: str) -> None:
        self.property = f"properties/{property_id}"
        self.display_name = name
        self.property_type = type("T", (), {"name": "PROPERTY_TYPE_ORDINARY"})()


class FakeAccountSummary:
    def __init__(self, account_id: str, name: str, props: list[tuple[str, str]]) -> None:
        self.account = f"accounts/{account_id}"
        self.display_name = name
        self.property_summaries = [FakePropertySummary(pid, pname) for pid, pname in props]


class FakePage:
    def __init__(self, account_summaries: list[FakeAccountSummary]) -> None:
        self.account_summaries = account_summaries


class TestDates:
    def test_previous_year_same_dates(self):
        assert compare_range("previous_year", d("2026-09-01"), d("2026-09-24")) == (d("2025-09-01"), d("2025-09-24"))

    def test_previous_year_leap_day(self):
        assert compare_range("previous_year", d("2028-02-29"), d("2028-02-29")) == (d("2027-02-28"), d("2027-02-28"))

    def test_previous_period_same_length(self):
        assert compare_range("previous_period", d("2026-09-01"), d("2026-09-24")) == (d("2026-08-08"), d("2026-08-31"))

    def test_previous_month(self):
        assert compare_range("previous_month", d("2026-09-01"), d("2026-09-24")) == (d("2026-08-01"), d("2026-08-24"))
        assert compare_range("previous_month", d("2026-03-01"), d("2026-03-31")) == (d("2026-02-01"), d("2026-02-28"))

    def test_none_and_custom(self):
        assert compare_range("none", d("2026-09-01"), d("2026-09-24")) is None
        assert compare_range("custom", d("2026-09-01"), d("2026-09-24"), d("2026-07-10"), d("2026-07-01")) == (d("2026-07-01"), d("2026-07-10"))
        with pytest.raises(ValueError):
            compare_range("custom", d("2026-09-01"), d("2026-09-24"))

    def test_parse_date_rejects_injection(self):
        with pytest.raises(ValueError):
            parse_date("2026-09-01' OR 1=1 --")


class TestMetrics:
    def test_roas_is_ratio_not_currency(self):
        m = derive({"impressions": 4983, "clicks": 762, "spend": 3613.39, "conversions": 19, "conversion_value": 8800})
        assert round(m["roas"], 2) == 2.44
        assert round(m["cpa"], 2) == 190.18
        assert round(m["conversion_rate"], 4) == round(19 / 762, 4)

    def test_zero_division_safe(self):
        m = derive({"impressions": 0, "clicks": 0, "spend": 0, "conversions": 0, "conversion_value": 0})
        assert m["roas"] is None and m["cpa"] is None and m["ctr"] is None


class TestCache:
    def test_keys_never_shared_between_clients(self):
        cache = TTLCache(300)
        calls = []
        k1 = perf_key("cliente-a", "google_ads", "123", "2026-09-01", "2026-09-24")
        k2 = perf_key("cliente-b", "google_ads", "123", "2026-09-01", "2026-09-24")
        v1, _, hit1 = cache.get_or_set(k1, lambda: calls.append("a") or "A")
        v2, _, hit2 = cache.get_or_set(k2, lambda: calls.append("b") or "B")
        assert (v1, v2, hit1, hit2) == ("A", "B", False, False)
        assert cache.get_or_set(k1, lambda: "X")[0] == "A"
        assert calls == ["a", "b"]

    def test_expiry_and_refresh(self):
        cache = TTLCache(0)
        cache.set("k", 1)
        assert cache.get("k") is None
        cache = TTLCache(300)
        cache.set("k", 1)
        assert cache.get_or_set("k", lambda: 2, refresh=True)[0] == 2


class TestMetaParsing:
    def test_filtered_campaign_totals_normalize_actions_before_aggregation(self):
        provider = MetaProvider(Settings())
        raw = {"campaign_id": "1", "campaign_name": "Leads", "objective": "OUTCOME_LEADS",
               "spend": "1000", "clicks": "100", "impressions": "1000", "reach": "500",
               "actions": [{"action_type": "lead", "value": "10"}, {"action_type": "purchase", "value": "2"}],
               "action_values": [{"action_type": "purchase", "value": "500"}], "account_currency": "MXN",
               "date_start": "2026-09-01"}
        provider._insights = lambda *args, **kwargs: [raw]
        result = provider.performance("123", "2026-09-01", "2026-09-01", campaign_ids=["1"])
        assert result["totals"]["leads"] == 10
        assert result["totals"]["conversions"] == 2
        assert result["totals"]["purchases"] == 2
        assert result["totals"]["cost_per_purchase"] == 500
        assert result["totals"]["cost_per_result"] == 100
        assert result["totals"]["value_available"] is True

    def test_leads_are_distinct_from_purchases_and_cost_is_safe(self):
        actions = [
            {"action_type": "purchase", "value": "4"},
            {"action_type": "lead", "value": "10"},
            {"action_type": "omni_lead", "value": "10"},
        ]
        row = normalize_row({"spend": "1000", "clicks": "20", "impressions": "100", "actions": actions})
        assert pick_leads(actions) == 10
        assert row["conversions"] == 4
        assert row["purchases"] == 4
        assert row["cost_per_purchase"] == 250
        assert row["leads"] == 10
        assert row["cost_per_result"] == 100
        assert normalize_row({"spend": "1000", "actions": [{"action_type": "lead", "value": "0"}]})["cost_per_result"] is None
        assert normalize_row({"spend": "1000", "actions": None})["leads"] is None
        assert sum_rows([{"spend": 1000, "leads": 10}])["cost_per_result"] == 100
        assert sum_rows([{"spend": 1000, "leads": 0}])["cost_per_result"] is None
        assert sum_rows([{"spend": 1000, "leads": None}])["leads"] is None

    def test_pick_action_prefers_omni_purchase_without_double_counting(self):
        actions = [
            {"action_type": "link_click", "value": "100"},
            {"action_type": "offsite_conversion.fb_pixel_purchase", "value": "7"},
            {"action_type": "omni_purchase", "value": "8"},
            {"action_type": "purchase", "value": "8"},
        ]
        assert pick_action(actions) == 8.0
        assert pick_action(None) == 0.0
        assert pick_purchases(actions) == 8.0

    def test_purchase_null_zero_and_aggregate_cost(self):
        measured = normalize_row({"spend": "1000", "actions": [{"action_type": "purchase", "value": "10"}]})
        zero = normalize_row({"spend": "1000", "actions": [{"action_type": "purchase", "value": "0"}]})
        absent = normalize_row({"spend": "1000", "actions": None})
        assert (measured["purchases"], measured["cost_per_purchase"]) == (10, 100)
        assert (zero["purchases"], zero["cost_per_purchase"]) == (0, None)
        assert (absent["purchases"], absent["cost_per_purchase"]) == (None, None)
        total = sum_rows([measured, measured])
        assert (total["purchases"], total["cost_per_purchase"]) == (20, 100)
        assert sum_rows([measured, absent])["purchases"] is None
        assert pick_purchases([{"action_type": "lead", "value": "10"}]) is None

    def test_cirque_custom_purchase_precedes_equivalent_variants_and_matches_value(self):
        custom = "offsite_conversion.custom.1366991912161544"
        actions = [
            {"action_type": "omni_purchase", "value": "99"},
            {"action_type": "offsite_conversion.fb_pixel_purchase", "value": "99"},
            {"action_type": custom, "value": "7"},
            {"action_type": "offsite_conversion.custom.1547453603727986", "value": "40"},
        ]
        values = [
            {"action_type": "omni_purchase", "value": "9900"},
            {"action_type": custom, "value": "700"},
        ]
        row = normalize_row({"spend": "1400", "actions": actions, "action_values": values}, "2048742145193546")
        assert row["purchases"] == row["conversions"] == 7
        assert row["cost_per_purchase"] == 200
        assert row["conversion_value"] == 700
        assert row["roas"] == 0.5
        assert sum_rows([row, row])["purchases"] == 14
        assert sum_rows([row, row])["cost_per_purchase"] == 200
        other_account = normalize_row({"spend": "1400", "actions": actions, "action_values": values}, "other-account")
        assert other_account["purchases"] == 99
        assert other_account["conversion_value"] == 9900
        missing_custom_value = normalize_row({
            "spend": "1400", "actions": actions,
            "action_values": [{"action_type": "omni_purchase", "value": "9900"}],
        }, "2048742145193546")
        assert missing_custom_value["purchases"] == 7
        assert missing_custom_value["value_available"] is False
        assert missing_custom_value["conversion_value"] is None
        assert missing_custom_value["roas"] is None

    def test_missing_purchase_action_value_stays_null_through_consolidation(self):
        absent = normalize_row({
            "spend": "1000", "actions": [{"action_type": "lead", "value": "5"}],
            "action_values": [{"action_type": "omni_purchase", "value": "500"}],
        })
        assert absent["purchases"] is None
        assert absent["conversions"] == 0
        assert absent["leads"] == 5
        assert absent["cost_per_purchase"] is None
        assert absent["conversion_value"] is None
        assert absent["roas"] is None
        total = sum_rows([absent])
        assert total["purchases"] is None
        assert total["conversion_value"] is None
        assert total["roas"] is None
        assert total["cost_per_purchase"] is None
        daily = merge_daily([{**absent, "date": "2026-10-01"}])
        assert daily[0]["conversion_value"] is None
        assert daily[0]["roas"] is None

    def test_jungala_omni_purchase_and_lead_clients_keep_their_results(self):
        ecommerce = normalize_row({"spend": "1000", "actions": [
            {"action_type": "omni_purchase", "value": "3"},
            {"action_type": "purchase", "value": "3"},
        ]})
        assert ecommerce["purchases"] == 3
        assert ecommerce["cost_per_purchase"] == pytest.approx(1000 / 3)
        lead = normalize_row({"spend": "1000", "actions": [
            {"action_type": "omni_purchase", "value": "12"},
            {"action_type": "lead", "value": "5"},
        ]})
        assert lead["leads"] == 5
        assert lead["cost_per_result"] == 200

    def test_normalize_row(self):
        row = {
            "impressions": "1000", "reach": "400", "clicks": "50", "spend": "250.50", "frequency": "2.5",
            "actions": [{"action_type": "purchase", "value": "5"}],
            "action_values": [{"action_type": "purchase", "value": "1000"}],
            "purchase_roas": [{"action_type": "omni_purchase", "value": "3.99"}],
        }
        n = normalize_row(row)
        assert n["conversions"] == 5 and n["conversion_value"] == 1000
        assert round(n["roas"], 2) == 3.99 and round(n["cpa"], 2) == 50.1
        assert n["frequency"] == 2.5 and n["purchase_roas_native"] == 3.99


class FakeStatus:
    def __init__(self, name: str) -> None:
        self.name = name


class FakeClient:
    """Fila de customer_client tal como la devuelve la API (proto-plus)."""

    def __init__(self, id, name, manager=False, level=1, status="ENABLED", hidden=False, test=False):
        self.id = id
        self.descriptive_name = name
        self.manager = manager
        self.level = level
        self.status = FakeStatus(status)
        self.currency_code = "MXN"
        self.time_zone = "America/Mexico_City"
        self.test_account = test
        self.hidden = hidden


class TestGoogleAdsHierarchy:
    """list_accounts() sale de customer_client en el MCC raíz, no de list_accessible_customers()."""

    ROWS = [
        FakeClient(7954113535, "MCC Tachuela", manager=True, level=0),
        FakeClient(6364343858, "Blumia"),
        FakeClient(4260570007, "Grupo Vidanta", manager=True, level=1),
        FakeClient(2980367668, "VidantaWorld", level=2),          # indirecta
        FakeClient(1111111111, "Oculta", hidden=True),
        FakeClient(3781931339, "Cancelada", status="CANCELED"),
        FakeClient(2980367668, "VidantaWorld (otra ruta)", level=3),  # duplicada
    ]

    def provider(self) -> GoogleAdsProvider:
        p = GoogleAdsProvider(Settings())
        p._hierarchy_rows = lambda: list(self.ROWS)  # type: ignore[method-assign]
        return p

    def test_excludes_root_and_hidden_and_dedupes(self):
        accounts = self.provider().list_accounts()
        ids = {a["id"] for a in accounts}
        assert ids == {"6364343858", "4260570007", "2980367668", "3781931339"}
        assert len(accounts) == 4
        # La cuenta duplicada se queda con la ruta más corta.
        assert next(a for a in accounts if a["id"] == "2980367668")["level"] == 2

    def test_separates_managers_from_advertisers(self):
        accounts = self.provider().list_accounts()
        managers = [a["id"] for a in accounts if a["account_type"] == "manager"]
        advertisers = [a["id"] for a in accounts if a["account_type"] == "client"]
        assert managers == ["4260570007"]
        assert sorted(advertisers) == ["2980367668", "3781931339", "6364343858"]

    def test_every_account_carries_the_root_mcc_as_login_customer_id(self):
        mcc = Settings().google_ads_manager_customer_id
        assert mcc == "7954113535"
        assert all(a["login_customer_id"] == mcc for a in self.provider().list_accounts())

    def test_include_flags_return_root_and_hidden(self):
        accounts = self.provider().list_accounts(include_hidden=True, include_root=True)
        ids = {a["id"] for a in accounts}
        assert "7954113535" in ids and "1111111111" in ids

    def test_enabled_accounts_come_first(self):
        accounts = self.provider().list_accounts()
        assert accounts[-1]["id"] == "3781931339"

    def test_performance_falls_back_to_the_root_mcc_as_login_customer_id(self):
        p = GoogleAdsProvider(Settings())
        seen: dict = {}

        def fake_search(customer_id, query, login_customer_id=None):
            seen.update(customer_id=customer_id, login=login_customer_id)
            return iter(())

        p._search = fake_search  # type: ignore[method-assign]
        p.performance("6364343858", None, "2026-09-01", "2026-09-24")
        assert seen == {"customer_id": "6364343858", "login": "7954113535"}


class TestGA4Credentials:
    """GA4 pide sus propias ADC con scope analytics.readonly (no reutiliza las de Ads)."""

    def _patched(self, monkeypatch):
        import google.analytics.admin_v1beta as admin_mod
        import google.analytics.data_v1beta as data_mod
        import google.auth

        creds = object()
        asked: list[list[str] | None] = []
        got: dict[str, object] = {}

        def fake_default(scopes=None, **kw):
            asked.append(scopes)
            return creds, "marketing-performance-prod"

        monkeypatch.setattr(google.auth, "default", fake_default)
        monkeypatch.setattr(data_mod, "BetaAnalyticsDataClient", lambda credentials=None: got.setdefault("data", credentials))
        monkeypatch.setattr(admin_mod, "AnalyticsAdminServiceClient", lambda credentials=None: got.setdefault("admin", credentials))
        return GA4Provider(Settings()), creds, asked, got

    def test_both_clients_get_credentials_scoped_to_analytics_readonly(self, monkeypatch):
        provider, creds, asked, got = self._patched(monkeypatch)
        provider._data_client()
        provider._admin_client()
        assert got == {"data": creds, "admin": creds}
        assert asked == [["https://www.googleapis.com/auth/analytics.readonly"]]
        assert GA4_SCOPES == ["https://www.googleapis.com/auth/analytics.readonly"]

    def test_scope_is_never_the_google_ads_one(self, monkeypatch):
        provider, _, asked, _ = self._patched(monkeypatch)
        provider._admin_client()
        assert all("adwords" not in scope for scopes in asked for scope in (scopes or []))

    def test_adc_failure_is_reported_as_provider_error(self, monkeypatch):
        import google.auth

        def boom(scopes=None, **kw):
            raise RuntimeError("no ADC")

        monkeypatch.setattr(google.auth, "default", boom)
        provider = GA4Provider(Settings())
        with pytest.raises(ProviderError, match="credenciales ADC para GA4"):
            provider._admin_client()


class TestExclusions:
    """Lista central: solo afecta a esta aplicación, no a las plataformas."""

    def test_pattern_is_case_insensitive_and_matches_inside_the_name(self):
        assert EXCLUDED_CLIENT_PATTERNS == ("vidanta", "vida vacations", "the view")
        for name in ("Vidanta", "VIDANTA", "vidanta", "VidantaWorld", "Grupo Vidanta", "MCC vidanta 2026",
                     "Marketing Vidanta", "Vida Vacations", "VIDA VACATIONS", "vida vacations MX",
                     "The View", "THE VIEW", "The View Cancun"):
            assert is_excluded(name), name

    def test_trims_and_collapses_whitespace_before_comparing(self):
        for name in ("  The View  ", "The  View", "\tVIDA   VACATIONS\n"):
            assert is_excluded(name), name

    def test_other_clients_are_never_excluded(self):
        for name in ("Blumia", "Ssiento", "Jungala", "Cliente X", "Grupo Regio", "Doc Towers", "Bambino",
                     "Clarvi", "Matermalia", "Santuario Veracruz", "Sign Factory", "IUPI",
                     "Xcaret Info Parks", "Vida Nueva", "Viewpoint", None, ""):
            assert not is_excluded(name), name

    def test_the_owning_business_never_excludes_a_client(self):
        """Regresión: la agencia "MarketingVidanta" administra cuentas de clientes
        que SÍ se quieren ver. Excluir por el Business Manager las borraba del
        selector de Meta Ads (datos reales de /me/adaccounts)."""
        accounts = [
            {"id": "2048742145193546", "name": "Cirque du Soleil JOYÀ", "business": "MarketingVidanta"},
            {"id": "802869966755765", "name": "Jungala", "business": "MarketingVidanta"},
            {"id": "1082598430772317", "name": "Cirque du Soleil JOYÀ_1 (Read-Only)", "business": "MarketingVidanta"},
            {"id": "633680236078983", "name": "Cirque du Soleil LUDÕ", "business": "VidantaWorld"},
            {"id": "1166529450042730", "name": "Joyá by Cirque du Soleil", "business": "Punk Marketing"},
        ]
        kept, dropped = filter_accounts(accounts)
        assert dropped == []
        assert [a["id"] for a in kept] == [a["id"] for a in accounts]

    def test_accounts_named_after_an_excluded_client_are_still_dropped(self):
        """El nombre propio de la cuenta sigue mandando, aunque el business no."""
        accounts = [
            {"id": "1", "name": "Vidanta Ads", "business": "MarketingVidanta"},
            {"id": "2", "name": "VIDANTA RESORTS", "business": "MarketingVidanta"},
            {"id": "3", "name": "VidantaWorld", "business": "VidantaWorld"},
            {"id": "4", "name": "Vida Vacations MX", "business": "Vida Vacations México"},
            {"id": "5", "name": "The View Cuenta publicitaria", "business": "THE VIEW"},
            {"id": "6", "name": "Blumia.mx", "business": "blumia_mx"},
        ]
        kept, dropped = filter_accounts(accounts)
        assert [a["id"] for a in kept] == ["6"]
        assert [d["id"] for d in dropped] == ["1", "2", "3", "4", "5"]

    def test_filter_accounts_reports_what_it_dropped(self):
        accounts = [
            {"id": "1", "name": "Blumia Website", "account_name": "Cuenta Blumia"},
            {"id": "2", "name": "Propiedad X", "account_name": "VIDANTA"},
            {"id": "3", "name": "VidantaWorld", "account_name": "Tachuela"},
        ]
        kept, dropped = filter_accounts(accounts)
        assert [a["id"] for a in kept] == ["1"]
        assert [d["id"] for d in dropped] == ["2", "3"]
        assert {d["pattern"] for d in dropped} == {"vidanta"}


class TestGA4Discovery:
    """ListAccountSummaries: todas las cuentas, todas las propiedades, N páginas."""

    def _provider(self, monkeypatch, pages):
        import google.analytics.admin_v1beta as admin_mod

        class FakePager:
            pages = None  # se asigna abajo

        class FakeAdmin:
            def list_account_summaries(self, request=None):
                pager = FakePager()
                FakePager.pages = pages
                assert request.page_size == 200
                return pager

            def get_property(self, name):
                raise RuntimeError("sin permiso de detalle")

        monkeypatch.setattr(admin_mod, "AnalyticsAdminServiceClient", lambda credentials=None: FakeAdmin())
        import google.auth

        monkeypatch.setattr(google.auth, "default", lambda scopes=None, **kw: (object(), "proj"))
        return GA4Provider(Settings())

    def test_returns_every_property_of_every_account_across_pages(self, monkeypatch):
        pages = [
            FakePage([FakeAccountSummary("123", "Cuenta Blumia", [("496398757", "Blumia Website"), ("496398758", "Blumia App")])]),
            FakePage([FakeAccountSummary("456", "Cuenta Ssiento", [("480211892", "Ssiento MX")])]),
        ]
        props = self._provider(monkeypatch, pages).list_accounts()
        assert len(props) == 3
        blumia = [p for p in props if p["account_id"] == "123"]
        assert len(blumia) == 2  # una cuenta puede tener varias propiedades
        p = blumia[0]  # ordenadas por cuenta y luego por nombre de propiedad
        assert p["platform"] == "ga4"
        assert (p["id"], p["property_id"], p["property_name"]) == ("496398758", "496398758", "Blumia App")
        assert p["property_resource"] == "properties/496398758"
        assert p["account_name"] == "Cuenta Blumia" and p["account_resource"] == "accounts/123"

    def test_property_ids_are_never_shared_between_accounts(self, monkeypatch):
        pages = [FakePage([
            FakeAccountSummary("1", "A", [("111", "Prop A")]),
            FakeAccountSummary("2", "B", [("222", "Prop B")]),
        ])]
        props = self._provider(monkeypatch, pages).list_accounts()
        assert {p["id"]: p["account_id"] for p in props} == {"111": "1", "222": "2"}


def ga4_period(sessions, engaged, users, key_events=0.0, revenue=0.0, currency="MXN", daily=(), rows=()):
    """Periodo GA4 tal como lo devuelve GA4Provider.performance."""
    def m(s, e, u, k=0.0, r=0.0):
        return {"users": u, "sessions": s, "engaged_sessions": e,
                "engagement_rate": (e / s) if s else 0.0, "key_events": k, "revenue": r}
    return {
        "currency": currency,
        "totals": m(sessions, engaged, users, key_events, revenue),
        "daily": [{"date": d, **m(*vals)} for d, vals in daily],
        "by_source_medium": [{"name": n, **m(*vals)} for n, vals in rows],
        "by_campaign": [], "by_landing_page": [],
    }


class TestGA4Consolidation:
    """Varias propiedades de un cliente en un solo periodo."""

    def test_sums_additive_metrics_and_recomputes_engagement_rate(self):
        a = ga4_period(sessions=100, engaged=60, users=80, key_events=5, revenue=1000)
        b = ga4_period(sessions=300, engaged=90, users=40, key_events=2.5, revenue=500)
        t = consolidate([a, b])["totals"]
        assert (t["sessions"], t["engaged_sessions"], t["users"]) == (400, 150, 120)
        assert t["key_events"] == 7.5 and t["revenue"] == 1500
        # SUM(engaged)/SUM(sessions) = 150/400, NO el promedio de 0.60 y 0.30.
        assert t["engagement_rate"] == 150 / 400 != (0.60 + 0.30) / 2

    def test_engagement_rate_is_zero_without_sessions(self):
        assert consolidate([ga4_period(0, 0, 0)])["totals"]["engagement_rate"] == 0.0
        assert consolidate([])["totals"] == {"users": 0, "sessions": 0, "engaged_sessions": 0,
                                             "engagement_rate": 0.0, "key_events": 0.0, "revenue": 0.0}

    def test_daily_series_merges_by_date_and_stays_sorted(self):
        a = ga4_period(0, 0, 0, daily=[("2026-09-01", (10, 5, 8)), ("2026-09-03", (30, 15, 20))])
        b = ga4_period(0, 0, 0, daily=[("2026-09-02", (7, 7, 7)), ("2026-09-03", (70, 25, 30))])
        daily = consolidate([a, b])["daily"]
        assert [d["date"] for d in daily] == ["2026-09-01", "2026-09-02", "2026-09-03"]
        assert daily[2]["sessions"] == 100 and daily[2]["engaged_sessions"] == 40
        assert daily[2]["engagement_rate"] == 40 / 100

    def test_breakdown_rows_with_the_same_name_are_merged_and_ranked(self):
        a = ga4_period(0, 0, 0, rows=[("google / cpc", (100, 50, 90)), ("(direct) / (none)", (10, 2, 9))])
        b = ga4_period(0, 0, 0, rows=[("google / cpc", (400, 100, 300))])
        rows = consolidate([a, b])["by_source_medium"]
        assert [r["name"] for r in rows] == ["google / cpc", "(direct) / (none)"]  # por sessions desc
        assert rows[0]["sessions"] == 500 and rows[0]["engagement_rate"] == 150 / 500

    def test_reports_how_many_properties_and_which_currencies(self):
        out = consolidate([ga4_period(1, 1, 1), ga4_period(2, 2, 2)])
        assert out["properties_count"] == 2
        assert currencies([ga4_period(1, 1, 1, currency="MXN"), ga4_period(1, 1, 1, currency="USD")]) == ["MXN", "USD"]
        assert currencies([ga4_period(1, 1, 1), ga4_period(1, 1, 1)]) == ["MXN"]


class TestClientGA4Migration:
    """Compatibilidad: ga4_property_id (antiguo) ↔ ga4_property_ids (nuevo)."""

    def test_legacy_single_property_is_migrated_to_the_array(self):
        c = Client(id="x", name="X", slug="x", ga4_property_id="264078195")
        assert c.ga4_property_ids == ["264078195"]
        assert c.ga4_property_id == "264078195"

    def test_array_keeps_the_legacy_field_as_its_first_element(self):
        c = Client(id="x", name="X", slug="x", ga4_property_ids=["304259869", "264078195"])
        assert c.ga4_property_id == "304259869"
        assert account_ids(c, "ga4") == ["304259869", "264078195"]

    def test_array_wins_when_both_are_present(self):
        c = Client(id="x", name="X", slug="x", ga4_property_id="111", ga4_property_ids=["222", "333"])
        assert c.ga4_property_ids == ["222", "333"] and c.ga4_property_id == "222"

    def test_ids_are_normalized_and_deduplicated(self):
        c = ClientFields(ga4_property_ids=["properties/264078195", "264078195", " 304259869 "])
        assert c.ga4_property_ids == ["264078195", "304259869"]

    def test_rejects_non_numeric_ids_and_oversized_lists(self):
        with pytest.raises(ValueError):
            ClientFields(ga4_property_ids=["no-es-un-id"])
        with pytest.raises(ValueError):
            ClientFields(ga4_property_ids=[str(i) for i in range(1000, 1000 + MAX_GA4_PROPERTIES + 1)])

    def test_no_properties_means_no_account_ids(self):
        c = Client(id="x", name="X", slug="x")
        assert c.ga4_property_ids == [] and c.ga4_property_id is None and account_ids(c, "ga4") == []

    def test_single_account_platforms_are_unaffected(self):
        c = Client(id="x", name="X", slug="x", google_ads_customer_id="123", meta_ad_account_id="456")
        assert account_ids(c, "google_ads") == ["123"] and account_ids(c, "meta_ads") == ["456"]


def test_sanitize_removes_tokens():
    msg = "GET https://graph.facebook.com/v24.0/me?access_token=EAABsecret123&x=1 Authorization: Bearer abc.def-ghi EAAG" + "x" * 30
    out = sanitize(msg)
    assert "EAABsecret123" not in out and "abc.def-ghi" not in out and "EAAG" + "x" * 30 not in out

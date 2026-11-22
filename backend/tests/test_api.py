import json
"""Tests de la API con proveedores falsos (no llaman a Google/Meta)."""
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.providers.base import ProviderError, ProviderNotConfigured
from app.providers.meta import MetaProvider
from app.registry import JsonClientRepository


def totals(spend: float, conv: float, value: float) -> dict:
    return {"impressions": 100, "clicks": 10, "spend": spend, "conversions": conv, "conversion_value": value,
            "roas": value / spend if spend else 0}


class FakeGoogleAds:
    def __init__(self, fail: bool = False):
        self.fail = fail
        self.calls: list[tuple] = []

    def performance(self, customer_id, login, start, end, campaign_ids=None):
        self.calls.append((customer_id, login, start, end, tuple(campaign_ids or ())))
        if self.fail:
            raise ProviderError("simulated outage")
        return {"currency": "MXN", "totals": totals(float(customer_id[-3:]), 2, 400), "campaigns": [], "daily": []}

    def list_campaigns(self, customer_id, login_customer_id=None):
        return []

    def list_accounts(self):
        return [{"platform": "google_ads", "id": "1110000111", "name": "Acme GAds", "currency": "MXN", "timezone": "America/Mexico_City",
                 "status": "ENABLED", "account_type": "client", "level": 1, "login_customer_id": "7954113535"},
                {"platform": "google_ads", "id": "9990000999", "name": "MCC secundario", "currency": "MXN", "timezone": "America/Mexico_City",
                 "status": "ENABLED", "account_type": "manager", "level": 1, "login_customer_id": "7954113535"}]

    def diagnostics(self):
        return {"manager_customer_id": "7954113535", "developer_token_present": False,
                "hierarchy": {"status": "ok", "rows": 3, "returned": 2, "managers": 1, "advertisers": 1},
                "accessible_customers": {"status": "ok", "count": 1, "ids": ["7954113535"]}}


class FakeMeta:
    """Meta sin credenciales: ejercita el camino "not_configured"."""

    def performance(self, account_id, start, end, campaign_ids=None):
        raise ProviderNotConfigured("falta META_ACCESS_TOKEN")

    def adsets(self, *a, **kw):
        return []

    def list_accounts(self):
        raise ProviderNotConfigured("falta META_ACCESS_TOKEN")

    def list_campaigns(self, account_id):
        raise ProviderNotConfigured("falta META_ACCESS_TOKEN")


class FakeGA4:
    """Cada propiedad devuelve métricas proporcionales a su ID, para poder
    comprobar que el consolidado suma exactamente lo que debe."""

    names = {"333": "Acme Web", "334": "Acme App", "555": "Beta Web"}

    def __init__(self):
        self.calls: list[tuple] = []
        #: property_id → excepción a lanzar (simula una propiedad caída)
        self.broken: dict[str, Exception] = {}
        self.currency_of: dict[str, str] = {}

    def performance(self, property_id, start, end):
        self.calls.append((property_id, start, end))
        if property_id in self.broken:
            raise self.broken[property_id]
        n = int(property_id[-1])  # 333 → 3, 334 → 4
        sessions, engaged = 100 * n, 40 * n
        row = {"users": 10 * n, "sessions": sessions, "engaged_sessions": engaged,
               "engagement_rate": engaged / sessions, "key_events": float(n), "revenue": 100.0 * n}
        return {
            "property_id": property_id,
            "property_name": self.names.get(property_id),
            "currency": self.currency_of.get(property_id, "MXN"),
            "totals": row,
            "daily": [{"date": start, **row}],
            "by_source_medium": [{"name": "google / cpc", **row}],
            "by_campaign": [], "by_landing_page": [],
        }

    def list_accounts(self):
        def prop(pid, name, account_id, account_name):
            return {"platform": "ga4", "id": pid, "name": name, "property_id": pid, "property_name": name,
                    "property_resource": f"properties/{pid}", "account_id": account_id, "account_name": account_name,
                    "account_resource": f"accounts/{account_id}", "currency": "MXN", "timezone": "America/Mexico_City",
                    "status": "ACTIVE", "account_type": "ordinary"}
        return [
            prop("333", "Acme Web", "10", "Cuenta Acme"),
            prop("334", "Acme App", "10", "Cuenta Acme"),
            prop("555", "Beta Web", "20", "Cuenta Beta"),
            prop("999", "Resorts", "30", "VIDANTA"),        # excluida por la cuenta
            prop("998", "VidantaWorld Web", "40", "Otra"),  # excluida por la propiedad
        ]


@pytest.fixture
def setup(tmp_path: Path):
    settings = Settings(clients_file=tmp_path / "clients.json", static_dir=tmp_path / "nodist")
    repo = JsonClientRepository(settings.clients_file)
    gads = FakeGoogleAds()
    providers = {"google_ads": gads, "meta_ads": FakeMeta(), "ga4": FakeGA4()}
    app = create_app(settings, repo, providers)
    client = TestClient(app)
    client.post("/api/clients", json={"name": "Acme", "google_ads_customer_id": "111-000-0111", "meta_ad_account_id": "act_222", "ga4_property_id": "properties/333"})
    client.post("/api/clients", json={"name": "Beta Corp", "google_ads_customer_id": "4440000444"})
    return client, gads, providers


def test_clients_crud_and_normalization(setup):
    client, *_ = setup
    r = client.get("/api/clients").json()["clients"]
    assert [c["id"] for c in r] == ["acme", "beta-corp"]
    acme = r[0]
    assert acme["google_ads_customer_id"] == "1110000111"
    assert acme["meta_ad_account_id"] == "222" and acme["ga4_property_id"] == "333"

    upd = client.put("/api/clients/acme", json={"active": False, "logo": "https://example.com/l.png"}).json()
    assert upd["active"] is False and upd["google_ads_customer_id"] == "1110000111"
    assert [c["id"] for c in client.get("/api/clients?active_only=true").json()["clients"]] == ["beta-corp"]

    # desasignar explícitamente
    upd = client.put("/api/clients/acme", json={"ga4_property_id": None}).json()
    assert upd["ga4_property_id"] is None


def test_account_cannot_be_assigned_to_two_clients(setup):
    client, *_ = setup
    r = client.put("/api/clients/beta-corp", json={"google_ads_customer_id": "1110000111"})
    assert r.status_code == 409
    r = client.post("/api/clients", json={"name": "Acme"})
    assert r.status_code == 409
    assert client.get("/api/clients/nope").status_code == 404


def test_rejects_secrets_like_values_in_ids(setup):
    client, *_ = setup
    r = client.put("/api/clients/acme", json={"meta_ad_account_id": "EAABtoken"})
    assert r.status_code == 422


def test_dashboard_isolates_platform_failures(setup):
    client, gads, _ = setup
    r = client.get("/api/dashboard/acme", params={"start_date": "2026-09-01", "end_date": "2026-09-24", "comparison": "previous_year"})
    assert r.status_code == 200
    d = r.json()
    assert d["compare_range"] == {"start": "2025-09-01", "end": "2025-09-24"}
    p = d["platforms"]
    assert p["google_ads"]["status"] == "ok" and p["google_ads"]["source"] == "real"
    assert p["google_ads"]["previous"] is not None
    assert p["ga4"]["status"] == "ok"
    assert p["meta_ads"]["status"] == "not_configured"
    assert p["meta_ads"]["message"] == "Meta Ads temporalmente no disponible"

    gads.fail = True
    d = client.get("/api/dashboard/acme", params={"start_date": "2026-08-01", "end_date": "2026-08-31"}).json()
    assert d["platforms"]["google_ads"]["status"] == "error"
    assert d["platforms"]["google_ads"]["message"] == "Google Ads temporalmente no disponible"
    assert d["platforms"]["ga4"]["status"] == "ok"


def test_cirque_custom_purchase_reaches_dashboard_and_detail_endpoints(setup):
    client, _, providers = setup
    assert client.put("/api/clients/acme", json={
        "meta_ad_account_id": "2048742145193546",
    }).status_code == 200
    meta = MetaProvider(Settings())
    custom = "offsite_conversion.custom.1366991912161544"
    raw = {
        "campaign_id": "campaign-1", "campaign_name": "JOYÀ", "adset_id": "adset-1",
        "adset_name": "Conjunto JOYÀ", "ad_id": "ad-1", "ad_name": "Anuncio JOYÀ",
        "spend": "1400", "impressions": "1000", "clicks": "100", "reach": "500",
        "account_currency": "MXN", "date_start": "2026-09-07",
        "actions": [
            {"action_type": custom, "value": "7"},
            {"action_type": "omni_purchase", "value": "99"},
            {"action_type": "offsite_conversion.fb_pixel_purchase", "value": "99"},
        ],
        "action_values": [
            {"action_type": custom, "value": "700"},
            {"action_type": "omni_purchase", "value": "9900"},
        ],
    }
    meta._insights = lambda *args, **kwargs: [raw]
    meta.list_campaigns = lambda account: [{
        "campaign_id": "campaign-1", "campaign_name": "JOYÀ", "status": "ACTIVE",
        "effective_status": "ACTIVE", "currency": "MXN",
    }]
    meta.list_ads = lambda account, campaign_ids=None, adset_ids=None: [{
        "ad_id": "ad-1", "ad_name": "Anuncio JOYÀ", "campaign_id": "campaign-1",
        "campaign_name": "JOYÀ", "ad_set_id": "adset-1", "ad_set_name": "Conjunto JOYÀ",
        "status": "ACTIVE", "effective_status": "ACTIVE", "source_account_id": account,
    }]
    providers["meta_ads"] = meta
    dates = {"start_date": "2026-09-07", "end_date": "2026-10-06"}
    dashboard = client.get("/api/dashboard/acme", params=dates)
    assert dashboard.status_code == 200
    totals = dashboard.json()["platforms"]["meta_ads"]["current"]["totals"]
    assert totals["purchases"] == 7
    assert totals["cost_per_purchase"] == 200
    assert totals["conversion_value"] == 700
    assert totals["roas"] == 0.5
    for level in ("campaign", "ad"):
        detail = client.get("/api/performance/detail", params={
            "client_id": "acme", "platform": "meta_ads", "level": level, **dates,
        })
        assert detail.status_code == 200, detail.text
        assert detail.json()["totals"]["purchases"] == 7
        assert detail.json()["totals"]["cost_per_purchase"] == 200
        assert detail.json()["rows"][0]["conversion_value"] == 700
        assert detail.json()["rows"][0]["roas"] == 0.5


def test_performance_detail_filters_current_status_and_period_spend(setup):
    client, google, _ = setup
    catalog = [
        {"platform": "google_ads", "campaign_id": "11", "campaign_name": "Active spend",
         "status": "ENABLED", "account_name": "Acme GAds", "currency": "MXN"},
        {"platform": "google_ads", "campaign_id": "11", "campaign_name": "Active spend",
         "status": "ENABLED", "account_name": "Acme GAds", "currency": "MXN"},
        {"platform": "google_ads", "campaign_id": "12", "campaign_name": "Paused with history",
         "status": "PAUSED", "account_name": "Acme GAds", "currency": "MXN"},
        {"platform": "google_ads", "campaign_id": "13", "campaign_name": "Active zero",
         "status": "ENABLED", "account_name": "Acme GAds", "currency": "MXN"},
    ]
    metrics = [
        {"campaign_id": "11", "campaign_name": "Active spend", "spend": 25.0, "impressions": 100,
         "clicks": 5, "conversions": 1, "conversion_value": 60},
        {"campaign_id": "12", "campaign_name": "Paused with history", "spend": 500.0, "impressions": 1000,
         "clicks": 100, "conversions": 10, "conversion_value": 900},
    ]
    google.list_campaigns = lambda account, login_customer_id=None: catalog
    google.performance = lambda account, login, start, end, campaign_ids=None: {
        "currency": "MXN", "campaigns": [r for r in metrics if campaign_ids is None or r["campaign_id"] in campaign_ids],
        "totals": totals(0, 0, 0), "daily": [],
    }
    base = {"client_id": "acme", "platform": "google_ads", "start_date": "2026-10-01", "end_date": "2026-10-05"}

    normal = client.get("/api/performance/detail", params=base).json()
    assert normal["level"] == "campaign"
    assert {r["campaign_id"] for r in normal["rows"]} == {"11", "12", "13"}

    inactive_with_spend = client.get("/api/performance/detail", params={
        **base, "status": "inactive", "spend": "with_spend",
    }).json()
    assert [r["campaign_id"] for r in inactive_with_spend["rows"]] == ["12"]
    assert inactive_with_spend["rows"][0]["status_label"] == "Pausado"
    assert inactive_with_spend["totals"]["spend"] == 500

    active_without_spend = client.get("/api/performance/detail", params={
        **base, "status": "active", "spend": "without_spend",
    }).json()
    assert [r["campaign_id"] for r in active_without_spend["rows"]] == ["13"]
    assert active_without_spend["rows"][0]["spend"] == 0

    assert client.get("/api/performance/detail", params={**base, "level": "unknown"}).status_code == 422
    assert client.get("/api/performance/detail", params={**base, "status": "paused"}).status_code == 422


def test_performance_detail_respects_assigned_campaign_scope(setup):
    client, google, _ = setup
    google.list_campaigns = lambda account, login_customer_id=None: [
        {"platform": "google_ads", "campaign_id": cid, "campaign_name": f"Campaign {cid}", "status": "ENABLED"}
        for cid in ("11", "12")
    ]
    google.performance = lambda account, login, start, end, campaign_ids=None: {
        "currency": "MXN", "campaigns": [
            {"campaign_id": cid, "campaign_name": f"Campaign {cid}", "spend": 10, "impressions": 100,
             "clicks": 2, "conversions": 1, "conversion_value": 5}
            for cid in (campaign_ids or ["11", "12"])
        ], "totals": totals(0, 0, 0), "daily": [],
    }
    assignment = client.put("/api/clients/acme", json={
        "google_ads_sources": [{
            "customer_id": "1110000111", "login_customer_id": "7954113535",
            "campaign_ids": ["12"], "allocation": "exclusive",
        }],
    })
    assert assignment.status_code == 200, assignment.text
    result = client.get("/api/performance/detail", params={
        "client_id": "acme", "platform": "google_ads", "start_date": "2026-10-01", "end_date": "2026-10-05",
    })
    assert result.status_code == 200
    assert [row["campaign_id"] for row in result.json()["rows"]] == ["12"]


def test_performance_detail_provider_error_is_not_returned_as_empty(setup):
    client, google, _ = setup
    google.list_campaigns = lambda account, login_customer_id=None: []
    google.performance = lambda *args, **kwargs: (_ for _ in ()).throw(ProviderError("simulated outage"))
    result = client.get("/api/performance/detail", params={
        "client_id": "acme", "platform": "google_ads", "start_date": "2026-10-01", "end_date": "2026-10-05",
    })
    assert result.status_code == 502
    assert "simulated outage" in result.json()["detail"]


def test_not_linked_platform(setup):
    client, *_ = setup
    d = client.get("/api/dashboard/beta-corp", params={"start_date": "2026-09-01", "end_date": "2026-09-24"}).json()
    assert d["platforms"]["meta_ads"]["status"] == "not_linked"
    assert d["platforms"]["ga4"]["status"] == "not_linked"
    assert d["platforms"]["google_ads"]["current"]["totals"]["spend"] == 444.0


def test_cache_per_client_and_refresh(setup):
    client, gads, _ = setup
    q = {"client_id": "acme", "start_date": "2026-09-01", "end_date": "2026-09-24"}
    a1 = client.get("/api/google-ads/performance", params=q).json()
    a2 = client.get("/api/google-ads/performance", params=q).json()
    assert a1["cached"] is False and a2["cached"] is True
    b = client.get("/api/google-ads/performance", params={**q, "client_id": "beta-corp"}).json()
    assert b["current"]["totals"]["spend"] == 444.0 and a1["current"]["totals"]["spend"] == 111.0
    assert len(gads.calls) == 2
    client.get("/api/google-ads/performance", params={**q, "refresh": "true"})
    assert len(gads.calls) == 3


def test_validation(setup):
    client, *_ = setup
    base = {"start_date": "2026-09-24", "end_date": "2026-09-01"}
    assert client.get("/api/dashboard/acme", params=base).status_code == 422
    assert client.get("/api/dashboard/acme", params={"start_date": "x", "end_date": "2026-09-01"}).status_code == 422
    assert client.get("/api/dashboard/acme", params={"start_date": "2026-09-01", "end_date": "2026-09-02", "comparison": "bad"}).status_code == 422


def test_discovery_marks_assignments_and_handles_missing_credentials(setup):
    client, *_ = setup
    g = client.get("/api/accounts/google-ads").json()
    assert g["status"] == "ok" and g["accounts"][0]["assigned_to"] == {"id": "acme", "name": "Acme"}
    # Anunciantes y MCC secundarios van separados en el resumen.
    assert g["summary"] == {"total": 2, "managers": 1, "advertisers": 1}
    m = client.get("/api/accounts/meta").json()
    assert m["status"] == "not_configured" and m["accounts"] == []


def test_google_ads_diagnostics_is_separate_from_discovery(setup):
    client, *_ = setup
    d = client.get("/api/accounts/google-ads/diagnostics").json()
    assert d["status"] == "ok" and d["manager_customer_id"] == "7954113535"
    assert d["hierarchy"]["managers"] == 1 and d["accessible_customers"]["count"] == 1
    # Meta no expone diagnóstico: no debe romper.
    assert client.get("/api/accounts/meta").json()["status"] == "not_configured"


def test_ga4_discovery_keeps_the_account_property_tree(setup):
    client, *_ = setup
    g = client.get("/api/accounts/ga4").json()
    assert g["status"] == "ok"
    # Todas las cuentas y todas sus propiedades, no solo la primera.
    assert [a["id"] for a in g["accounts"]] == ["333", "334", "555"]
    tree = {grp["account_name"]: [p["property_id"] for p in grp["properties"]] for grp in g["groups"]}
    assert tree == {"Cuenta Acme": ["333", "334"], "Cuenta Beta": ["555"]}
    first = g["accounts"][0]
    for field in ("account_id", "account_name", "property_id", "property_name", "property_resource"):
        assert first[field], field
    assert first["platform"] == "ga4"


def test_excluded_accounts_never_reach_the_api(setup):
    client, *_ = setup
    g = client.get("/api/accounts/ga4").json()
    body = client.get("/api/accounts/ga4").text
    assert "vidanta" not in body.lower()
    # Ni los nombres de lo excluido viajan al navegador: solo el conteo.
    assert g["excluded"] == {"count": 2}
    assert g["summary"]["total"] == 3


def test_excluded_clients_are_hidden_and_cannot_be_created(setup, tmp_path):
    client, *_ = setup
    r = client.post("/api/clients", json={"name": "Vidanta"})
    assert r.status_code == 400 and "exclusión" in r.json()["detail"]
    assert client.post("/api/clients", json={"name": "VidantaWorld Concert Series"}).status_code == 400
    # Un registro excluido que ya estuviera en el archivo tampoco se lista.
    store = json.loads((tmp_path / "clients.json").read_text())
    store["clients"].append({"id": "vidanta", "name": "Vidanta", "slug": "vidanta"})
    (tmp_path / "clients.json").write_text(json.dumps(store))
    names = [c["name"] for c in client.get("/api/clients").json()["clients"]]
    assert "Vidanta" not in names and "Acme" in names
    assert client.get("/api/clients/vidanta").status_code == 404
    # Tampoco se puede reintroducir renombrando un cliente existente.
    assert client.put("/api/clients/acme", json={"name": "Grupo Vidanta"}).status_code == 400
    assert client.get("/api/clients/acme").json()["name"] == "Acme"


RANGE = {"start_date": "2026-09-01", "end_date": "2026-09-24"}


def ga4_block(client, client_id="acme", **extra):
    return client.get(f"/api/dashboard/{client_id}", params={**RANGE, **extra}).json()["platforms"]["ga4"]


class TestGA4MultiProperty:
    """Varias propiedades GA4 por cliente: consolidado, por propiedad y fallos aislados."""

    def assign(self, client, ids, client_id="acme"):
        r = client.put(f"/api/clients/{client_id}", json={"ga4_property_ids": ids})
        assert r.status_code == 200, r.text
        return r.json()

    def test_registry_accepts_several_properties_and_keeps_the_legacy_field(self, setup):
        client, *_ = setup
        c = self.assign(client, ["properties/333", "334"])
        assert c["ga4_property_ids"] == ["333", "334"]
        # El campo antiguo sigue existiendo y apunta a la primera.
        assert c["ga4_property_id"] == "333"

    def test_legacy_single_field_still_works_and_migrates(self, setup):
        client, *_ = setup
        c = client.put("/api/clients/acme", json={"ga4_property_id": "555"}).json()
        assert c["ga4_property_id"] == "555" and c["ga4_property_ids"] == ["555"]

    def test_consolidated_totals_sum_and_recompute_engagement_rate(self, setup):
        client, *_ = setup
        self.assign(client, ["333", "334"])
        block = ga4_block(client)
        assert block["status"] == "ok"
        assert block["property_ids"] == ["333", "334"]
        t = block["current"]["totals"]
        # 333 → 300 sesiones / 120 engaged; 334 → 400 / 160
        assert (t["sessions"], t["engaged_sessions"]) == (700, 280)
        assert t["users"] == 70 and t["key_events"] == 7.0 and t["revenue"] == 700.0
        assert t["engagement_rate"] == 280 / 700
        assert block["current"]["properties_count"] == 2

    def test_each_property_is_also_available_on_its_own(self, setup):
        client, *_ = setup
        self.assign(client, ["333", "334"])
        props = ga4_block(client)["properties"]
        assert [p["property_id"] for p in props] == ["333", "334"]
        assert [p["property_name"] for p in props] == ["Acme Web", "Acme App"]
        assert props[0]["current"]["totals"]["sessions"] == 300
        assert props[1]["current"]["totals"]["sessions"] == 400
        assert all(p["status"] == "ok" for p in props)

    def test_series_and_tables_are_grouped_and_summed(self, setup):
        client, *_ = setup
        self.assign(client, ["333", "334"])
        cur = ga4_block(client)["current"]
        assert [d["date"] for d in cur["daily"]] == ["2026-09-01"]
        assert cur["daily"][0]["sessions"] == 700
        # Las dos propiedades traen "google / cpc": se agrupa en una sola fila.
        assert [r["name"] for r in cur["by_source_medium"]] == ["google / cpc"]
        assert cur["by_source_medium"][0]["sessions"] == 700

    def test_one_broken_property_never_breaks_the_rest(self, setup):
        client, _, providers = setup
        self.assign(client, ["333", "334", "555"])
        providers["ga4"].broken["334"] = ProviderError("cuota agotada")
        block = ga4_block(client, refresh="true")
        assert block["status"] == "ok"
        # Las otras dos siguen sumando (300 + 500 sesiones).
        assert block["current"]["totals"]["sessions"] == 800
        assert block["current"]["properties_count"] == 2
        # El aviso dice exactamente qué property_id falló.
        assert [w["property_id"] for w in block["warnings"]] == ["334"]
        assert "334" in block["warnings"][0]["message"]
        assert {p["property_id"]: p["status"] for p in block["properties"]} == {"333": "ok", "334": "error", "555": "ok"}

    def test_all_properties_broken_reports_an_error_block(self, setup):
        client, _, providers = setup
        self.assign(client, ["333", "334"])
        providers["ga4"].broken.update({"333": ProviderError("boom"), "334": ProviderError("boom")})
        block = ga4_block(client, refresh="true")
        assert block["status"] == "error" and block["current"] is None
        assert [w["property_id"] for w in block["warnings"]] == ["333", "334"]

    def test_comparison_uses_the_same_properties_for_both_periods(self, setup):
        client, _, providers = setup
        self.assign(client, ["333", "334"])
        block = ga4_block(client, comparison="previous_year", refresh="true")
        assert block["compare_range"] == {"start": "2025-09-01", "end": "2025-09-24"}
        assert block["previous"]["totals"]["sessions"] == 700
        assert block["properties"][0]["previous"]["totals"]["sessions"] == 300
        # Mismas propiedades en ambos periodos, ni una más.
        asked = {(pid, start) for pid, start, _ in providers["ga4"].calls}
        assert asked == {("333", "2026-09-01"), ("334", "2026-09-01"), ("333", "2025-09-01"), ("334", "2025-09-01")}

    def test_a_failing_comparison_period_never_hides_the_current_one(self, setup):
        """Si solo falla el periodo de comparación, los datos actuales se siguen viendo."""
        client, _, providers = setup
        self.assign(client, ["333", "334"])
        ga4 = providers["ga4"]
        original = ga4.performance

        def only_current_works(property_id, start, end):
            if start.startswith("2025"):  # el periodo de comparación falla
                raise ProviderError("sin datos históricos")
            return original(property_id, start, end)

        ga4.performance = only_current_works
        block = ga4_block(client, comparison="previous_year", refresh="true")
        assert block["status"] == "ok"
        # El periodo actual llega completo…
        assert block["current"]["totals"]["sessions"] == 700
        assert [p["status"] for p in block["properties"]] == ["ok", "ok"]
        # …y lo único que se pierde es la comparación, con su aviso por propiedad.
        assert block["previous"] is None
        compare = [w for w in block["warnings"] if w["status"] == "compare_error"]
        assert [w["property_id"] for w in compare] == ["333", "334"]

    def test_mixed_currencies_are_flagged_instead_of_silently_summed(self, setup):
        client, _, providers = setup
        self.assign(client, ["333", "334"])
        providers["ga4"].currency_of["334"] = "USD"
        block = ga4_block(client, refresh="true")
        assert block["status"] == "ok" and block["mixed_currency"] is True
        mixed = [w for w in block["warnings"] if w["status"] == "mixed_currency"]
        assert len(mixed) == 1 and "MXN" in mixed[0]["message"] and "USD" in mixed[0]["message"]

    def test_a_property_can_only_belong_to_one_client(self, setup):
        client, *_ = setup
        self.assign(client, ["333", "334"])
        r = client.put("/api/clients/beta-corp", json={"ga4_property_ids": ["334", "555"]})
        assert r.status_code == 409 and "334" in r.json()["detail"]
        # La asignación de acme no se tocó.
        assert client.get("/api/clients/acme").json()["ga4_property_ids"] == ["333", "334"]

    def test_discovery_marks_every_assigned_property(self, setup):
        client, *_ = setup
        self.assign(client, ["333", "334"])
        accounts = {a["id"]: a["assigned_to"] for a in client.get("/api/accounts/ga4").json()["accounts"]}
        assert accounts["333"] == {"id": "acme", "name": "Acme"}
        assert accounts["334"] == {"id": "acme", "name": "Acme"}
        assert accounts["555"] is None

    def test_unassigning_all_properties_returns_not_linked(self, setup):
        client, *_ = setup
        self.assign(client, ["333"])
        c = self.assign(client, [])
        assert c["ga4_property_ids"] == [] and c["ga4_property_id"] is None
        assert ga4_block(client)["status"] == "not_linked"


def test_health_never_exposes_secrets(setup, monkeypatch):
    client, *_ = setup
    monkeypatch.setenv("META_ACCESS_TOKEN", "EAAsupersecretvalue")
    body = client.get("/api/health").text
    assert "EAAsupersecretvalue" not in body and '"meta_ads":"configured"' in body

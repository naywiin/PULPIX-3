"""Asignación de campañas por cliente (Google Ads y Meta Ads).

Escenario real que motivó la arquitectura: un cliente ("Origen") es dueño de la
cuenta publicitaria, pero dentro conviven campañas que pertenecen lógicamente a
otro cliente del dashboard ("Destino"). Las campañas NO se mueven en la
plataforma: solo se declara a quién pertenecen aquí.

Los proveedores falsos imitan el contrato real: filtran por `campaign_ids` en la
propia consulta, igual que hacen GAQL y el `filtering` de la Graph API.
"""
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.providers.base import ProviderError
from app.registry import JsonClientRepository

# ---------------------------------------------------------------- catálogos

GADS_SHARED = "7000000001"   # cuenta de "Origen": aloja campañas de los dos
GADS_OWN = "7000000002"      # cuenta propia de "Destino"
META_SHARED = "8000000001"
META_OWN = "8000000002"

GADS_CAMPAIGNS = {
    GADS_SHARED: [("101", "Origen Search"), ("102", "Origen PMax"),
                  ("201", "Destino Search"), ("202", "Destino Performance Max")],
    GADS_OWN: [("301", "Destino Brand")],
}
META_CAMPAIGNS = {
    META_SHARED: [("501", "Origen Awareness"), ("601", "Destino Leads"), ("602", "Destino Remarketing")],
    META_OWN: [("701", "Destino Prospecting")],
}
#: campaign_id → [(adset_id, nombre)]. Dos ad sets por campaña.
META_ADSETS = {cid: [(f"{cid}1", f"{name} · AS1"), (f"{cid}2", f"{name} · AS2")]
               for rows in META_CAMPAIGNS.values() for cid, name in rows}

#: Métricas base por campaña. Números elegidos para que las sumas se lean claro.
METRICS = {
    "101": dict(impressions=1000, clicks=100, spend=500.0, conversions=10.0, conversion_value=2000.0),
    "102": dict(impressions=2000, clicks=100, spend=300.0, conversions=5.0, conversion_value=1500.0),
    "201": dict(impressions=4000, clicks=200, spend=800.0, conversions=20.0, conversion_value=4000.0),
    "202": dict(impressions=3000, clicks=150, spend=200.0, conversions=8.0, conversion_value=1000.0),
    "301": dict(impressions=500, clicks=50, spend=100.0, conversions=4.0, conversion_value=600.0),
    "501": dict(impressions=9000, clicks=300, spend=900.0, conversions=30.0, conversion_value=3000.0),
    "601": dict(impressions=6000, clicks=240, spend=600.0, conversions=24.0, conversion_value=2400.0),
    "602": dict(impressions=2000, clicks=60, spend=400.0, conversions=6.0, conversion_value=1200.0),
    "701": dict(impressions=1000, clicks=40, spend=100.0, conversions=4.0, conversion_value=500.0),
}


def _select(catalog, account_id, campaign_ids):
    """Lo que devolvería la API ya filtrada por campaña."""
    rows = catalog[account_id]
    if campaign_ids:
        wanted = set(campaign_ids)
        rows = [r for r in rows if r[0] in wanted]
    return rows


def _adset_metrics(adset_id):
    """Cada ad set aporta la mitad de su campaña: 2 ad sets = la campaña entera."""
    return {k: v / 2 for k, v in METRICS[adset_id[:-1]].items()}


def _sum(ids):
    out = dict(impressions=0, clicks=0, spend=0.0, conversions=0.0, conversion_value=0.0)
    for cid in ids:
        for k, v in METRICS[cid].items():
            out[k] += v
    return out


class FakeGoogleAds:
    def __init__(self):
        self.calls: list[tuple] = []
        self.broken: set[str] = set()

    def list_accounts(self):
        return [{"platform": "google_ads", "id": a, "name": f"Cuenta {a}", "currency": "MXN",
                 "timezone": "America/Mexico_City", "status": "ENABLED", "account_type": "client",
                 "level": 1, "login_customer_id": "7954113535"} for a in GADS_CAMPAIGNS]

    def list_campaigns(self, customer_id, login_customer_id=None):
        return [{"platform": "google_ads", "customer_id": customer_id, "account_name": f"Cuenta {customer_id}",
                 "currency": "MXN", "campaign_id": cid, "campaign_name": name, "status": "ENABLED",
                 "advertising_channel_type": "SEARCH"} for cid, name in GADS_CAMPAIGNS[customer_id]]

    def performance(self, customer_id, login, start, end, campaign_ids=None):
        self.calls.append((customer_id, start, tuple(sorted(campaign_ids or ()))))
        if customer_id in self.broken:
            raise ProviderError(f"cuota agotada en {customer_id}")
        rows = _select(GADS_CAMPAIGNS, customer_id, campaign_ids)
        ids = [cid for cid, _ in rows]
        campaigns = [{"campaign_id": cid, "campaign_name": name, "status": "ENABLED",
                      "channel": "SEARCH", **METRICS[cid]} for cid, name in rows]
        half = {k: v / 2 for k, v in _sum(ids).items()}
        return {"currency": "MXN", "account_name": f"Cuenta {customer_id}", "totals": _sum(ids),
                "campaigns": campaigns, "daily": [{"date": start, **half}, {"date": end, **half}]}


class FakeMeta:
    def __init__(self):
        self.calls: list[tuple] = []
        self.adset_calls: list[tuple] = []
        self.broken: set[str] = set()

    def list_accounts(self):
        return [{"platform": "meta_ads", "id": a, "name": f"Meta {a}", "currency": "MXN",
                 "timezone": "America/Mexico_City", "status": "ACTIVE", "account_type": "ad_account",
                 "business": "MarketingVidanta"} for a in META_CAMPAIGNS]

    def list_campaigns(self, account_id):
        return [{"platform": "meta_ads", "ad_account_id": account_id, "account_name": f"Meta {account_id}",
                 "campaign_id": cid, "campaign_name": name, "status": "ACTIVE",
                 "effective_status": "ACTIVE", "objective": "LEADS"} for cid, name in META_CAMPAIGNS[account_id]]

    def performance(self, account_id, start, end, campaign_ids=None):
        self.calls.append((account_id, start, tuple(sorted(campaign_ids or ())), ()))
        if account_id in self.broken:
            raise ProviderError(f"cuota agotada en {account_id}")
        rows = _select(META_CAMPAIGNS, account_id, campaign_ids)
        ids = [cid for cid, _ in rows]
        campaigns = [{"campaign_id": cid, "campaign_name": name, **METRICS[cid]} for cid, name in rows]
        half = {k: v / 2 for k, v in _sum(ids).items()}
        return {"currency": "MXN", "account_name": f"Meta {account_id}", "totals": _sum(ids),
                "campaigns": campaigns, "daily": [{"date": start, **half}, {"date": end, **half}]}

    def adsets(self, account_id, start, end, campaign_ids=None, adset_ids=None):
        """El filtro de campañas y de ad sets DEBE propagarse hasta aquí."""
        self.adset_calls.append((account_id, start, tuple(sorted(campaign_ids or ())), tuple(sorted(adset_ids or ()))))
        rows = _select(META_CAMPAIGNS, account_id, campaign_ids)
        out = []
        for cid, name in rows:
            for aid, aname in META_ADSETS[cid]:
                if adset_ids and aid not in set(adset_ids):
                    continue
                out.append({"campaign_id": cid, "campaign_name": name, "ad_set_id": aid,
                            "ad_set_name": aname, **_adset_metrics(aid)})
        return out

    def list_adsets(self, account_id, campaign_id):
        name = next((n for c, n in META_CAMPAIGNS[account_id] if c == campaign_id), None)
        return [{"platform": "meta_ads", "ad_account_id": account_id, "account_name": f"Meta {account_id}",
                 "campaign_id": campaign_id, "campaign_name": name, "adset_id": aid, "adset_name": aname,
                 "status": "ACTIVE", "effective_status": "ACTIVE",
                 "optimization_goal": "OFFSITE_CONVERSIONS", "billing_event": "IMPRESSIONS"}
                for aid, aname in META_ADSETS.get(campaign_id, [])]

    def performance_adsets(self, account_id, start, end, adset_ids):
        """Solo esos ad sets: totales, campañas y serie diaria salen de ellos."""
        self.calls.append((account_id, start, (), tuple(sorted(adset_ids))))
        if account_id in self.broken:
            raise ProviderError(f"cuota agotada en {account_id}")
        wanted = set(adset_ids)
        rows, by_campaign = [], {}
        for cid, name in META_CAMPAIGNS[account_id]:
            for aid, aname in META_ADSETS[cid]:
                if aid not in wanted:
                    continue
                m = _adset_metrics(aid)
                rows.append({"campaign_id": cid, "campaign_name": name, "ad_set_id": aid,
                             "ad_set_name": aname, **m})
                acc = by_campaign.setdefault(cid, {"campaign_id": cid, "campaign_name": name,
                                                   **{k: 0.0 for k in m}})
                for k, v in m.items():
                    acc[k] += v
        totals = {k: 0.0 for k in ("impressions", "clicks", "spend", "conversions", "conversion_value")}
        for r in rows:
            for k in totals:
                totals[k] += r[k]
        half = {k: v / 2 for k, v in totals.items()}
        return {"currency": "MXN", "account_name": f"Meta {account_id}", "totals": totals,
                "campaigns": list(by_campaign.values()), "adsets": rows,
                "daily": [{"date": start, **half}, {"date": end, **half}],
                "reach_is_sum": True}


class DeadGA4:
    def list_accounts(self):
        return []

    def performance(self, *a, **kw):
        raise ProviderError("GA4 fuera de esta prueba")


@pytest.fixture
def api(tmp_path: Path):
    settings = Settings(clients_file=tmp_path / "clients.json", static_dir=tmp_path / "nodist")
    gads, meta = FakeGoogleAds(), FakeMeta()
    app = create_app(settings, JsonClientRepository(settings.clients_file),
                     {"google_ads": gads, "meta_ads": meta, "ga4": DeadGA4()})
    c = TestClient(app)
    # Origen es dueño de las cuentas compartidas; Destino tiene las suyas.
    c.post("/api/clients", json={"name": "Origen", "google_ads_customer_id": GADS_SHARED,
                                 "meta_ad_account_id": META_SHARED})
    c.post("/api/clients", json={"name": "Destino", "google_ads_customer_id": GADS_OWN,
                                 "meta_ad_account_id": META_OWN})
    return c, gads, meta


RANGE = {"start_date": "2026-09-01", "end_date": "2026-09-24"}


def block(c, client_id, platform="google_ads", **extra):
    return c.get(f"/api/dashboard/{client_id}", params={**RANGE, **extra}).json()["platforms"][platform]


def campaign_names(blk):
    return sorted(c["campaign_name"] for c in blk["current"]["campaigns"])


def add_source(c, client_id, platform, account_id, campaign_ids, allocation="exclusive", keep_primary=True):
    """Añade una fuente conservando la cuenta principal del cliente."""
    field = "google_ads_sources" if platform == "google_ads" else "meta_ads_sources"
    key = "customer_id" if platform == "google_ads" else "ad_account_id"
    current = c.get(f"/api/clients/{client_id}").json()[field] if keep_primary else []
    body = {field: [*current, {key: account_id, "campaign_ids": campaign_ids, "allocation": allocation}]}
    return c.put(f"/api/clients/{client_id}", json=body)


# ============================================================ GOOGLE ADS

class TestGoogleAdsAssignment:
    def test_1_campaign_living_in_another_account_can_be_assigned(self, api):
        c, *_ = api
        r = add_source(c, "destino", "google_ads", GADS_SHARED, ["201", "202"])
        assert r.status_code == 200, r.text
        sources = r.json()["google_ads_sources"]
        assert [s["customer_id"] for s in sources] == [GADS_OWN, GADS_SHARED]
        assert sources[1]["campaign_ids"] == ["201", "202"]
        assert sources[1]["allocation"] == "exclusive"

    def test_2_exclusive_moves_the_campaign_from_owner_to_target(self, api):
        c, *_ = api
        assert campaign_names(block(c, "origen")) == ["Destino Performance Max", "Destino Search",
                                                      "Origen PMax", "Origen Search"]
        add_source(c, "destino", "google_ads", GADS_SHARED, ["201", "202"])
        # El destino las recibe…
        assert campaign_names(block(c, "destino")) == ["Destino Brand", "Destino Performance Max", "Destino Search"]
        # …y el dueño de la cuenta deja de contabilizarlas (sin doble conteo).
        assert campaign_names(block(c, "origen")) == ["Origen PMax", "Origen Search"]

    def test_2b_exclusive_totals_move_too(self, api):
        c, *_ = api
        add_source(c, "destino", "google_ads", GADS_SHARED, ["201", "202"])
        origen = block(c, "origen")["current"]["totals"]
        destino = block(c, "destino")["current"]["totals"]
        assert origen["spend"] == 800.0            # 500 + 300, sin las de Destino
        assert destino["spend"] == 1100.0          # 800 + 200 + 100 (su cuenta)
        assert origen["impressions"] == 3000 and destino["impressions"] == 7500

    def test_3_shared_lets_both_clients_see_it(self, api):
        c, *_ = api
        add_source(c, "destino", "google_ads", GADS_SHARED, ["201"], allocation="shared")
        assert "Destino Search" in campaign_names(block(c, "destino"))
        # Compartida: el dueño de la cuenta la sigue viendo.
        assert "Destino Search" in campaign_names(block(c, "origen"))

    def test_4_several_campaigns_from_one_account(self, api):
        c, gads, _ = api
        add_source(c, "destino", "google_ads", GADS_SHARED, ["201", "202"])
        blk = block(c, "destino")
        assert len([x for x in blk["current"]["campaigns"] if x["source_account_id"] == GADS_SHARED]) == 2
        asked = [call for call in gads.calls if call[0] == GADS_SHARED]
        assert all(call[2] == ("201", "202") for call in asked), asked

    def test_5_sources_from_several_accounts(self, api):
        c, *_ = api
        add_source(c, "destino", "google_ads", GADS_SHARED, ["201"])
        blk = block(c, "destino")
        assert {s["account_id"] for s in blk["sources"]} == {GADS_OWN, GADS_SHARED}
        assert blk["current"]["sources_count"] == 2
        # 100 (301) + 800 (201)
        assert blk["current"]["totals"]["spend"] == 900.0

    def test_6_empty_campaign_ids_means_every_campaign(self, api):
        c, gads, _ = api
        blk = block(c, "origen")
        assert len(blk["current"]["campaigns"]) == 4
        assert all(call[2] == () for call in gads.calls if call[0] == GADS_SHARED)

    def test_7_empty_campaign_ids_still_excludes_what_was_given_away(self, api):
        c, gads, _ = api
        add_source(c, "destino", "google_ads", GADS_SHARED, ["201", "202"])
        blk = block(c, "origen", refresh="true")
        assert campaign_names(blk) == ["Origen PMax", "Origen Search"]
        # El filtro viaja a la API: se piden solo las que le quedan.
        asked = [call for call in gads.calls if call[0] == GADS_SHARED and call[2]]
        assert ("101", "102") in [call[2] for call in asked]

    def test_8_legacy_customer_id_still_works(self, api):
        c, *_ = api
        legacy = c.post("/api/clients", json={"name": "Heredado", "google_ads_customer_id": "9990000999"}).json()
        assert legacy["google_ads_sources"] == [
            {"customer_id": "9990000999", "login_customer_id": None, "campaign_ids": [], "allocation": "exclusive"}
        ]
        assert legacy["google_ads_customer_id"] == "9990000999"

    def test_9_identity_is_account_plus_campaign(self, api):
        """El mismo campaign_id en dos cuentas NO es la misma campaña."""
        c, *_ = api
        add_source(c, "destino", "google_ads", GADS_SHARED, ["201"])
        rows = block(c, "destino")["current"]["campaigns"]
        ident = [(r["source_account_id"], r["campaign_id"]) for r in rows]
        assert len(ident) == len(set(ident))
        assert (GADS_SHARED, "201") in ident and (GADS_OWN, "301") in ident

    def test_10_two_exclusive_claims_are_rejected(self, api):
        c, *_ = api
        add_source(c, "destino", "google_ads", GADS_SHARED, ["201"])
        # El tercero tiene SU cuenta principal; pide la campaña como fuente extra.
        c.post("/api/clients", json={"name": "Tercero", "google_ads_customer_id": "7000000003"})
        r = add_source(c, "tercero", "google_ads", GADS_SHARED, ["201"])
        assert r.status_code == 409
        detail = r.json()["detail"]
        assert "201" in detail and "Destino" in detail
        # Compartida sí puede convivir con la exclusiva.
        assert add_source(c, "tercero", "google_ads", GADS_SHARED, ["201"],
                          allocation="shared").status_code == 200

    def test_11_comparison_uses_the_same_campaigns(self, api):
        c, gads, _ = api
        add_source(c, "destino", "google_ads", GADS_SHARED, ["201", "202"])
        for comparison in ("previous_period", "previous_month", "previous_year"):
            gads.calls.clear()
            blk = block(c, "destino", comparison=comparison, refresh="true")
            assert blk["previous"] is not None, comparison
            sets = {call[2] for call in gads.calls if call[0] == GADS_SHARED}
            assert sets == {("201", "202")}, (comparison, sets)
            # Mismo universo ⇒ mismos totales en ambos periodos.
            assert blk["current"]["totals"]["spend"] == blk["previous"]["totals"]["spend"]

    def test_12_overview_respects_the_assignment(self, api):
        c, *_ = api
        add_source(c, "destino", "google_ads", GADS_SHARED, ["201", "202"])
        origen = c.get("/api/dashboard/origen", params=RANGE).json()["platforms"]["google_ads"]
        destino = c.get("/api/dashboard/destino", params=RANGE).json()["platforms"]["google_ads"]
        # Lo que el Overview suma es exactamente lo del bloque, sin duplicar.
        assert origen["current"]["totals"]["spend"] == 800.0
        assert destino["current"]["totals"]["spend"] == 1100.0
        assert origen["current"]["totals"]["spend"] + destino["current"]["totals"]["spend"] == 1900.0

    def test_derived_metrics_come_from_the_summed_totals(self, api):
        c, *_ = api
        add_source(c, "destino", "google_ads", GADS_SHARED, ["201"])
        t = block(c, "destino")["current"]["totals"]
        # 301 + 201: clicks 250, impressions 4500, spend 900, value 4600, conv 24
        assert t["ctr"] == 250 / 4500 and t["cpc"] == 900.0 / 250
        assert t["roas"] == 4600.0 / 900.0 and t["cpa"] == 900.0 / 24
        # Promediar los ratios de las dos cuentas daría otra cosa.
        assert t["ctr"] != ((50 / 500) + (200 / 4000)) / 2

    def test_daily_series_merge_by_date(self, api):
        c, *_ = api
        add_source(c, "destino", "google_ads", GADS_SHARED, ["201"])
        daily = block(c, "destino")["current"]["daily"]
        assert [d["date"] for d in daily] == ["2026-09-01", "2026-09-24"]
        assert daily[0]["spend"] == (100.0 + 800.0) / 2   # las dos cuentas, mismo día

    def test_campaign_rows_keep_their_source_account(self, api):
        c, *_ = api
        add_source(c, "destino", "google_ads", GADS_SHARED, ["201"])
        for row in block(c, "destino")["current"]["campaigns"]:
            assert row["source_account_id"] in (GADS_OWN, GADS_SHARED)
            assert row["source_account_name"]

    def test_a_failing_source_does_not_hide_the_others(self, api):
        c, gads, _ = api
        add_source(c, "destino", "google_ads", GADS_SHARED, ["201"])
        gads.broken.add(GADS_SHARED)
        blk = block(c, "destino", refresh="true")
        assert blk["status"] == "ok"
        assert blk["current"]["totals"]["spend"] == 100.0          # solo su cuenta
        partial = [w for w in blk["warnings"] if w["status"] == "partial"]
        assert partial and "1 de 2" in partial[0]["message"]
        assert any(w["account_id"] == GADS_SHARED for w in blk["warnings"])

    def test_campaign_catalog_endpoint(self, api):
        c, *_ = api
        r = c.get(f"/api/accounts/google-ads/{GADS_SHARED}/campaigns").json()
        assert r["status"] == "ok" and r["count"] == 4
        first = r["campaigns"][0]
        for field in ("customer_id", "account_name", "campaign_id", "campaign_name", "status",
                      "advertising_channel_type", "currency"):
            assert field in first, field
        add_source(c, "destino", "google_ads", GADS_SHARED, ["201"])
        r2 = c.get(f"/api/accounts/google-ads/{GADS_SHARED}/campaigns").json()
        taken = {x["campaign_id"]: x["assigned_to"] for x in r2["campaigns"]}
        assert taken["201"] == {"id": "destino", "name": "Destino", "allocation": "exclusive"}
        assert taken["101"] is None


# ================================================================== META

class TestMetaAssignment:
    def test_13_meta_campaign_from_another_account_can_be_assigned(self, api):
        c, *_ = api
        r = add_source(c, "destino", "meta_ads", META_SHARED, ["601", "602"])
        assert r.status_code == 200, r.text
        assert [s["ad_account_id"] for s in r.json()["meta_ads_sources"]] == [META_OWN, META_SHARED]

    def test_14_exclusive_avoids_double_counting(self, api):
        c, *_ = api
        add_source(c, "destino", "meta_ads", META_SHARED, ["601", "602"])
        assert campaign_names(block(c, "origen", "meta_ads")) == ["Origen Awareness"]
        assert campaign_names(block(c, "destino", "meta_ads")) == ["Destino Leads", "Destino Prospecting",
                                                                  "Destino Remarketing"]
        assert block(c, "origen", "meta_ads")["current"]["totals"]["spend"] == 900.0
        assert block(c, "destino", "meta_ads")["current"]["totals"]["spend"] == 1100.0

    def test_15_shared_works_in_both(self, api):
        c, *_ = api
        add_source(c, "destino", "meta_ads", META_SHARED, ["601"], allocation="shared")
        assert "Destino Leads" in campaign_names(block(c, "destino", "meta_ads"))
        assert "Destino Leads" in campaign_names(block(c, "origen", "meta_ads"))

    def test_16_several_campaigns_of_one_account(self, api):
        c, _, meta = api
        add_source(c, "destino", "meta_ads", META_SHARED, ["601", "602"])
        blk = block(c, "destino", "meta_ads")
        assert len([x for x in blk["current"]["campaigns"] if x["source_account_id"] == META_SHARED]) == 2
        assert all(call[2] == ("601", "602") for call in meta.calls if call[0] == META_SHARED)

    def test_17_sources_from_several_meta_accounts(self, api):
        c, *_ = api
        add_source(c, "destino", "meta_ads", META_SHARED, ["601"])
        blk = block(c, "destino", "meta_ads")
        assert {s["account_id"] for s in blk["sources"]} == {META_OWN, META_SHARED}
        assert blk["current"]["totals"]["spend"] == 700.0    # 100 + 600

    def test_18_empty_campaign_ids_works(self, api):
        c, *_ = api
        assert campaign_names(block(c, "origen", "meta_ads")) == ["Destino Leads", "Destino Remarketing",
                                                                  "Origen Awareness"]

    def test_19_legacy_meta_ad_account_id_still_works(self, api):
        c, *_ = api
        legacy = c.post("/api/clients", json={"name": "Heredado Meta", "meta_ad_account_id": "act_555000555"}).json()
        assert legacy["meta_ads_sources"] == [
            {"ad_account_id": "555000555", "campaign_ids": [], "allocation": "exclusive",
             "adset_selections": []}
        ]
        assert legacy["meta_ad_account_id"] == "555000555"

    def test_20_identity_is_ad_account_plus_campaign(self, api):
        c, *_ = api
        add_source(c, "destino", "meta_ads", META_SHARED, ["601"])
        rows = block(c, "destino", "meta_ads")["current"]["campaigns"]
        ident = [(r["source_account_id"], r["campaign_id"]) for r in rows]
        assert len(ident) == len(set(ident))

    def test_21_adsets_are_filtered_by_the_selected_campaigns(self, api):
        c, _, meta = api
        add_source(c, "destino", "meta_ads", META_SHARED, ["601"])
        blk = block(c, "destino", "meta_ads")
        adsets = blk["current"]["adsets"]
        # Solo ad sets de la campaña recibida y de su propia cuenta.
        assert {a["campaign_id"] for a in adsets} == {"601", "701"}
        assert "501" not in {a["campaign_id"] for a in adsets}
        # El filtro se pidió a la API, no se recortó en memoria.
        assert ("601",) in [call[2] for call in meta.adset_calls if call[0] == META_SHARED]

    def test_22_ads_level_inherits_the_same_campaign_filter(self, api):
        """El filtro de campañas es el mismo objeto que se pasa a adsets/ads."""
        c, _, meta = api
        add_source(c, "destino", "meta_ads", META_SHARED, ["601", "602"])
        block(c, "destino", "meta_ads", refresh="true")
        perf = {call[2] for call in meta.calls if call[0] == META_SHARED}
        ads = {call[2] for call in meta.adset_calls if call[0] == META_SHARED}
        assert perf == ads == {("601", "602")}

    def test_23_comparisons_keep_the_campaign_ids(self, api):
        c, _, meta = api
        add_source(c, "destino", "meta_ads", META_SHARED, ["601"])
        for comparison in ("previous_period", "previous_year"):
            meta.calls.clear()
            blk = block(c, "destino", "meta_ads", comparison=comparison, refresh="true")
            assert blk["previous"] is not None
            assert {call[2] for call in meta.calls if call[0] == META_SHARED} == {("601",)}

    def test_24_overview_respects_the_meta_assignment(self, api):
        c, *_ = api
        add_source(c, "destino", "meta_ads", META_SHARED, ["601", "602"])
        dash_o = c.get("/api/dashboard/origen", params=RANGE).json()["platforms"]["meta_ads"]
        dash_d = c.get("/api/dashboard/destino", params=RANGE).json()["platforms"]["meta_ads"]
        assert dash_o["current"]["totals"]["spend"] == 900.0
        assert dash_d["current"]["totals"]["spend"] == 1100.0

    def test_meta_campaign_catalog_endpoint(self, api):
        c, *_ = api
        r = c.get(f"/api/accounts/meta/{META_SHARED}/campaigns").json()
        assert r["status"] == "ok" and r["count"] == 3
        for field in ("ad_account_id", "account_name", "campaign_id", "campaign_name", "status", "effective_status"):
            assert field in r["campaigns"][0], field


# ========================================================== validaciones

class TestAssignmentValidation:
    def test_campaign_ids_must_be_numeric(self, api):
        c, *_ = api
        r = c.put("/api/clients/destino", json={"google_ads_sources": [
            {"customer_id": GADS_SHARED, "campaign_ids": ["no-es-un-id"]}]})
        assert r.status_code == 422

    def test_duplicate_campaign_ids_are_collapsed(self, api):
        c, *_ = api
        r = c.put("/api/clients/destino", json={"google_ads_sources": [
            {"customer_id": GADS_OWN, "campaign_ids": []},
            {"customer_id": GADS_SHARED, "campaign_ids": ["201", "201", "202"]}]})
        assert r.status_code == 200, r.text
        assert r.json()["google_ads_sources"][1]["campaign_ids"] == ["201", "202"]

    def test_the_same_account_cannot_be_two_sources_of_one_client(self, api):
        c, *_ = api
        r = c.put("/api/clients/destino", json={"google_ads_sources": [
            {"customer_id": GADS_SHARED, "campaign_ids": ["201"]},
            {"customer_id": GADS_SHARED, "campaign_ids": ["202"]}]})
        assert r.status_code == 422

    def test_a_primary_account_still_belongs_to_one_client_only(self, api):
        c, *_ = api
        r = c.put("/api/clients/destino", json={"google_ads_customer_id": GADS_SHARED})
        assert r.status_code == 409 and "Origen" in r.json()["detail"]

    def test_limits_on_sources_and_campaigns(self, api):
        c, *_ = api
        many = [{"customer_id": str(9000000000 + i), "campaign_ids": []} for i in range(11)]
        assert c.put("/api/clients/destino", json={"google_ads_sources": many}).status_code == 422
        huge = [{"customer_id": GADS_SHARED, "campaign_ids": [str(i) for i in range(1000, 1300)]}]
        assert c.put("/api/clients/destino", json={"google_ads_sources": huge}).status_code == 422

    def test_changing_the_assignment_does_not_reuse_cached_data(self, api):
        c, gads, _ = api
        first = block(c, "destino", "google_ads")["current"]["totals"]["spend"]
        add_source(c, "destino", "google_ads", GADS_SHARED, ["201"])
        second = block(c, "destino", "google_ads")["current"]["totals"]["spend"]
        assert first == 100.0 and second == 900.0   # sin refresh: la clave de cache cambió

    def test_removing_a_source_returns_the_campaigns_to_the_owner(self, api):
        c, *_ = api
        add_source(c, "destino", "google_ads", GADS_SHARED, ["201", "202"])
        assert campaign_names(block(c, "origen")) == ["Origen PMax", "Origen Search"]
        current = c.get("/api/clients/destino").json()["google_ads_sources"]
        c.put("/api/clients/destino", json={"google_ads_sources": [current[0]]})
        assert len(campaign_names(block(c, "origen"))) == 4


# ===================================================== regresiones vivas

class TestRegressions:
    """Lo que ya se rompió una vez y no debe volver a romperse."""

    def _discovery(self, api):
        c, _, meta = api
        meta.list_accounts = lambda: [
            {"platform": "meta_ads", "id": "2048742145193546", "name": "Cirque du Soleil JOYÀ",
             "business": "MarketingVidanta", "currency": "MXN", "timezone": "America/Mexico_City",
             "status": "ACTIVE", "account_type": "ad_account"},
            {"platform": "meta_ads", "id": "802869966755765", "name": "Jungala",
             "business": "MarketingVidanta", "currency": "MXN", "timezone": "America/Mexico_City",
             "status": "ACTIVE", "account_type": "ad_account"},
            {"platform": "meta_ads", "id": "694368622413037", "name": "Vidanta Ads",
             "business": "MarketingVidanta", "currency": "MXN", "timezone": "America/Mexico_City",
             "status": "ACTIVE", "account_type": "ad_account"},
            {"platform": "meta_ads", "id": "781558888969462", "name": "Vida Vacations MX",
             "business": "Vida Vacations México", "currency": "MXN", "timezone": "America/Mexico_City",
             "status": "ACTIVE", "account_type": "ad_account"},
            {"platform": "meta_ads", "id": "585352203499122", "name": "The View Cuenta publicitaria",
             "business": "THE VIEW", "currency": "MXN", "timezone": "America/Mexico_City",
             "status": "UNSETTLED", "account_type": "ad_account"},
            {"platform": "meta_ads", "id": "348393051561641", "name": "VW Concert Series",
             "business": "VidantaWorld Concert Series", "currency": "MXN", "timezone": "America/Mexico_City",
             "status": "ACTIVE", "account_type": "ad_account"},
        ]
        return {a["id"]: a["name"] for a in c.get("/api/accounts/meta").json()["accounts"]}

    def test_25_joya_is_visible_even_under_the_vidanta_business(self, api):
        assert "2048742145193546" in self._discovery(api)

    def test_26_jungala_is_visible_even_when_managed_by_marketingvidanta(self, api):
        assert "802869966755765" in self._discovery(api)

    def test_27_vidanta_is_still_excluded(self, api):
        assert "694368622413037" not in self._discovery(api)

    def test_28_vida_vacations_is_still_excluded(self, api):
        assert "781558888969462" not in self._discovery(api)

    def test_29_the_view_is_still_excluded(self, api):
        assert "585352203499122" not in self._discovery(api)

    def test_vw_concert_series_stays_visible_for_now(self, api):
        """Decisión explícita del usuario: no se excluye por ahora."""
        assert "348393051561641" in self._discovery(api)

    def test_30_ga4_multi_property_is_untouched_by_the_new_architecture(self, api):
        """GA4 no participa en la asignación de campañas: sigue con sus propiedades."""
        c, *_ = api
        r = c.put("/api/clients/destino", json={"ga4_property_ids": ["111", "222"]})
        assert r.status_code == 200
        body = r.json()
        assert body["ga4_property_ids"] == ["111", "222"]
        assert body["ga4_property_id"] == "111"
        # Y no aparecen conceptos de campañas donde no tocan.
        assert "campaign_ids" not in body


class TestClientWithoutItsOwnAccount:
    """Caso real: un cliente SIN cuenta propia cuyas campañas viven en la de otro."""

    def test_can_take_campaigns_from_another_clients_account(self, api):
        c, *_ = api
        c.post("/api/clients", json={"name": "Sin Cuenta"})
        r = c.put("/api/clients/sin-cuenta", json={"google_ads_sources": [
            {"customer_id": GADS_SHARED, "campaign_ids": ["201", "202"], "allocation": "exclusive"}]})
        assert r.status_code == 200, r.text
        assert campaign_names(block(c, "sin-cuenta")) == ["Destino Performance Max", "Destino Search"]
        # Y el dueño de la cuenta deja de contabilizarlas.
        assert campaign_names(block(c, "origen")) == ["Origen PMax", "Origen Search"]

    def test_taking_the_whole_account_of_another_client_is_rejected(self, api):
        c, *_ = api
        c.post("/api/clients", json={"name": "Sin Cuenta"})
        r = c.put("/api/clients/sin-cuenta", json={"google_ads_sources": [
            {"customer_id": GADS_SHARED, "campaign_ids": [], "allocation": "exclusive"}]})
        assert r.status_code == 409
        assert "Origen" in r.json()["detail"] and "campañas concretas" in r.json()["detail"]

    def test_two_clients_can_split_one_account_by_campaigns(self, api):
        c, *_ = api
        c.post("/api/clients", json={"name": "Uno"})
        c.post("/api/clients", json={"name": "Dos"})
        assert c.put("/api/clients/uno", json={"google_ads_sources": [
            {"customer_id": GADS_SHARED, "campaign_ids": ["201"]}]}).status_code == 200
        assert c.put("/api/clients/dos", json={"google_ads_sources": [
            {"customer_id": GADS_SHARED, "campaign_ids": ["202"]}]}).status_code == 200
        assert campaign_names(block(c, "uno")) == ["Destino Search"]
        assert campaign_names(block(c, "dos")) == ["Destino Performance Max"]
        assert campaign_names(block(c, "origen")) == ["Origen PMax", "Origen Search"]


class TestPrimaryAccountMirror:
    """La cuenta principal solo existe si alguna fuente toma la cuenta ENTERA."""

    def test_partial_source_does_not_become_the_primary_account(self, api):
        c, *_ = api
        c.post("/api/clients", json={"name": "Sin Cuenta"})
        body = c.put("/api/clients/sin-cuenta", json={"google_ads_sources": [
            {"customer_id": GADS_SHARED, "campaign_ids": ["201", "202"]}]}).json()
        # Consume campañas de la cuenta de otro: NO es su cuenta principal.
        assert body["google_ads_customer_id"] is None
        assert body["google_ads_login_customer_id"] is None
        # Pero la fuente sí queda guardada y sirve datos.
        assert body["google_ads_sources"][0]["campaign_ids"] == ["201", "202"]
        assert campaign_names(block(c, "sin-cuenta")) == ["Destino Performance Max", "Destino Search"]

    def test_same_for_meta(self, api):
        c, *_ = api
        c.post("/api/clients", json={"name": "Sin Meta"})
        body = c.put("/api/clients/sin-meta", json={"meta_ads_sources": [
            {"ad_account_id": META_SHARED, "campaign_ids": ["601"]}]}).json()
        assert body["meta_ad_account_id"] is None
        assert body["meta_ads_sources"][0]["campaign_ids"] == ["601"]
        assert body["meta_ads_sources"][0]["adset_selections"] == []

    def test_a_whole_account_source_is_the_primary_even_if_not_first(self, api):
        c, *_ = api
        c.post("/api/clients", json={"name": "Mixto"})
        body = c.put("/api/clients/mixto", json={"google_ads_sources": [
            {"customer_id": GADS_SHARED, "campaign_ids": ["201"]},
            {"customer_id": "7000000009", "campaign_ids": []}]}).json()
        assert body["google_ads_customer_id"] == "7000000009"

    def test_legacy_clients_keep_their_primary_account(self, api):
        c, *_ = api
        body = c.get("/api/clients/origen").json()
        assert body["google_ads_customer_id"] == GADS_SHARED
        assert body["meta_ad_account_id"] == META_SHARED

    def test_setting_the_legacy_field_claims_the_whole_account(self, api):
        c, *_ = api
        c.post("/api/clients", json={"name": "Nuevo"})
        body = c.put("/api/clients/nuevo", json={"google_ads_customer_id": "7000000008"}).json()
        assert body["google_ads_customer_id"] == "7000000008"
        assert body["google_ads_sources"] == [
            {"customer_id": "7000000008", "login_customer_id": None, "campaign_ids": [], "allocation": "exclusive"}
        ]

    def test_partial_sources_survive_changing_the_primary_account(self, api):
        c, *_ = api
        add_source(c, "destino", "google_ads", GADS_SHARED, ["201"])
        body = c.put("/api/clients/destino", json={"google_ads_customer_id": "7000000007"}).json()
        assert body["google_ads_customer_id"] == "7000000007"
        parcial = [s for s in body["google_ads_sources"] if s["campaign_ids"]]
        assert parcial == [{"customer_id": GADS_SHARED, "login_customer_id": None,
                            "campaign_ids": ["201"], "allocation": "exclusive"}]

    def test_discovery_separates_owner_from_campaign_clients(self, api):
        c, *_ = api
        add_source(c, "destino", "google_ads", GADS_SHARED, ["201"])
        row = next(a for a in c.get("/api/accounts/google-ads").json()["accounts"] if a["id"] == GADS_SHARED)
        # La cuenta sigue siendo de Origen, que la consume entera…
        assert row["assigned_to"] == {"id": "origen", "name": "Origen"}
        # …y aparte se ve quién le toma campañas.
        assert row["campaign_clients"] == [
            {"id": "destino", "name": "Destino", "campaigns": 1, "allocation": "exclusive"}
        ]


# ================================================= META · nivel ad set

def meta_source(account_id, *, campaign_ids=None, selections=None, allocation="exclusive"):
    src = {"ad_account_id": account_id, "campaign_ids": campaign_ids or [], "allocation": allocation}
    if selections:
        src["adset_selections"] = [
            {"campaign_id": cid, "adset_ids": ids, "allocation": alloc}
            for cid, ids, alloc in selections
        ]
    return src


def set_meta(c, client_id, sources):
    return c.put(f"/api/clients/{client_id}", json={"meta_ads_sources": sources})


def adset_names(blk):
    return sorted(a["ad_set_name"] for a in blk["current"].get("adsets", []))


class TestMetaAdSetLevel:
    """Tercer nivel: el cliente consume solo ciertos ad sets de una campaña."""

    def test_1_whole_account_still_works(self, api):
        c, *_ = api
        blk = block(c, "origen", "meta_ads")
        assert blk["sources"][0]["level"] == "account"
        assert campaign_names(blk) == ["Destino Leads", "Destino Remarketing", "Origen Awareness"]

    def test_2_specific_campaigns(self, api):
        c, *_ = api
        c.post("/api/clients", json={"name": "Campañero"})
        set_meta(c, "campanero", [meta_source(META_SHARED, campaign_ids=["601"])])
        blk = block(c, "campanero", "meta_ads")
        assert blk["sources"][0]["level"] == "campaign"
        assert campaign_names(blk) == ["Destino Leads"]

    def test_3_specific_adsets(self, api):
        c, _, meta = api
        c.post("/api/clients", json={"name": "Adsetero"})
        r = set_meta(c, "adsetero", [meta_source(META_SHARED, selections=[("601", ["6011"], "exclusive")])])
        assert r.status_code == 200, r.text
        blk = block(c, "adsetero", "meta_ads")
        assert blk["sources"][0]["level"] == "adset"
        # Solo ese ad set, y la campaña aparece marcada como parcial.
        assert adset_names(blk) == ["Destino Leads · AS1"]
        campaign = blk["current"]["campaigns"][0]
        assert campaign["partial"] is True and campaign["selected_adsets"] == 1
        # Métricas = las del ad set, no las de la campaña entera.
        assert campaign["spend"] == METRICS["601"]["spend"] / 2
        # Se pidió a nivel ad set, nunca la campaña entera.
        assert any(call[3] == ("6011",) for call in meta.calls if call[0] == META_SHARED)

    def test_4_two_clients_split_one_campaign_by_adsets(self, api):
        c, *_ = api
        c.post("/api/clients", json={"name": "Uno"})
        c.post("/api/clients", json={"name": "Dos"})
        set_meta(c, "uno", [meta_source(META_SHARED, selections=[("601", ["6011"], "exclusive")])])
        r = set_meta(c, "dos", [meta_source(META_SHARED, selections=[("601", ["6012"], "exclusive")])])
        assert r.status_code == 200, r.text
        assert adset_names(block(c, "uno", "meta_ads")) == ["Destino Leads · AS1"]
        assert adset_names(block(c, "dos", "meta_ads")) == ["Destino Leads · AS2"]

    def test_5_adset_exclusive_carves_a_whole_campaign_selection(self, api):
        c, *_ = api
        c.post("/api/clients", json={"name": "Campañero"})
        set_meta(c, "campanero", [meta_source(META_SHARED, campaign_ids=["601"])])
        c.post("/api/clients", json={"name": "Adsetero"})
        set_meta(c, "adsetero", [meta_source(META_SHARED, selections=[("601", ["6011"], "exclusive")])])
        # El dueño de la campaña la sigue viendo, pero solo el ad set que le queda.
        blk = block(c, "campanero", "meta_ads", refresh="true")
        assert adset_names(blk) == ["Destino Leads · AS2"]
        assert blk["current"]["campaigns"][0]["partial"] is True
        assert blk["current"]["totals"]["spend"] == METRICS["601"]["spend"] / 2

    def test_6_campaign_exclusive_carves_a_whole_account_selection(self, api):
        c, *_ = api
        c.post("/api/clients", json={"name": "Campañero"})
        set_meta(c, "campanero", [meta_source(META_SHARED, campaign_ids=["601"])])
        # Origen consume la cuenta entera: pierde esa campaña.
        assert campaign_names(block(c, "origen", "meta_ads", refresh="true")) == [
            "Destino Remarketing", "Origen Awareness"]

    def test_6b_adset_exclusive_carves_a_whole_account_selection(self, api):
        c, *_ = api
        c.post("/api/clients", json={"name": "Adsetero"})
        set_meta(c, "adsetero", [meta_source(META_SHARED, selections=[("601", ["6011"], "exclusive")])])
        blk = block(c, "origen", "meta_ads", refresh="true")
        # La campaña sigue, pero solo con el ad set que no se cedió.
        assert "Destino Leads" in campaign_names(blk)
        assert "Destino Leads · AS1" not in adset_names(blk)
        assert "Destino Leads · AS2" in adset_names(blk)

    def test_7_shared_campaign(self, api):
        c, *_ = api
        c.post("/api/clients", json={"name": "Campañero"})
        set_meta(c, "campanero", [meta_source(META_SHARED, campaign_ids=["601"], allocation="shared")])
        assert "Destino Leads" in campaign_names(block(c, "campanero", "meta_ads"))
        assert "Destino Leads" in campaign_names(block(c, "origen", "meta_ads", refresh="true"))

    def test_8_shared_adset(self, api):
        c, *_ = api
        c.post("/api/clients", json={"name": "Adsetero"})
        set_meta(c, "adsetero", [meta_source(META_SHARED, selections=[("601", ["6011"], "shared")])])
        assert adset_names(block(c, "adsetero", "meta_ads")) == ["Destino Leads · AS1"]
        # Compartido: el dueño de la cuenta lo sigue viendo.
        assert "Destino Leads · AS1" in adset_names(block(c, "origen", "meta_ads", refresh="true"))

    def test_9_same_adset_exclusive_twice_is_a_conflict(self, api):
        c, *_ = api
        c.post("/api/clients", json={"name": "Uno"})
        c.post("/api/clients", json={"name": "Dos"})
        set_meta(c, "uno", [meta_source(META_SHARED, selections=[("601", ["6011"], "exclusive")])])
        r = set_meta(c, "dos", [meta_source(META_SHARED, selections=[("601", ["6011"], "exclusive")])])
        assert r.status_code == 409
        detail = r.json()["detail"]
        assert "adset" in detail and "6011" in detail and "Uno" in detail

    def test_10_same_whole_campaign_exclusive_twice_is_a_conflict(self, api):
        c, *_ = api
        c.post("/api/clients", json={"name": "Uno"})
        c.post("/api/clients", json={"name": "Dos"})
        set_meta(c, "uno", [meta_source(META_SHARED, campaign_ids=["601"])])
        r = set_meta(c, "dos", [meta_source(META_SHARED, campaign_ids=["601"])])
        assert r.status_code == 409 and "campaign" in r.json()["detail"]

    def test_10b_a_campaign_and_its_adsets_are_not_a_conflict(self, api):
        """Niveles distintos se recortan, no chocan. Vale en los dos órdenes."""
        c, *_ = api
        c.post("/api/clients", json={"name": "Uno"})
        c.post("/api/clients", json={"name": "Dos"})
        set_meta(c, "uno", [meta_source(META_SHARED, campaign_ids=["601"])])
        assert set_meta(c, "dos", [meta_source(META_SHARED, selections=[("601", ["6011"], "exclusive")])]).status_code == 200
        c.post("/api/clients", json={"name": "Tres"})
        c.post("/api/clients", json={"name": "Cuatro"})
        set_meta(c, "tres", [meta_source(META_SHARED, selections=[("602", ["6021"], "exclusive")])])
        assert set_meta(c, "cuatro", [meta_source(META_SHARED, campaign_ids=["602"])]).status_code == 200

    def test_11_12_13_several_accounts_campaigns_and_adsets(self, api):
        c, *_ = api
        c.post("/api/clients", json={"name": "Mixto"})
        r = set_meta(c, "mixto", [
            meta_source(META_OWN, campaign_ids=["701"]),
            meta_source(META_SHARED, campaign_ids=["602"],
                        selections=[("601", ["6011", "6012"], "exclusive")]),
        ])
        assert r.status_code == 200, r.text
        blk = block(c, "mixto", "meta_ads")
        assert {s["account_id"] for s in blk["sources"]} == {META_OWN, META_SHARED}
        assert campaign_names(blk) == ["Destino Leads", "Destino Prospecting", "Destino Remarketing"]
        # Los dos ad sets seleccionados suman la campaña 601 completa.
        leads = next(x for x in blk["current"]["campaigns"] if x["campaign_id"] == "601")
        assert leads["spend"] == METRICS["601"]["spend"] and leads["selected_adsets"] == 2

    def test_14_ads_and_adsets_inherit_the_adset_filter(self, api):
        c, _, meta = api
        c.post("/api/clients", json={"name": "Adsetero"})
        set_meta(c, "adsetero", [meta_source(META_SHARED, selections=[("601", ["6011"], "exclusive")])])
        blk = block(c, "adsetero", "meta_ads", refresh="true")
        assert {a["ad_set_id"] for a in blk["current"]["adsets"]} == {"6011"}
        # El filtro se pidió a la API por ad set, no se recortó en memoria.
        assert all(call[3] == ("6011",) for call in meta.calls if call[0] == META_SHARED and call[3])

    def test_16_comparisons_keep_the_same_adsets(self, api):
        c, _, meta = api
        c.post("/api/clients", json={"name": "Adsetero"})
        set_meta(c, "adsetero", [meta_source(META_SHARED, selections=[("601", ["6011"], "exclusive")])])
        for comparison in ("previous_period", "previous_month", "previous_year"):
            meta.calls.clear()
            blk = block(c, "adsetero", "meta_ads", comparison=comparison, refresh="true")
            assert blk["previous"] is not None, comparison
            assert {call[3] for call in meta.calls if call[0] == META_SHARED} == {("6011",)}
            assert blk["current"]["totals"]["spend"] == blk["previous"]["totals"]["spend"]

    def test_17_daily_series_are_consolidated(self, api):
        c, *_ = api
        c.post("/api/clients", json={"name": "Mixto"})
        set_meta(c, "mixto", [
            meta_source(META_OWN, campaign_ids=["701"]),
            meta_source(META_SHARED, selections=[("601", ["6011"], "exclusive")]),
        ])
        daily = block(c, "mixto", "meta_ads")["current"]["daily"]
        assert [d["date"] for d in daily] == ["2026-09-01", "2026-09-24"]
        assert daily[0]["spend"] == (METRICS["701"]["spend"] + METRICS["601"]["spend"] / 2) / 2

    def test_18_derived_metrics_are_recomputed_from_totals(self, api):
        c, *_ = api
        c.post("/api/clients", json={"name": "Mixto"})
        set_meta(c, "mixto", [
            meta_source(META_OWN, campaign_ids=["701"]),
            meta_source(META_SHARED, selections=[("601", ["6011"], "exclusive")]),
        ])
        t = block(c, "mixto", "meta_ads")["current"]["totals"]
        clicks = METRICS["701"]["clicks"] + METRICS["601"]["clicks"] / 2
        impressions = METRICS["701"]["impressions"] + METRICS["601"]["impressions"] / 2
        assert t["ctr"] == clicks / impressions

    def test_19_reach_is_flagged_as_not_deduplicated(self, api):
        c, *_ = api
        # Cuenta entera y sin recortes: se consulta a nivel cuenta y Meta
        # devuelve el alcance ya deduplicado.
        assert block(c, "origen", "meta_ads")["current"]["reach_is_sum"] is False

        c.post("/api/clients", json={"name": "Adsetero"})
        set_meta(c, "adsetero", [meta_source(META_SHARED, selections=[("601", ["6011"], "exclusive")])])
        # Quien consume ad sets sueltos: el alcance es una suma de segmentos.
        assert block(c, "adsetero", "meta_ads")["current"]["reach_is_sum"] is True
        # Y el dueño de la cuenta, al quedar recortada, ya no puede pedirla a
        # nivel cuenta: su alcance pasa también a ser suma, y así se marca.
        assert block(c, "origen", "meta_ads", refresh="true")["current"]["reach_is_sum"] is True

    def test_22_cache_distinguishes_selections(self, api):
        c, *_ = api
        c.post("/api/clients", json={"name": "Adsetero"})
        set_meta(c, "adsetero", [meta_source(META_SHARED, selections=[("601", ["6011"], "exclusive")])])
        first = block(c, "adsetero", "meta_ads")["current"]["totals"]["spend"]
        set_meta(c, "adsetero", [meta_source(META_SHARED, selections=[("601", ["6011", "6012"], "exclusive")])])
        second = block(c, "adsetero", "meta_ads")["current"]["totals"]["spend"]
        assert first == METRICS["601"]["spend"] / 2
        assert second == METRICS["601"]["spend"]   # sin refresh: la clave cambió

    def test_23_a_failing_source_keeps_the_others(self, api):
        c, _, meta = api
        c.post("/api/clients", json={"name": "Mixto"})
        set_meta(c, "mixto", [
            meta_source(META_OWN, campaign_ids=["701"]),
            meta_source(META_SHARED, selections=[("601", ["6011"], "exclusive")]),
        ])
        meta.broken.add(META_SHARED)
        blk = block(c, "mixto", "meta_ads", refresh="true")
        assert blk["status"] == "ok"
        assert blk["current"]["totals"]["spend"] == METRICS["701"]["spend"]
        partial = [w for w in blk["warnings"] if w["status"] == "partial"]
        assert partial and "1 de 2" in partial[0]["message"]
        assert any(w["account_id"] == META_SHARED for w in blk["warnings"])

    def test_adsets_catalog_endpoint(self, api):
        c, *_ = api
        r = c.get(f"/api/accounts/meta/{META_SHARED}/campaigns/601/adsets").json()
        assert r["status"] == "ok" and r["count"] == 2
        for field in ("ad_account_id", "account_name", "campaign_id", "campaign_name",
                      "adset_id", "adset_name", "status", "effective_status"):
            assert field in r["adsets"][0], field
        c.post("/api/clients", json={"name": "Adsetero"})
        set_meta(c, "adsetero", [meta_source(META_SHARED, selections=[("601", ["6011"], "exclusive")])])
        r2 = c.get(f"/api/accounts/meta/{META_SHARED}/campaigns/601/adsets").json()
        taken = {a["adset_id"]: a["assigned_to"] for a in r2["adsets"]}
        assert taken["6011"] == {"id": "adsetero", "name": "Adsetero", "allocation": "exclusive"}
        assert taken["6012"] is None

    def test_20_21_backward_compatibility(self, api):
        c, *_ = api
        legacy = c.post("/api/clients", json={"name": "Antiguo", "meta_ad_account_id": "act_999000999"}).json()
        assert legacy["meta_ads_sources"] == [{"ad_account_id": "999000999", "campaign_ids": [],
                                               "allocation": "exclusive", "adset_selections": []}]
        # Y una selección de campañas actual sigue significando campañas enteras.
        c.post("/api/clients", json={"name": "Campañero"})
        set_meta(c, "campanero", [meta_source(META_SHARED, campaign_ids=["601"])])
        blk = block(c, "campanero", "meta_ads")
        assert blk["sources"][0]["level"] == "campaign"
        assert blk["current"]["campaigns"][0].get("partial") is None
        assert blk["current"]["totals"]["spend"] == METRICS["601"]["spend"]

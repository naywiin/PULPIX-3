"""Spotify Ads: OAuth dinámico, normalización y aislamiento estricto."""
from __future__ import annotations

import json
from datetime import date

import pytest
from pydantic import ValidationError

from app.config import Settings
from app.migrations import migrate
from app.models import Client
from app.providers.spotify import REPORT_FIELDS, SpotifyAdsProvider
from app.cache import TTLCache
from app.service import DashboardService
from app.spotify_security import (
    SPOTIFY_SANTUARIO_AD_ACCOUNT_ID,
    SPOTIFY_SANTUARIO_BUSINESS_ID,
)


class Response:
    def __init__(self, payload, status=200):
        self.payload = payload
        self.status_code = status

    def json(self):
        return self.payload


class Session:
    def __init__(self):
        self.posts = []
        self.gets = []

    def post(self, url, **kwargs):
        self.posts.append((url, kwargs))
        return Response({"access_token": "temporary-access-token", "expires_in": 3600})

    def get(self, url, **kwargs):
        self.gets.append((url, kwargs))
        if url.endswith(f"ad_accounts/{SPOTIFY_SANTUARIO_AD_ACCOUNT_ID}"):
            return Response({"id": SPOTIFY_SANTUARIO_AD_ACCOUNT_ID, "name": "Santuario Residencial", "currency": "MXN", "status": "ACTIVE"})
        entity = kwargs["params"]["entity_type"]
        row = {
            "SPEND": "123.45", "IMPRESSIONS": "10000", "CLICKS": "40",
            "REACH": "5000", "FREQUENCY": "2", "COMPLETES": "700",
            "VIDEO_VIEWS": "800", "campaign_id": "camp-1", "campaign_name": "Santuario",
            "ad_set_id": "set-1", "ad_set_name": "Set", "ad_id": "ad-1", "ad_name": "Ad",
            "date": "2026-09-01",
        }
        if entity == "AD_ACCOUNT":
            for key in ("campaign_id", "campaign_name", "ad_set_id", "ad_set_name", "ad_id", "ad_name"):
                row.pop(key, None)
        return Response({"rows": [row]})


@pytest.fixture
def provider(monkeypatch):
    monkeypatch.setenv("SPOTIFY_CLIENT_ID", "client-id")
    monkeypatch.setenv("SPOTIFY_CLIENT_SECRET", "client-secret")
    monkeypatch.setenv("SPOTIFY_REFRESH_TOKEN", "refresh-token")
    session = Session()
    return SpotifyAdsProvider(Settings(), session), session


def test_only_confirmed_account_can_be_queried(provider):
    spotify, session = provider
    with pytest.raises(ValueError, match="no autorizado"):
        spotify.performance("00000000-0000-0000-0000-000000000000", "2026-09-01", "2026-09-02")
    assert session.posts == [] and session.gets == []


def test_unassigned_client_does_not_call_spotify_provider():
    class MustNotQuery:
        def performance(self, *args, **kwargs):
            raise AssertionError("Spotify no debe consultarse sin asignación explícita")

    service = DashboardService({"spotify_ads": MustNotQuery()}, TTLCache(60), TTLCache(60))
    client = Client(id="santuario-residencial", name="Santuario", slug="santuario-residencial")
    block = service.platform_block(client, "spotify_ads", (date(2026, 10, 1), date(2026, 10, 5)), None)
    assert block["status"] == "not_configured"
    assert client.spotify_ads_sources == []


def test_performance_keeps_provenance_and_never_requests_deprecated_fields(provider):
    spotify, session = provider
    result = spotify.performance(SPOTIFY_SANTUARIO_AD_ACCOUNT_ID, "2026-09-01", "2026-09-02")
    assert len(session.posts) == 1  # access token renovado una vez y mantenido solo en memoria
    assert "CPM" not in REPORT_FIELDS
    assert "COMPLETION_RATE" not in REPORT_FIELDS
    assert {r["entity_type"] for r in [result["totals"], *result["campaigns"], *result["adsets"], *result["ads"]]} == {
        "AD_ACCOUNT", "CAMPAIGN", "AD_SET", "AD"
    }
    for row in [result["totals"], *result["daily"], *result["campaigns"], *result["adsets"], *result["ads"]]:
        assert row["source"] == "spotify_ads"
        assert row["client_slug"] == "santuario-residencial"
        assert row["business_id"] == SPOTIFY_SANTUARIO_BUSINESS_ID
        assert row["ad_account_id"] == SPOTIFY_SANTUARIO_AD_ACCOUNT_ID
        assert row["currency"] == "MXN"
        assert row["conversions"] is None
        assert row["conversion_value"] is None
    assert result["totals"]["cpm"] == pytest.approx(12.345)
    assert result["campaigns"][0]["video_views"] == 800


def test_spotify_source_is_discoverable_and_linked_source_queries_provider():
    from app.models import SpotifyAdsSource

    class LinkedProvider:
        def __init__(self):
            self.calls = []

        def performance(self, account_id, start, end):
            self.calls.append((account_id, start, end))
            return {"account_name": "Santuario Ads", "currency": "MXN", "totals": {},
                    "campaigns": [], "adsets": [], "ads": [], "daily": []}

        def list_accounts(self):
            return [{"platform": "spotify_ads", "id": SPOTIFY_SANTUARIO_AD_ACCOUNT_ID,
                     "business_id": SPOTIFY_SANTUARIO_BUSINESS_ID, "name": "Santuario Ads",
                     "currency": "MXN", "status": "ACTIVE", "account_type": "ad_account"}]

    provider = LinkedProvider()
    service = DashboardService({"spotify_ads": provider}, TTLCache(60), TTLCache(60))
    discovery = service.discover("spotify_ads")
    assert discovery["status"] == "ok"
    assert discovery["accounts"][0]["id"] == SPOTIFY_SANTUARIO_AD_ACCOUNT_ID
    client = Client(id="santuario-residencial", name="Santuario", slug="santuario-residencial",
                    spotify_ads_sources=[SpotifyAdsSource(business_id=SPOTIFY_SANTUARIO_BUSINESS_ID,
                                                          ad_account_id=SPOTIFY_SANTUARIO_AD_ACCOUNT_ID)])
    block = service.platform_block(client, "spotify_ads", (date(2026, 10, 1), date(2026, 10, 5)), None)
    assert block["status"] == "ok"
    assert provider.calls == [(SPOTIFY_SANTUARIO_AD_ACCOUNT_ID, "2026-10-01", "2026-10-05")]


def test_spotify_auth_and_provider_failures_are_distinct_and_sanitized():
    from app.providers.spotify import SpotifyAuthError

    class BrokenProvider:
        def list_accounts(self):
            raise SpotifyAuthError("OAuth falló con refresh_token=[secret]")

    service = DashboardService({"spotify_ads": BrokenProvider()}, TTLCache(60), TTLCache(60))
    result = service.discover("spotify_ads")
    assert result["status"] == "auth_error"
    assert "[secret]" not in result["detail"]


def test_clients_contract_reports_spotify_link_without_secret_fields(tmp_path):
    from fastapi.testclient import TestClient
    from app.main import create_app
    from app.registry import ClientRegistry
    from app.stores import MemoryClientStore

    source = {"business_id": SPOTIFY_SANTUARIO_BUSINESS_ID,
              "ad_account_id": SPOTIFY_SANTUARIO_AD_ACCOUNT_ID}
    repo = ClientRegistry(MemoryClientStore({"clients": [{
        "id": "santuario-residencial", "name": "Santuario Residencial",
        "slug": "santuario-residencial", "spotify_ads_sources": [source],
        "report_category": "ENTRETENIMIENTO", "report_position": 4,
    }]}))
    app = create_app(settings=Settings(clients_file=tmp_path / "clients.json", static_dir=tmp_path / "dist"),
                     repo=repo, providers={"google_ads": object()})
    response = TestClient(app).get("/api/clients")
    assert response.status_code == 200
    client = response.json()["clients"][0]
    assert client["spotify_ads_sources"] == [source]
    assert client["spotify_source_id"] == SPOTIFY_SANTUARIO_AD_ACCOUNT_ID
    assert client["spotify_linked"] is True
    assert client["spotify_status"] == "linked"
    assert client["report_category"] == "ENTRETENIMIENTO"
    assert client["report_position"] == 4
    assert not any("token" in key.lower() or "secret" in key.lower() for key in client)


def test_client_model_rejects_spotify_for_any_other_client():
    with pytest.raises(ValidationError):
        Client.model_validate({
            "id": "otro", "name": "Otro", "slug": "otro",
            "spotify_ads_sources": [{
                "business_id": SPOTIFY_SANTUARIO_BUSINESS_ID,
                "ad_account_id": SPOTIFY_SANTUARIO_AD_ACCOUNT_ID,
            }],
        })


def test_v5_migration_does_not_create_spotify_assignments_and_preserves_existing_sources():
    original = {
        "schema_version": 4,
        "clients": [{
            "id": "santuario-residencial", "name": "Santuario Residencial",
            "slug": "santuario-residencial",
            "google_ads_sources": [{"customer_id": "8323928147", "campaign_ids": [], "allocation": "exclusive"}],
            "meta_ads_sources": [{"ad_account_id": "123", "campaign_ids": ["456"], "allocation": "shared"}],
            "hidden_metrics": ["roas"],
        }],
    }
    before_google = json.dumps(original["clients"][0]["google_ads_sources"], sort_keys=True)
    before_meta = json.dumps(original["clients"][0]["meta_ads_sources"], sort_keys=True)
    migrated, origin, changed = migrate(original)
    client = migrated["clients"][0]
    assert (origin, changed) == (4, True)
    assert json.dumps(client["google_ads_sources"], sort_keys=True) == before_google
    assert json.dumps(client["meta_ads_sources"], sort_keys=True) == before_meta
    assert client["spotify_ads_sources"] == []


def test_v5_migration_preserves_an_explicit_spotify_assignment():
    source = {"business_id": SPOTIFY_SANTUARIO_BUSINESS_ID, "ad_account_id": SPOTIFY_SANTUARIO_AD_ACCOUNT_ID}
    migrated, _, _ = migrate({
        "schema_version": 4,
        "clients": [{"id": "santuario-residencial", "name": "Santuario", "slug": "santuario-residencial",
                     "spotify_ads_sources": [source]}],
    })
    assert migrated["clients"][0]["spotify_ads_sources"] == [source]

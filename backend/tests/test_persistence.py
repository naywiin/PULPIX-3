"""Persistencia del registro: lo que el usuario configura NO se pierde.

El bug que motivó estas pruebas: el registro vivía en el archivo que el
Dockerfile copia dentro de la imagen, así que cada revisión de Cloud Run
devolvía la configuración al estado del repositorio y las fuentes creadas
desde Settings desaparecían.

Aquí no se comprueba estado en memoria: cada prueba **destruye el repositorio**
y crea uno nuevo sobre el mismo almacenamiento, que es lo que realmente ocurre
cuando arranca otra instancia o se despliega otra revisión.
"""
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.migrations import SCHEMA_VERSION, migrate
from app.models import ClientUpdate
from app.registry import ClientRegistry, JsonClientRepository
from app.stores import FileClientStore, MemoryClientStore, create_store

GADS_OWN = "7000000002"
GADS_SHARED = "7000000001"
META_OWN = "8000000002"
META_SHARED = "8000000001"


@pytest.fixture
def store_path(tmp_path: Path) -> Path:
    return tmp_path / "store" / "clients.json"


def registry(path: Path) -> ClientRegistry:
    """Un repositorio NUEVO sobre el mismo almacenamiento (simula otro arranque)."""
    return ClientRegistry(FileClientStore(path))


def seed(path: Path, clients: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"schema_version": SCHEMA_VERSION, "clients": clients}), encoding="utf-8")


def a_client(**over) -> dict:
    base = {"id": "destino", "name": "Destino", "slug": "destino", "active": True,
            "currency": "MXN", "timezone": "America/Mexico_City"}
    return {**base, **over}


class TestRoundTrip:
    """Guardar → destruir el repositorio → volver a leer → debe coincidir."""

    def test_1_3_google_source_survives_a_reload(self, store_path):
        seed(store_path, [a_client(google_ads_customer_id=GADS_OWN)])
        repo = registry(store_path)
        repo.update("destino", ClientUpdate(google_ads_sources=[
            {"customer_id": GADS_OWN, "campaign_ids": [], "allocation": "exclusive"},
            {"customer_id": GADS_SHARED, "campaign_ids": ["201", "202"], "allocation": "exclusive"},
        ]))
        del repo  # el objeto en memoria desaparece

        again = registry(store_path).get("destino")
        assert [s.customer_id for s in again.google_ads_sources] == [GADS_OWN, GADS_SHARED]
        assert again.google_ads_sources[1].campaign_ids == ["201", "202"]
        assert again.google_ads_sources[1].allocation == "exclusive"

    def test_report_category_and_position_persist_without_touching_platform_sources(self, store_path):
        seed(store_path, [a_client(
            google_ads_sources=[{"customer_id": GADS_OWN, "campaign_ids": [], "allocation": "exclusive"}],
            meta_ads_sources=[{"ad_account_id": META_OWN, "campaign_ids": [], "allocation": "exclusive"}],
        )])
        before = registry(store_path).get("destino")
        registry(store_path).update("destino", ClientUpdate(
            report_category="ENTRETENIMIENTO", report_position=2,
        ))
        after = registry(store_path).get("destino")
        assert (after.report_category, after.report_position) == ("ENTRETENIMIENTO", 2)
        assert after.google_ads_sources == before.google_ads_sources
        assert after.meta_ads_sources == before.meta_ads_sources

    def test_4_6_meta_campaign_source_survives_a_reload(self, store_path):
        seed(store_path, [a_client(meta_ad_account_id=META_OWN)])
        registry(store_path).update("destino", ClientUpdate(meta_ads_sources=[
            {"ad_account_id": META_OWN, "campaign_ids": [], "allocation": "exclusive"},
            {"ad_account_id": META_SHARED, "campaign_ids": ["601", "602"], "allocation": "shared"},
        ]))
        again = registry(store_path).get("destino")
        assert [s.ad_account_id for s in again.meta_ads_sources] == [META_OWN, META_SHARED]
        assert again.meta_ads_sources[1].campaign_ids == ["601", "602"]
        assert again.meta_ads_sources[1].allocation == "shared"

    def test_7_9_meta_adset_selection_survives_a_reload(self, store_path):
        seed(store_path, [a_client()])
        registry(store_path).update("destino", ClientUpdate(meta_ads_sources=[{
            "ad_account_id": META_SHARED,
            "campaign_ids": [],
            "allocation": "exclusive",
            "adset_selections": [{"campaign_id": "601", "adset_ids": ["6011", "6012"], "allocation": "exclusive"}],
        }]))
        src = registry(store_path).get("destino").meta_ads_sources[0]
        assert src.ad_account_id == META_SHARED
        assert [(s.campaign_id, s.adset_ids, s.allocation) for s in src.adset_selections] == [
            ("601", ["6011", "6012"], "exclusive")
        ]
        # Una selección parcial sigue siendo parcial: NO se convirtió en campaña entera.
        assert src.campaign_ids == []

    def test_12_allocation_persists_for_every_level(self, store_path):
        seed(store_path, [a_client()])
        registry(store_path).update("destino", ClientUpdate(meta_ads_sources=[{
            "ad_account_id": META_SHARED,
            "campaign_ids": ["602"],
            "allocation": "shared",
            "adset_selections": [{"campaign_id": "601", "adset_ids": ["6011"], "allocation": "shared"}],
        }]))
        src = registry(store_path).get("destino").meta_ads_sources[0]
        assert src.allocation == "shared"
        assert src.adset_selections[0].allocation == "shared"

    def test_16_campaign_ids_never_collapse_to_empty(self, store_path):
        """[] significa "toda la cuenta": convertir ["1","2"] en [] cambiaría los datos."""
        seed(store_path, [a_client()])
        repo = registry(store_path)
        repo.update("destino", ClientUpdate(google_ads_sources=[
            {"customer_id": GADS_SHARED, "campaign_ids": ["1", "2"], "allocation": "exclusive"}]))
        # Varias recargas seguidas no deben erosionar la selección.
        for _ in range(3):
            client = registry(store_path).get("destino")
            assert client.google_ads_sources[0].campaign_ids == ["1", "2"]
        # Y lo escrito en disco tampoco.
        raw = json.loads(store_path.read_text())
        assert raw["clients"][0]["google_ads_sources"][0]["campaign_ids"] == ["1", "2"]

    def test_everything_together_round_trips_byte_for_byte(self, store_path):
        seed(store_path, [a_client()])
        payload = {
            "google_ads_sources": [
                {"customer_id": GADS_OWN, "campaign_ids": [], "allocation": "exclusive"},
                {"customer_id": GADS_SHARED, "campaign_ids": ["201"], "allocation": "shared"},
            ],
            "meta_ads_sources": [{
                "ad_account_id": META_SHARED, "campaign_ids": ["602"], "allocation": "exclusive",
                "adset_selections": [{"campaign_id": "601", "adset_ids": ["6011"], "allocation": "exclusive"}],
            }],
            "ga4_property_ids": ["111", "222"],
        }
        saved = registry(store_path).update("destino", ClientUpdate(**payload)).model_dump()
        reloaded = registry(store_path).get("destino").model_dump()
        assert saved == reloaded


class TestNullVsAbsent:
    """Campo ausente = no tocar. Campo null explícito = desasignar."""

    def _client(self, store_path):
        seed(store_path, [a_client(google_ads_customer_id=GADS_OWN, meta_ad_account_id=META_OWN,
                                   ga4_property_ids=["111"])])
        return registry(store_path)

    def test_absent_fields_change_nothing(self, store_path):
        repo = self._client(store_path)
        repo.update("destino", ClientUpdate())
        after = registry(store_path).get("destino")
        assert after.google_ads_customer_id == GADS_OWN
        assert after.meta_ad_account_id == META_OWN
        assert after.ga4_property_ids == ["111"]

    def test_10_explicit_null_removes_the_primary_account(self, store_path):
        repo = self._client(store_path)
        repo.update("destino", ClientUpdate(google_ads_customer_id=None))
        after = registry(store_path).get("destino")
        assert after.google_ads_customer_id is None
        assert after.google_ads_login_customer_id is None
        assert after.google_ads_sources == []
        # Y persiste tras otra recarga, no solo en memoria.
        assert registry(store_path).get("destino").google_ads_customer_id is None

    def test_explicit_null_works_for_meta_and_ga4(self, store_path):
        repo = self._client(store_path)
        repo.update("destino", ClientUpdate(meta_ad_account_id=None, ga4_property_ids=[]))
        after = registry(store_path).get("destino")
        assert after.meta_ad_account_id is None and after.meta_ads_sources == []
        assert after.ga4_property_ids == [] and after.ga4_property_id is None

    def test_canonical_empty_source_lists_remove_legacy_primary_ids(self, store_path):
        """Es el payload real de Settings al elegir Pendiente y guardar."""
        repo = self._client(store_path)
        repo.update("destino", ClientUpdate(
            google_ads_sources=[],
            meta_ads_sources=[],
        ))
        after = registry(store_path).get("destino")
        assert after.google_ads_sources == []
        assert after.google_ads_customer_id is None
        assert after.google_ads_login_customer_id is None
        assert after.meta_ads_sources == []
        assert after.meta_ad_account_id is None

    def test_11_removing_the_primary_account_keeps_partial_sources(self, store_path):
        """Quitar la cuenta principal NO puede borrar las campañas de otras cuentas."""
        repo = self._client(store_path)
        repo.update("destino", ClientUpdate(google_ads_sources=[
            {"customer_id": GADS_OWN, "campaign_ids": [], "allocation": "exclusive"},
            {"customer_id": GADS_SHARED, "campaign_ids": ["201", "202"], "allocation": "exclusive"},
        ]))
        repo.update("destino", ClientUpdate(google_ads_customer_id=None))

        after = registry(store_path).get("destino")
        assert after.google_ads_customer_id is None
        assert [s.customer_id for s in after.google_ads_sources] == [GADS_SHARED]
        assert after.google_ads_sources[0].campaign_ids == ["201", "202"]

    def test_removing_the_primary_meta_account_keeps_adset_selections(self, store_path):
        repo = self._client(store_path)
        repo.update("destino", ClientUpdate(meta_ads_sources=[
            {"ad_account_id": META_OWN, "campaign_ids": [], "allocation": "exclusive"},
            {"ad_account_id": META_SHARED, "campaign_ids": [], "allocation": "exclusive",
             "adset_selections": [{"campaign_id": "601", "adset_ids": ["6011"], "allocation": "exclusive"}]},
        ]))
        repo.update("destino", ClientUpdate(meta_ad_account_id=None))
        after = registry(store_path).get("destino")
        assert after.meta_ad_account_id is None
        assert [s.ad_account_id for s in after.meta_ads_sources] == [META_SHARED]
        assert after.meta_ads_sources[0].adset_selections[0].adset_ids == ["6011"]

    def test_meta_adset_only_source_is_never_removed_as_the_primary(self, store_path):
        repo = self._client(store_path)
        repo.update("destino", ClientUpdate(meta_ads_sources=[
            {"ad_account_id": META_SHARED, "campaign_ids": [], "allocation": "exclusive",
             "adset_selections": [{"campaign_id": "601", "adset_ids": ["6011"], "allocation": "exclusive"}]},
            {"ad_account_id": META_OWN, "campaign_ids": [], "allocation": "exclusive"},
        ]))
        repo.update("destino", ClientUpdate(meta_ad_account_id=None))
        after = registry(store_path).get("destino")
        assert after.meta_ad_account_id is None
        assert [s.ad_account_id for s in after.meta_ads_sources] == [META_SHARED]
        assert after.meta_ads_sources[0].adset_selections[0].adset_ids == ["6011"]


class TestMigrations:
    """Migrar traduce el formato; nunca borra ni resetea configuración."""

    V1 = [{
        "id": "antiguo", "name": "Antiguo", "slug": "antiguo", "active": True,
        "currency": "MXN", "timezone": "America/Mexico_City",
        "google_ads_customer_id": "6364343858", "google_ads_login_customer_id": "7954113535",
        "meta_ad_account_id": "1066569498344996", "ga4_property_id": "496398757",
    }]

    def test_13_migration_keeps_every_assignment(self):
        payload, origin, changed = migrate({"clients": [dict(c) for c in self.V1]})
        assert (origin, changed) == (1, True)
        c = payload["clients"][0]
        assert c["google_ads_sources"] == [{
            "customer_id": "6364343858", "login_customer_id": "7954113535",
            "campaign_ids": [], "allocation": "exclusive"}]
        assert c["meta_ads_sources"] == [{
            "ad_account_id": "1066569498344996", "campaign_ids": [],
            "allocation": "exclusive", "adset_selections": []}]
        assert c["ga4_property_ids"] == ["496398757"]
        # Nada del original se perdió.
        assert c["google_ads_customer_id"] == "6364343858"
        assert payload["schema_version"] == SCHEMA_VERSION

    def test_migration_is_idempotent(self):
        once, _, _ = migrate({"clients": [dict(c) for c in self.V1]})
        twice, origin, changed = migrate(json.loads(json.dumps(once)))
        assert twice["clients"] == once["clients"]
        assert (origin, changed) == (SCHEMA_VERSION, False)

    def test_migration_never_touches_already_configured_sources(self):
        current = {"schema_version": SCHEMA_VERSION, "clients": [{
            **self.V1[0],
            "google_ads_sources": [{"customer_id": "999", "login_customer_id": None,
                                    "campaign_ids": ["1", "2"], "allocation": "shared"}],
            "meta_ads_sources": [{"ad_account_id": "888", "campaign_ids": [], "allocation": "exclusive",
                                  "adset_selections": [{"campaign_id": "7", "adset_ids": ["71"],
                                                        "allocation": "exclusive"}]}],
        }]}
        out, _, changed = migrate(json.loads(json.dumps(current)))
        assert changed is False
        assert out["clients"][0]["google_ads_sources"][0]["campaign_ids"] == ["1", "2"]
        assert out["clients"][0]["meta_ads_sources"][0]["adset_selections"][0]["adset_ids"] == ["71"]

    def test_a_v1_store_is_readable_without_reconfiguring(self, store_path):
        store_path.parent.mkdir(parents=True, exist_ok=True)
        store_path.write_text(json.dumps({"clients": self.V1}), encoding="utf-8")
        client = registry(store_path).get("antiguo")
        assert client.google_ads_sources[0].customer_id == "6364343858"
        assert client.meta_ads_sources[0].ad_account_id == "1066569498344996"
        assert client.ga4_property_ids == ["496398757"]


class TestSeedAndRevisions:
    """El seed inicializa un almacenamiento vacío; jamás pisa datos existentes."""

    def _app(self, tmp_path: Path, seed_clients: list[dict], store: Path):
        seed_file = tmp_path / "seed.json"
        seed_file.write_text(json.dumps({"clients": seed_clients}), encoding="utf-8")
        settings = Settings(clients_file=store, clients_seed_file=seed_file,
                            static_dir=tmp_path / "nodist")
        providers = {p: _NoProvider() for p in ("google_ads", "meta_ads", "ga4")}
        return TestClient(create_app(settings, providers=providers))

    def test_14_seed_only_initializes_an_empty_store(self, tmp_path):
        store = tmp_path / "store" / "clients.json"
        c = self._app(tmp_path, [a_client(id="uno", name="Uno", slug="uno")], store)
        assert [x["id"] for x in c.get("/api/clients").json()["clients"]] == ["uno"]

        # El usuario configura algo…
        c.put("/api/clients/uno", json={"google_ads_sources": [
            {"customer_id": GADS_SHARED, "campaign_ids": ["201"], "allocation": "exclusive"}]})

        # …y llega una revisión nueva con OTRO seed (imagen actualizada).
        c2 = self._app(tmp_path, [a_client(id="uno", name="Uno", slug="uno"),
                                  a_client(id="dos", name="Dos", slug="dos")], store)
        clients = c2.get("/api/clients").json()["clients"]
        # El seed NO se aplicó: manda lo persistido.
        assert [x["id"] for x in clients] == ["uno"]
        assert clients[0]["google_ads_sources"][0]["campaign_ids"] == ["201"]

    def test_seed_can_be_explicitly_disabled_for_isolated_revisions(self, tmp_path):
        seed_file = tmp_path / "seed.json"
        seed_file.write_text(json.dumps({"clients": [a_client(id="stale", name="Stale", slug="stale")]}), encoding="utf-8")
        settings = Settings(
            clients_file=tmp_path / "empty-test-store" / "clients.json",
            clients_seed_file=seed_file,
            seed_clients=False,
            static_dir=tmp_path / "nodist",
        )
        providers = {p: _NoProvider() for p in ("google_ads", "meta_ads", "ga4")}
        client = TestClient(create_app(settings, providers=providers))
        assert client.get("/api/clients").json()["clients"] == []

    def test_15_a_new_revision_does_not_reset_clients(self, tmp_path):
        """Revisión A configura; revisión B (otro proceso) ve lo mismo."""
        store = tmp_path / "store" / "clients.json"
        a = self._app(tmp_path, [a_client(id="uno", name="Uno", slug="uno")], store)
        a.put("/api/clients/uno", json={"meta_ads_sources": [{
            "ad_account_id": META_SHARED, "campaign_ids": [], "allocation": "exclusive",
            "adset_selections": [{"campaign_id": "601", "adset_ids": ["6011"], "allocation": "exclusive"}]}]})
        before = a.get("/api/clients/uno").json()

        b = self._app(tmp_path, [a_client(id="uno", name="Uno", slug="uno")], store)
        assert b.get("/api/clients/uno").json() == before

    def test_seeding_a_v1_file_migrates_instead_of_discarding(self, tmp_path):
        store = tmp_path / "store" / "clients.json"
        c = self._app(tmp_path, TestMigrations.V1, store)
        body = c.get("/api/clients/antiguo").json()
        assert body["google_ads_sources"][0]["customer_id"] == "6364343858"
        assert body["ga4_property_ids"] == ["496398757"]

    def test_a_backup_is_kept_before_migrating_the_stored_document(self, tmp_path):
        store = tmp_path / "store" / "clients.json"
        store.parent.mkdir(parents=True, exist_ok=True)
        store.write_text(json.dumps({"clients": TestMigrations.V1}), encoding="utf-8")
        self._app(tmp_path, [], store)
        raw = json.loads(store.read_text())
        assert raw["schema_version"] == SCHEMA_VERSION
        # El documento anterior queda guardado por si hubiera que revisarlo.
        assert raw["backup_v1"][0]["google_ads_customer_id"] == "6364343858"


class _NoProvider:
    def list_accounts(self):
        return []

    def performance(self, *a, **kw):
        raise RuntimeError("sin proveedor en esta prueba")

    def adsets(self, *a, **kw):
        return []


class TestStores:
    def test_the_file_store_writes_atomically_and_reads_back(self, tmp_path):
        store = FileClientStore(tmp_path / "a" / "clients.json")
        assert store.is_empty()
        store.save({"schema_version": 3, "clients": [{"id": "x"}]})
        assert store.load()["clients"] == [{"id": "x"}]
        assert not store.is_empty()
        # No quedan temporales a medias.
        assert [p.name for p in (tmp_path / "a").iterdir()] == ["clients.json"]

    def test_the_memory_store_isolates_copies(self):
        store = MemoryClientStore({"clients": [{"id": "x"}]})
        data = store.load()
        data["clients"].append({"id": "y"})
        assert store.load()["clients"] == [{"id": "x"}]

    def test_create_store_honours_the_configured_backend(self, tmp_path):
        assert create_store(Settings(clients_file=tmp_path / "c.json")).name == "file"
        assert create_store(Settings(store_backend="memory")).name == "memory"
        assert create_store(Settings(store_backend="firestore")).name == "firestore"

    def test_the_legacy_repository_name_still_works(self, tmp_path):
        path = tmp_path / "clients.json"
        seed(path, [a_client()])
        repo = JsonClientRepository(path)
        assert repo.get("destino").name == "Destino"
        assert repo.path == path

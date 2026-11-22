"""Persistencia durable en producción: `FirestoreClientStore` de verdad.

`google-cloud-firestore` está en requirements.txt pero SOLO se instala en la
imagen de Cloud Run (ver Dockerfile); en esta VM de desarrollo no está
instalado y no hay proyecto de GCP a mano. Para probar el store real sin
tocar infraestructura ni depender de credenciales, se inyecta un Firestore
falso en `sys.modules["google.cloud.firestore"]` — `FirestoreClientStore._db()`
hace `from google.cloud import firestore` de forma perezosa (dentro del
método), así que recoge el falso sin que el store sepa que no es el real.

La pieza clave para simular "una revisión/instancia nueva" es que los datos
viven en `_FakeFirestoreServer`, FUERA de cualquier instancia de
`FirestoreClientStore`: crear un store nuevo (como hace cada arranque del
proceso) no resetea nada, exactamente como el Firestore real.
"""
from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.migrations import SCHEMA_VERSION
from app.models import ClientUpdate
from app.registry import ClientRegistry
from app.stores import FirestoreClientStore


class _FakeDocRef:
    def __init__(self, backing: dict, doc_id: str):
        self._backing = backing
        self.id = doc_id


class _FakeSnapshot:
    def __init__(self, doc_id: str, data: dict):
        self.id = doc_id
        self._data = data

    def to_dict(self) -> dict:
        return dict(self._data)


class _FakeCollection:
    def __init__(self, backing: dict):
        self._backing = backing

    def document(self, doc_id: str) -> _FakeDocRef:
        return _FakeDocRef(self._backing, str(doc_id))

    def stream(self):
        return [_FakeSnapshot(doc_id, data) for doc_id, data in self._backing.items()]

    def list_documents(self):
        return [_FakeDocRef(self._backing, doc_id) for doc_id in list(self._backing.keys())]


class _FakeBatch:
    def __init__(self):
        self._ops: list[tuple] = []

    def set(self, doc_ref: _FakeDocRef, data: dict) -> None:
        self._ops.append(("set", doc_ref, dict(data)))

    def delete(self, doc_ref: _FakeDocRef) -> None:
        self._ops.append(("delete", doc_ref))

    def commit(self) -> None:
        for op in self._ops:
            if op[0] == "set":
                _, doc_ref, data = op
                doc_ref._backing[doc_ref.id] = data
            else:
                _, doc_ref = op
                doc_ref._backing.pop(doc_ref.id, None)
        self._ops.clear()


class _FakeClient:
    """Análogo a `google.cloud.firestore.Client`: solo lo que usa el store."""

    def __init__(self, server: "_FakeFirestoreServer", **kwargs):
        self._server = server

    def collection(self, name: str) -> _FakeCollection:
        return _FakeCollection(self._server.backing(name))

    def batch(self) -> _FakeBatch:
        return _FakeBatch()


class _FakeFirestoreServer:
    """El "backend real": persiste fuera de cualquier `FirestoreClientStore`
    o `_FakeClient`, tal como Firestore persiste fuera del proceso de Cloud Run."""

    def __init__(self) -> None:
        self._collections: dict[str, dict[str, dict]] = {}

    def backing(self, collection: str) -> dict:
        return self._collections.setdefault(collection, {})

    def client(self, **kwargs) -> _FakeClient:
        return _FakeClient(self, **kwargs)


@pytest.fixture
def fake_firestore(monkeypatch) -> _FakeFirestoreServer:
    server = _FakeFirestoreServer()
    fake_module = types.ModuleType("google.cloud.firestore")
    fake_module.Client = lambda **kwargs: server.client(**kwargs)  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "google.cloud.firestore", fake_module)
    return server


def new_store(collection: str = "clients") -> FirestoreClientStore:
    """Una instancia NUEVA del store — simula otro proceso/instancia/revisión."""
    return FirestoreClientStore(collection=collection)


def a_client(**over) -> dict:
    base = {"id": "destino", "name": "Destino", "slug": "destino", "active": True,
            "currency": "MXN", "timezone": "America/Mexico_City"}
    return {**base, **over}


GADS_OWN = "7000000002"
GADS_SHARED = "7000000001"
META_OWN = "8000000002"
META_SHARED = "8000000001"


class TestRoundTrip:
    """11 — guardar → destruir la instancia de repository → crear una nueva →
    leer del almacenamiento → verificar. Nunca solo estado en memoria."""

    def test_1_google_primary_account_persists(self, fake_firestore):
        ClientRegistry(new_store()).store.save({"schema_version": SCHEMA_VERSION, "clients": [a_client()]})
        ClientRegistry(new_store()).update("destino", ClientUpdate(google_ads_customer_id=GADS_OWN))
        again = ClientRegistry(new_store()).get("destino")
        assert again.google_ads_customer_id == GADS_OWN

    def test_2_google_sources_persist(self, fake_firestore):
        ClientRegistry(new_store()).store.save({"schema_version": SCHEMA_VERSION, "clients": [a_client()]})
        ClientRegistry(new_store()).update("destino", ClientUpdate(google_ads_sources=[
            {"customer_id": GADS_OWN, "campaign_ids": [], "allocation": "exclusive"},
            {"customer_id": GADS_SHARED, "campaign_ids": ["201", "202"], "allocation": "exclusive"},
        ]))
        again = ClientRegistry(new_store()).get("destino")
        assert [s.customer_id for s in again.google_ads_sources] == [GADS_OWN, GADS_SHARED]

    def test_3_campaign_ids_persist(self, fake_firestore):
        ClientRegistry(new_store()).store.save({"schema_version": SCHEMA_VERSION, "clients": [a_client()]})
        ClientRegistry(new_store()).update("destino", ClientUpdate(google_ads_sources=[
            {"customer_id": GADS_SHARED, "campaign_ids": ["1", "2"], "allocation": "exclusive"},
        ]))
        again = ClientRegistry(new_store()).get("destino")
        assert again.google_ads_sources[0].campaign_ids == ["1", "2"]

    def test_4_allocation_persists(self, fake_firestore):
        ClientRegistry(new_store()).store.save({"schema_version": SCHEMA_VERSION, "clients": [a_client()]})
        ClientRegistry(new_store()).update("destino", ClientUpdate(google_ads_sources=[
            {"customer_id": GADS_SHARED, "campaign_ids": ["1"], "allocation": "shared"},
        ]))
        again = ClientRegistry(new_store()).get("destino")
        assert again.google_ads_sources[0].allocation == "shared"

    def test_5_meta_primary_account_persists(self, fake_firestore):
        ClientRegistry(new_store()).store.save({"schema_version": SCHEMA_VERSION, "clients": [a_client()]})
        ClientRegistry(new_store()).update("destino", ClientUpdate(meta_ad_account_id=META_OWN))
        again = ClientRegistry(new_store()).get("destino")
        assert again.meta_ad_account_id == META_OWN

    def test_6_meta_sources_persist(self, fake_firestore):
        ClientRegistry(new_store()).store.save({"schema_version": SCHEMA_VERSION, "clients": [a_client()]})
        ClientRegistry(new_store()).update("destino", ClientUpdate(meta_ads_sources=[
            {"ad_account_id": META_OWN, "campaign_ids": [], "allocation": "exclusive"},
            {"ad_account_id": META_SHARED, "campaign_ids": ["601"], "allocation": "shared"},
        ]))
        again = ClientRegistry(new_store()).get("destino")
        assert [s.ad_account_id for s in again.meta_ads_sources] == [META_OWN, META_SHARED]

    def test_7_campaign_selections_persist(self, fake_firestore):
        ClientRegistry(new_store()).store.save({"schema_version": SCHEMA_VERSION, "clients": [a_client()]})
        ClientRegistry(new_store()).update("destino", ClientUpdate(meta_ads_sources=[
            {"ad_account_id": META_SHARED, "campaign_ids": ["602"], "allocation": "exclusive"},
        ]))
        again = ClientRegistry(new_store()).get("destino")
        assert again.meta_ads_sources[0].campaign_ids == ["602"]

    def test_8_adset_selections_persist(self, fake_firestore):
        ClientRegistry(new_store()).store.save({"schema_version": SCHEMA_VERSION, "clients": [a_client()]})
        ClientRegistry(new_store()).update("destino", ClientUpdate(meta_ads_sources=[{
            "ad_account_id": META_SHARED, "campaign_ids": [], "allocation": "exclusive",
            "adset_selections": [{"campaign_id": "601", "adset_ids": ["6011", "6012"], "allocation": "exclusive"}],
        }]))
        again = ClientRegistry(new_store()).get("destino")
        sel = again.meta_ads_sources[0].adset_selections[0]
        assert (sel.campaign_id, sel.adset_ids, sel.allocation) == ("601", ["6011", "6012"], "exclusive")

    def test_9_ga4_property_ids_persist(self, fake_firestore):
        ClientRegistry(new_store()).store.save({"schema_version": SCHEMA_VERSION, "clients": [a_client()]})
        ClientRegistry(new_store()).update("destino", ClientUpdate(ga4_property_ids=["111", "222"]))
        again = ClientRegistry(new_store()).get("destino")
        assert again.ga4_property_ids == ["111", "222"]
        assert again.ga4_property_id == "111"

    def test_10_hidden_metrics_persists(self, fake_firestore):
        ClientRegistry(new_store()).store.save({"schema_version": SCHEMA_VERSION, "clients": [a_client()]})
        ClientRegistry(new_store()).update("destino", ClientUpdate(hidden_metrics=["roas"]))
        again = ClientRegistry(new_store()).get("destino")
        assert again.hidden_metrics == ["roas"]

    def test_11_explicit_null_persists(self, fake_firestore):
        ClientRegistry(new_store()).store.save({"schema_version": SCHEMA_VERSION, "clients": [
            a_client(google_ads_customer_id=GADS_OWN)]})
        ClientRegistry(new_store()).update("destino", ClientUpdate(google_ads_customer_id=None))
        again = ClientRegistry(new_store()).get("destino")
        assert again.google_ads_customer_id is None
        assert again.google_ads_sources == []

    def test_12_partial_source_without_primary_persists(self, fake_firestore):
        ClientRegistry(new_store()).store.save({"schema_version": SCHEMA_VERSION, "clients": [a_client()]})
        repo = ClientRegistry(new_store())
        repo.update("destino", ClientUpdate(google_ads_sources=[
            {"customer_id": GADS_OWN, "campaign_ids": [], "allocation": "exclusive"},
            {"customer_id": GADS_SHARED, "campaign_ids": ["201", "202"], "allocation": "exclusive"},
        ]))
        repo.update("destino", ClientUpdate(google_ads_customer_id=None))
        again = ClientRegistry(new_store()).get("destino")
        assert again.google_ads_customer_id is None
        assert [s.customer_id for s in again.google_ads_sources] == [GADS_SHARED]
        assert again.google_ads_sources[0].campaign_ids == ["201", "202"]

    def test_13_a_third_instance_still_sees_everything(self, fake_firestore):
        """"Restart" repetido: cada instancia nueva vuelve a ver lo mismo."""
        ClientRegistry(new_store()).store.save({"schema_version": SCHEMA_VERSION, "clients": [a_client()]})
        ClientRegistry(new_store()).update("destino", ClientUpdate(
            google_ads_customer_id=GADS_OWN, ga4_property_ids=["111"], hidden_metrics=["roas"]))
        for _ in range(3):
            c = ClientRegistry(new_store()).get("destino")
            assert (c.google_ads_customer_id, c.ga4_property_ids, c.hidden_metrics) == (GADS_OWN, ["111"], ["roas"])

    def test_everything_together_round_trips(self, fake_firestore):
        ClientRegistry(new_store()).store.save({"schema_version": SCHEMA_VERSION, "clients": [a_client()]})
        payload = ClientUpdate(
            google_ads_sources=[
                {"customer_id": GADS_OWN, "campaign_ids": [], "allocation": "exclusive"},
                {"customer_id": GADS_SHARED, "campaign_ids": ["201"], "allocation": "shared"},
            ],
            meta_ads_sources=[{
                "ad_account_id": META_SHARED, "campaign_ids": ["602"], "allocation": "exclusive",
                "adset_selections": [{"campaign_id": "601", "adset_ids": ["6011"], "allocation": "exclusive"}],
            }],
            ga4_property_ids=["111", "222"],
            hidden_metrics=["roas"],
        )
        saved = ClientRegistry(new_store()).update("destino", payload).model_dump()
        reloaded = ClientRegistry(new_store()).get("destino").model_dump()
        assert saved == reloaded


class TestNewRevision:
    """12/16 — simula un `gcloud run deploy`: revisión A configura, la
    "imagen" se recicla (nueva app, nuevo store, MISMO Firestore falso) y la
    revisión B debe ver exactamente lo mismo."""

    def _app(self, tmp_path: Path):
        seed = tmp_path / "seed.json"
        seed.write_text('{"clients": [{"id": "uno", "name": "Uno", "slug": "uno", '
                         '"currency": "MXN", "timezone": "America/Mexico_City", "active": true}]}')
        settings = Settings(store_backend="firestore", clients_seed_file=seed, static_dir=tmp_path / "nodist")
        providers = {p: _NoProvider() for p in ("google_ads", "meta_ads", "ga4")}
        return TestClient(create_app(settings, providers=providers))

    def test_a_new_revision_keeps_the_previous_configuration(self, fake_firestore, tmp_path):
        a = self._app(tmp_path)
        assert a.get("/api/clients").json()["clients"][0]["id"] == "uno"
        a.put("/api/clients/uno", json={
            "google_ads_sources": [{"customer_id": GADS_SHARED, "campaign_ids": ["201"], "allocation": "exclusive"}],
            "hidden_metrics": ["roas"],
        })

        # "Revisión B": misma imagen/seed, proceso nuevo, Firestore intacto.
        b = self._app(tmp_path)
        body = b.get("/api/clients/uno").json()
        assert body["google_ads_sources"][0]["campaign_ids"] == ["201"]
        assert body["hidden_metrics"] == ["roas"]

    def test_seed_only_initializes_an_empty_collection(self, fake_firestore, tmp_path):
        a = self._app(tmp_path)
        a.put("/api/clients/uno", json={"active": False})

        # Otra "revisión" con el MISMO seed no debe re-copiarlo ni pisar el cambio.
        b = self._app(tmp_path)
        assert b.get("/api/clients/uno").json()["active"] is False

    def test_health_reports_firestore_as_durable(self, fake_firestore, tmp_path):
        a = self._app(tmp_path)
        body = a.get("/api/health").json()
        assert body["persistence"] == {
            "backend": "firestore",
            "budgets_backend": "firestore",
            "durable": True,
            "warning": None,
        }

    def test_15_migration_is_idempotent_across_revisions(self, fake_firestore, tmp_path):
        """Revisión A migra un documento v1 (guardado directo, sin pasar por
        la app) a la versión actual; revisión B lo vuelve a leer: nada cambia
        ni se pierde en la segunda pasada."""
        v1 = {"id": "uno", "name": "Uno", "slug": "uno", "active": True,
              "currency": "MXN", "timezone": "America/Mexico_City",
              "google_ads_customer_id": GADS_OWN}
        new_store().save({"clients": [v1]})

        a = self._app(tmp_path)
        after_a = a.get("/api/clients/uno").json()
        assert after_a["google_ads_sources"][0]["customer_id"] == GADS_OWN
        assert after_a["hidden_metrics"] == []

        b = self._app(tmp_path)
        after_b = b.get("/api/clients/uno").json()
        assert after_b == after_a


class _NoProvider:
    def list_accounts(self):
        return []

    def performance(self, *a, **kw):
        raise RuntimeError("sin proveedor en esta prueba")

    def adsets(self, *a, **kw):
        return []

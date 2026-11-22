"""Visibilidad de métricas por cliente (`hidden_metrics`).

Cubre: normalización por familia (ver `metrics.normalize_metric_family`),
persistencia entre recargas, backward compatibility con registros sin el
campo, migración de un store antiguo, y la configuración real de los seis
clientes a los que se les oculta ROAS.
"""
import json
from pathlib import Path

import pytest

from app.migrations import SCHEMA_VERSION, migrate
from app.models import Client, ClientUpdate
from app.registry import ClientRegistry
from app.stores import FileClientStore

ROAS_CLIENTS = [
    "maternalia",
    "doc-towers",
    "dorado-center",
    "santuario-residencial",
    "blumia",
]


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


class TestNormalization:
    def test_unknown_variants_collapse_to_the_roas_family(self):
        c = Client(id="x", name="X", slug="x",
                   hidden_metrics=["ROAS", "purchase_roas", "Blended ROAS", " meta_roas "])
        assert c.hidden_metrics == ["roas"]

    def test_empty_and_blank_entries_are_dropped(self):
        c = Client(id="x", name="X", slug="x", hidden_metrics=["", "  ", "roas"])
        assert c.hidden_metrics == ["roas"]

    def test_a_single_string_is_tolerated(self):
        c = Client(id="x", name="X", slug="x", hidden_metrics="roas")
        assert c.hidden_metrics == ["roas"]


class TestPersistence:
    def test_1_hidden_metrics_persists(self, store_path):
        seed(store_path, [a_client()])
        registry(store_path).update("destino", ClientUpdate(hidden_metrics=["roas"]))
        again = registry(store_path).get("destino")
        assert again.hidden_metrics == ["roas"]

    def test_2_a_client_without_hidden_metrics_gets_an_empty_list(self, store_path):
        seed(store_path, [a_client()])
        client = registry(store_path).get("destino")
        assert client.hidden_metrics == []

    def test_11_saving_and_reloading_keeps_hidden_metrics(self, store_path):
        seed(store_path, [a_client()])
        repo = registry(store_path)
        repo.update("destino", ClientUpdate(hidden_metrics=["roas"]))
        del repo
        for _ in range(3):
            assert registry(store_path).get("destino").hidden_metrics == ["roas"]
        raw = json.loads(store_path.read_text())
        assert raw["clients"][0]["hidden_metrics"] == ["roas"]

    def test_absent_field_in_a_patch_does_not_clear_hidden_metrics(self, store_path):
        seed(store_path, [a_client(hidden_metrics=["roas"])])
        repo = registry(store_path)
        repo.update("destino", ClientUpdate(notes="actualizado"))
        assert registry(store_path).get("destino").hidden_metrics == ["roas"]

    def test_explicit_empty_list_clears_hidden_metrics(self, store_path):
        seed(store_path, [a_client(hidden_metrics=["roas"])])
        repo = registry(store_path)
        repo.update("destino", ClientUpdate(hidden_metrics=[]))
        assert registry(store_path).get("destino").hidden_metrics == []

    def test_hidden_metrics_is_independent_of_source_structure(self, store_path):
        """No debe depender de si el cliente usa cuenta completa, campañas
        sueltas o varias fuentes: es ortogonal a la asignación de campañas."""
        seed(store_path, [a_client(hidden_metrics=["roas"])])
        repo = registry(store_path)
        repo.update("destino", ClientUpdate(google_ads_sources=[
            {"customer_id": "123", "campaign_ids": ["1", "2"], "allocation": "shared"},
        ]))
        after = registry(store_path).get("destino")
        assert after.hidden_metrics == ["roas"]
        assert after.google_ads_sources[0].campaign_ids == ["1", "2"]


class TestMigration:
    V1 = [{
        "id": "antiguo", "name": "Antiguo", "slug": "antiguo", "active": True,
        "currency": "MXN", "timezone": "America/Mexico_City",
        "google_ads_customer_id": "6364343858",
    }]

    def test_3_an_old_migration_does_not_break(self):
        payload, origin, changed = migrate({"clients": [dict(c) for c in self.V1]})
        assert (origin, changed) == (1, True)
        assert payload["clients"][0]["hidden_metrics"] == []
        assert payload["schema_version"] == SCHEMA_VERSION

    def test_migration_is_idempotent_for_hidden_metrics(self):
        once, _, _ = migrate({"clients": [dict(c) for c in self.V1]})
        twice, origin, changed = migrate(json.loads(json.dumps(once)))
        assert twice["clients"] == once["clients"]
        assert (origin, changed) == (SCHEMA_VERSION, False)

    def test_12_migrating_never_overwrites_hidden_metrics_already_set(self):
        current = {"schema_version": SCHEMA_VERSION, "clients": [{
            **self.V1[0], "hidden_metrics": ["roas"],
        }]}
        out, _, changed = migrate(json.loads(json.dumps(current)))
        assert changed is False
        assert out["clients"][0]["hidden_metrics"] == ["roas"]

    def test_a_v1_store_without_the_field_is_readable(self, store_path):
        store_path.parent.mkdir(parents=True, exist_ok=True)
        store_path.write_text(json.dumps({"clients": self.V1}), encoding="utf-8")
        client = registry(store_path).get("antiguo")
        assert client.hidden_metrics == []


class TestConfiguredClients:
    """La configuración real: los seis clientes con ROAS oculto en el registro
    persistido de la aplicación, sin afectar a ningún otro."""

    @pytest.fixture
    def real_registry(self) -> ClientRegistry:
        path = Path(__file__).resolve().parents[1] / "data" / "clients.json"
        return ClientRegistry(FileClientStore(path))

    @pytest.mark.parametrize("client_id", ROAS_CLIENTS)
    def test_4_9_configured_client_has_roas_hidden(self, real_registry, client_id):
        assert real_registry.get(client_id).hidden_metrics == ["roas"]

    def test_10_a_client_outside_the_list_does_not_have_roas_hidden(self, real_registry):
        for client in real_registry.list():
            if client.id not in ROAS_CLIENTS:
                assert "roas" not in client.hidden_metrics, client.id

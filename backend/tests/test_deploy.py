"""Comportamiento de producción: /health, SPA servida por FastAPI y registro persistente."""
import json
from pathlib import Path

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


class NoProvider:
    def list_accounts(self):
        return []


PROVIDERS = {"google_ads": NoProvider(), "meta_ads": NoProvider(), "ga4": NoProvider()}


def make_dist(tmp: Path) -> Path:
    dist = tmp / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<!doctype html><div id=root></div>")
    (dist / "assets" / "index-abc123.js").write_text("console.log(1)")
    return dist


def make_app(tmp: Path, **kw) -> TestClient:
    seed = tmp / "seed.json"
    seed.write_text(json.dumps({"version": 1, "clients": [{"id": "acme", "name": "Acme", "slug": "acme"}]}))
    settings = Settings(clients_file=tmp / "store" / "clients.json", clients_seed_file=seed, static_dir=make_dist(tmp), **kw)
    return TestClient(create_app(settings, providers=PROVIDERS))


def test_health(tmp_path):
    r = make_app(tmp_path).get("/health")
    assert r.status_code == 200 and r.json() == {"status": "ok"}


def test_spa_routes_serve_frontend(tmp_path):
    c = make_app(tmp_path)
    for path in ("/", "/settings/clients", "/cualquier/ruta"):
        r = c.get(path)
        assert r.status_code == 200, path
        assert "id=root" in r.text
        assert r.headers["cache-control"] == "no-cache"
    a = c.get("/assets/index-abc123.js")
    assert a.status_code == 200 and "immutable" in a.headers["cache-control"]


def test_api_routes_are_never_the_frontend(tmp_path):
    c = make_app(tmp_path)
    assert c.get("/api/clients").json()["clients"][0]["id"] == "acme"
    assert c.get("/api/accounts/meta").json()["platform"] == "meta_ads"
    for path in ("/api/no-existe", "/api", "/api/"):
        r = c.get(path)
        assert r.status_code == 404, path
        assert r.headers["content-type"].startswith("application/json")


def test_registry_is_seeded_into_persistent_storage(tmp_path):
    c = make_app(tmp_path)
    store = tmp_path / "store" / "clients.json"
    assert store.exists()
    c.put("/api/clients/acme", json={"active": False})
    # Un nuevo arranque (otra instancia) conserva los cambios y no re-siembra.
    c2 = TestClient(create_app(Settings(clients_file=store, clients_seed_file=tmp_path / "seed.json", static_dir=tmp_path / "dist"), providers=PROVIDERS))
    assert c2.get("/api/clients/acme").json()["active"] is False

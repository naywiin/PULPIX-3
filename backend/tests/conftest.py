"""Adaptaciones del runner de tests; no modifica el servidor productivo."""
from __future__ import annotations

import asyncio

import anyio.to_thread
import fastapi.testclient
import httpx
import pytest


class DirectASGITestClient:
    """Sustituto mínimo de TestClient para runners sin threads funcionales."""

    __test__ = False

    def __init__(
        self,
        app,
        base_url: str = "http://testserver",
        raise_server_exceptions: bool = True,
        root_path: str = "",
        follow_redirects: bool = True,
        headers: dict[str, str] | None = None,
        cookies=None,
        **_kwargs,
    ) -> None:
        self.app = app
        self.base_url = base_url
        self.raise_server_exceptions = raise_server_exceptions
        self.root_path = root_path
        self.follow_redirects = follow_redirects
        self.headers = headers or {}
        self.cookies = cookies

    def request(self, method: str, url: str, **kwargs):
        async def send():
            transport = httpx.ASGITransport(
                app=self.app,
                raise_app_exceptions=self.raise_server_exceptions,
                root_path=self.root_path,
            )
            async with httpx.AsyncClient(
                transport=transport,
                base_url=self.base_url,
                headers=self.headers,
                cookies=self.cookies,
                follow_redirects=self.follow_redirects,
            ) as client:
                response = await client.request(method, url, **kwargs)
                self.cookies = client.cookies
                return response

        return asyncio.run(send())

    def get(self, url: str, **kwargs):
        return self.request("GET", url, **kwargs)

    def post(self, url: str, **kwargs):
        return self.request("POST", url, **kwargs)

    def put(self, url: str, **kwargs):
        return self.request("PUT", url, **kwargs)


# Los módulos de tests importan TestClient después de cargar conftest.
fastapi.testclient.TestClient = DirectASGITestClient


@pytest.fixture(autouse=True)
def direct_sync_endpoint_runner(monkeypatch):
    """Ejecuta handlers sync en el loop durante tests.

    El runner aislado bloquea el worker thread de AnyIO usado por Starlette.
    Routing, validación, middleware y handlers de excepciones siguen pasando
    por ASGI/TestClient; sólo se evita ese salto de thread en el entorno test.
    """

    async def run_direct(function, *args, **kwargs):
        return function(*args, **kwargs)

    async def run_sync_direct(function, *args, **_runner_options):
        return function(*args)

    monkeypatch.setattr("fastapi.routing.run_in_threadpool", run_direct)
    monkeypatch.setattr("anyio.to_thread.run_sync", run_sync_direct)

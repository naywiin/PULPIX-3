"""Contrato de WooSync. Ninguna prueba realiza llamadas de red reales."""
from __future__ import annotations

import logging
from pathlib import Path

import httpx
import pytest
import requests

from app.config import Settings
from app.main import create_app
from app.providers.base import ProviderError, ProviderNotConfigured
from app.providers.woosync import (
    OVERVIEW_URL,
    WooSyncProvider,
    build_overview_params,
    validate_applied_branches,
    validate_overview,
)
from app.registry import JsonClientRepository
from app.woosync import ALL_BRANCHES


def payload(**updates):
    base = {
        "sales_overview": {
            "total_sales": 1234.56,
            "net_sales": 1100,
            "gross_sales": 1300,
            "orders_count": 4,
            "average_order_value": 308.64,
            "items_sold": 8,
        },
        "daily_sales": [{"date": "2026-09-01", "total_sales": 100}],
        "weekly_sales": [{"week": "2026-W36", "total_sales": 700}],
        "monthly_sales": [{"month": "2026-09", "total_sales": 1234.56}],
        "top_products": [{"product_id": 7, "name": "Silla", "quantity": 2, "total_sales": 800}],
        "top_categories": [{"name": "Muebles", "quantity": 2, "total_sales": 800}],
        "top_customers": [{"customer_id": 3, "name": "Cliente", "orders_count": 2, "total_spent": 900}],
        "order_statuses": [{"status": "wc-completed", "orders_count": 4}],
        "customers": {"registered": 3, "guests": 1, "total": 4},
        "applied_filters": {},
        "future_field": {"kept": True},
    }
    base.update(updates)
    return base


def nacional_real_payload():
    """Fixture de regresión tomada de la respuesta real verificada de WooSync."""
    return payload(
        sales_overview={
            "orders_count": 46,
            "unique_customers_count": 34,
            "products_count": 215,
            "customers_count": 997,
            "gross_sales": 90135,
            "coupon_amount": 7432.6,
            "refunds_total": 0,
            "net_sales": 83097.2,
            "tax_total": 0,
            "shipping_total": 12980,
            "total_sales": 96077.2,
            "items_sold": 128,
            "average_order_value": 2088.63,
            "last_order_at": "2026-09-29 11:10:23",
        },
        applied_filters={"branches": ["nacional"]},
    )


class FakeWooSync:
    def __init__(self, data=None, error: Exception | None = None):
        self.data = data or payload()
        self.error = error
        self.calls: list[dict] = []

    def overview(self, **filters):
        self.calls.append(filters)
        if self.error:
            raise self.error
        return self.data


@pytest.fixture
def api(tmp_path: Path):
    settings = Settings(clients_file=tmp_path / "clients.json", static_dir=tmp_path / "nodist")
    fake = FakeWooSync()
    app = create_app(settings, JsonClientRepository(settings.clients_file), {"woosync": fake})
    return app, fake


@pytest.fixture
def anyio_backend():
    """La suite usa asyncio; no requiere instalar Trio."""
    return "asyncio"


def asgi_client(app):
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


@pytest.mark.anyio
async def test_query_without_optional_filters_uses_reasonable_default(api):
    app, fake = api
    async with asgi_client(app) as client:
        response = await client.get("/api/woosync/overview")
    assert response.status_code == 200
    assert fake.calls == [{
        "from_date": None,
        "to_date": None,
        "branches": sorted(ALL_BRANCHES),
        "top_limit": 10,
        "compare_to": None,
    }]
    assert "nacional" in fake.calls[0]["branches"]


@pytest.mark.anyio
async def test_from_to_and_top_limit(api):
    app, fake = api
    async with asgi_client(app) as client:
        response = await client.get("/api/woosync/overview", params={"from": "2026-09-01", "to": "2026-09-29", "top_limit": 10})
    assert response.status_code == 200
    assert fake.calls[-1]["from_date"] == "2026-09-01"
    assert fake.calls[-1]["to_date"] == "2026-09-29"
    assert fake.calls[-1]["top_limit"] == 10


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("nacional", ["nacional"]),
        ("monterrey", ["monterrey"]),
        ("saltillo", ["saltillo"]),
        ("leon", ["leon"]),
        ("chihuahua", ["chihuahua"]),
        ("cdmx", ["cdmx"]),
        ("monterrey,cdmx", ["cdmx", "monterrey"]),
        ("", sorted(ALL_BRANCHES)),
    ],
)
@pytest.mark.anyio
async def test_branches_simple_multiple_and_all_locations_omission(api, raw, expected):
    app, fake = api
    async with asgi_client(app) as client:
        response = await client.get("/api/woosync/overview", params={"branches": raw})
    assert response.status_code == 200
    assert fake.calls[-1]["branches"] == expected


@pytest.mark.anyio
async def test_compare_to_previous_period(api):
    app, fake = api
    async with asgi_client(app) as client:
        response = await client.get("/api/woosync/overview", params={"compare_to": "previous_period"})
    assert response.status_code == 200
    assert fake.calls[-1]["compare_to"] == "previous_period"


def test_empty_params_are_omitted_and_branches_are_comma_separated():
    assert build_overview_params(from_date=None, to_date="", branches=[], top_limit=None, compare_to=None) == {}
    assert build_overview_params(branches=["monterrey", "cdmx"], top_limit=10) == {
        "branches": "monterrey,cdmx",
        "top_limit": 10,
    }
    assert build_overview_params(branches=["nacional"], top_limit=10)["branches"] == "nacional"


def test_total_sales_is_required_and_total_sale_is_never_used():
    assert validate_overview(payload())["sales_overview"]["total_sales"] == 1234.56
    broken = payload(sales_overview={"total_sale": 99})
    with pytest.raises(ProviderError, match="total_sales"):
        validate_overview(broken)


def test_all_confirmed_collections_and_customers_are_preserved():
    data = validate_overview(payload())
    for key in (
        "daily_sales", "weekly_sales", "monthly_sales", "top_products",
        "top_categories", "top_customers", "order_statuses",
    ):
        assert data[key], key
    assert data["customers"] == {"registered": 3, "guests": 1, "total": 4}
    assert data["future_field"] == {"kept": True}


def test_optional_arrays_null_or_missing_become_empty_and_partial_payload_works():
    data = validate_overview({"sales_overview": {"total_sales": None}, "daily_sales": None})
    assert data["daily_sales"] == [] and data["weekly_sales"] == []
    assert data["sales_overview"]["total_sales"] is None


@pytest.mark.anyio
async def test_cache_differs_by_branch_and_range(api):
    app, fake = api
    base = {"from": "2026-09-01", "to": "2026-09-29"}
    async with asgi_client(app) as client:
        await client.get("/api/woosync/overview", params={**base, "branches": "monterrey"})
        await client.get("/api/woosync/overview", params={**base, "branches": "monterrey"})
        await client.get("/api/woosync/overview", params={**base, "branches": "cdmx"})
        await client.get("/api/woosync/overview", params={**base, "to": "2026-09-28", "branches": "monterrey"})
    assert len(fake.calls) == 3


@pytest.mark.anyio
async def test_cache_never_confuses_nacional_with_all_locations(api):
    app, fake = api
    params = {"from": "2026-09-01", "to": "2026-09-29"}
    async with asgi_client(app) as client:
        await client.get("/api/woosync/overview", params={**params, "branches": "nacional"})
        await client.get("/api/woosync/overview", params=params)
        await client.get("/api/woosync/overview", params={**params, "branches": "nacional"})
    assert len(fake.calls) == 2
    assert fake.calls[0]["branches"] == ["nacional"]
    assert fake.calls[1]["branches"] == sorted(ALL_BRANCHES)


@pytest.mark.anyio
async def test_cache_canonicalizes_all_locations_order_and_duplicates(api):
    app, fake = api
    params = {"from": "2026-09-01", "to": "2026-09-29"}
    all_locations = ",".join(ALL_BRANCHES)
    reversed_with_duplicate = ",".join((*reversed(ALL_BRANCHES), ALL_BRANCHES[0]))
    async with asgi_client(app) as client:
        await client.get("/api/woosync/overview", params=params)
        await client.get("/api/woosync/overview", params={**params, "branches": all_locations})
        await client.get("/api/woosync/overview", params={**params, "branches": reversed_with_duplicate})
    assert len(fake.calls) == 1
    assert fake.calls[0]["branches"] == sorted(ALL_BRANCHES)


@pytest.mark.anyio
async def test_cache_distinguishes_nacional_monterrey_all_and_other_combination(api):
    app, fake = api
    params = {"from": "2026-09-01", "to": "2026-09-29"}
    async with asgi_client(app) as client:
        for branches in ("nacional", "monterrey", ",".join(ALL_BRANCHES), "monterrey,cdmx"):
            await client.get("/api/woosync/overview", params={**params, "branches": branches})
    assert [call["branches"] for call in fake.calls] == [
        ["nacional"],
        ["monterrey"],
        sorted(ALL_BRANCHES),
        ["cdmx", "monterrey"],
    ]


@pytest.mark.parametrize(
    "params",
    [
        {"from": "09/01/2026"},
        {"from": "2026-09-30", "to": "2026-09-01"},
        {"branches": "desconocida"},
        {"top_limit": 0},
        {"top_limit": 51},
        {"compare_to": "previous_year"},  # requiere from y to
        {"compare_to": "previous_year", "from": "2026-09-01"},
        {"compare_to": "otra"},
    ],
)
@pytest.mark.anyio
async def test_filter_validation(api, params):
    app, fake = api
    async with asgi_client(app) as client:
        response = await client.get("/api/woosync/overview", params=params)
    assert response.status_code == 422
    assert fake.calls == []


class FakeResponse:
    def __init__(self, status=200, data=None, invalid_json=False):
        self.status_code = status
        self.data = data or payload()
        self.invalid_json = invalid_json

    def json(self):
        if self.invalid_json:
            raise ValueError("bad json")
        return self.data


class FakeSession:
    def __init__(self, response=None, error=None):
        self.response = response or FakeResponse()
        self.error = error
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        if self.error:
            raise self.error
        return self.response


def provider(monkeypatch, session):
    monkeypatch.setenv("WOOSYNC_ANALYTICS_API_KEY", "super-secret-test-key")
    return WooSyncProvider(Settings(), session)


def test_provider_uses_exact_endpoint_headers_and_query_params(monkeypatch):
    session = FakeSession(FakeResponse(data=payload(applied_filters={"branches": ["monterrey", "cdmx"]})))
    p = provider(monkeypatch, session)
    p.overview(from_date="2026-09-01", to_date="2026-09-29", branches=["monterrey", "cdmx"], top_limit=10, compare_to="previous_period")
    url, kwargs = session.calls[0]
    assert url == OVERVIEW_URL
    assert kwargs["params"] == {"from": "2026-09-01", "to": "2026-09-29", "branches": "monterrey,cdmx", "top_limit": 10, "compare_to": "previous_period"}
    assert kwargs["headers"] == {"Authorization": "Bearer super-secret-test-key", "Accept": "application/json"}
    assert "json" not in kwargs and "data" not in kwargs


def test_applied_filters_branches_are_preserved_and_mismatch_warns(caplog):
    data = validate_overview(nacional_real_payload())
    with caplog.at_level(logging.WARNING, logger="mp.woosync"):
        validate_applied_branches(data, ["nacional"])
    assert data["applied_filters"]["branches"] == ["nacional"]
    assert caplog.text == ""

    with caplog.at_level(logging.WARNING, logger="mp.woosync"):
        validate_applied_branches(data, ["monterrey"])
    assert "solicitados=['monterrey']" in caplog.text
    assert "aplicados=['nacional']" in caplog.text


def test_verified_nacional_fixture_keeps_all_confirmed_values():
    data = validate_overview(nacional_real_payload())
    assert data["sales_overview"] == {
        "orders_count": 46,
        "unique_customers_count": 34,
        "products_count": 215,
        "customers_count": 997,
        "gross_sales": 90135,
        "coupon_amount": 7432.6,
        "refunds_total": 0,
        "net_sales": 83097.2,
        "tax_total": 0,
        "shipping_total": 12980,
        "total_sales": 96077.2,
        "items_sold": 128,
        "average_order_value": 2088.63,
        "last_order_at": "2026-09-29 11:10:23",
    }


def test_token_never_appears_in_response_or_logs(monkeypatch, caplog):
    reflected = payload(debug={"Authorization": "Bearer super-secret-test-key", "token": "super-secret-test-key"})
    p = provider(monkeypatch, FakeSession(FakeResponse(data=reflected)))
    with caplog.at_level(logging.DEBUG):
        result = p.overview(top_limit=10)
    assert "super-secret-test-key" not in str(result)
    assert "super-secret-test-key" not in caplog.text
    assert "Authorization" not in str(result)


@pytest.mark.parametrize(
    ("session", "error_type", "message"),
    [
        (FakeSession(FakeResponse(status=401)), ProviderNotConfigured, "401"),
        (FakeSession(error=requests.Timeout()), ProviderError, "demasiado"),
        (FakeSession(FakeResponse(status=500)), ProviderError, "500"),
    ],
)
def test_provider_handles_401_timeout_and_500(monkeypatch, session, error_type, message):
    with pytest.raises(error_type, match=message):
        provider(monkeypatch, session).overview(top_limit=10)


@pytest.mark.anyio
async def test_internal_endpoint_translates_provider_failures(tmp_path):
    settings = Settings(clients_file=tmp_path / "clients.json", static_dir=tmp_path / "nodist")
    repo = JsonClientRepository(settings.clients_file)
    for error, status in ((ProviderNotConfigured("401"), 503), (ProviderError("timeout"), 502)):
        app = create_app(settings, repo, {"woosync": FakeWooSync(error=error)})
        async with asgi_client(app) as client:
            response = await client.get("/api/woosync/overview")
        assert response.status_code == status


class YearFake(FakeWooSync):
    """Devuelve ventas distintas según el año consultado."""

    def overview(self, **filters):
        self.calls.append(filters)
        year = filters["from_date"][:4]
        total = {"2026": 96077.2, "2025": 152000.0}[year]
        return payload(sales_overview={"total_sales": total, "orders_count": 46}, applied_filters={"branches": filters["branches"]})


def year_api(tmp_path, fake):
    settings = Settings(clients_file=tmp_path / "clients.json", static_dir=tmp_path / "nodist")
    return create_app(settings, JsonClientRepository(settings.clients_file), {"woosync": fake})


@pytest.mark.anyio
async def test_previous_year_makes_two_queries_same_branch_exact_dates(tmp_path):
    fake = YearFake()
    params = {"from": "2026-09-01", "to": "2026-09-29", "branches": "nacional", "compare_to": "previous_year"}
    async with asgi_client(year_api(tmp_path, fake)) as client:
        response = await client.get("/api/woosync/overview", params=params)
    assert response.status_code == 200
    calls = sorted((c["from_date"], c["to_date"], tuple(c["branches"]), c["compare_to"]) for c in fake.calls)
    assert calls == [
        ("2025-09-01", "2025-09-29", ("nacional",), None),
        ("2026-09-01", "2026-09-29", ("nacional",), None),
    ]
    body = response.json()
    assert body["sales_overview"]["total_sales"] == 96077.2
    assert body["comparison"]["type"] == "previous_year"
    assert (body["comparison"]["from"], body["comparison"]["to"]) == ("2025-09-01", "2025-09-29")
    assert body["comparison"]["enabled"] is True
    assert body["comparison"]["sales_overview"]["total_sales"] == 152000.0
    assert body["daily_sales"]  # los campos del contrato se conservan


@pytest.mark.anyio
async def test_previous_year_cache_is_scoped_by_branch_range_and_comparison(tmp_path):
    fake = YearFake()
    base = {"from": "2026-09-01", "to": "2026-09-29", "compare_to": "previous_year"}
    async with asgi_client(year_api(tmp_path, fake)) as client:
        await client.get("/api/woosync/overview", params={**base, "branches": "nacional"})
        await client.get("/api/woosync/overview", params={**base, "branches": "nacional"})  # cache
        assert len(fake.calls) == 2
        await client.get("/api/woosync/overview", params={**base, "branches": "monterrey"})
        assert len(fake.calls) == 4
        await client.get("/api/woosync/overview", params={**base, "from": "2026-08-01", "to": "2026-08-31", "branches": "nacional"})
        assert len(fake.calls) == 6
        # sin comparación: reutiliza la consulta actual ya cacheada
        r = await client.get("/api/woosync/overview", params={"from": "2026-09-01", "to": "2026-09-29", "branches": "nacional"})
        assert len(fake.calls) == 6
        assert "type" not in (r.json().get("comparison") or {})


def test_shift_year_back_handles_leap_days():
    from app.woosync import shift_year_back

    assert shift_year_back("2026-09-01") == "2025-09-01"
    assert shift_year_back("2026-03-15") == "2025-03-15"
    assert shift_year_back("2028-02-29") == "2027-02-28"  # bisiesto -> no bisiesto
    assert shift_year_back("2029-02-28") == "2028-02-28"


@pytest.mark.anyio
async def test_previous_year_upstream_failure_fails_whole_request(tmp_path):
    fake = FakeWooSync(error=ProviderError("timeout"))
    params = {"from": "2026-09-01", "to": "2026-09-29", "compare_to": "previous_year"}
    async with asgi_client(year_api(tmp_path, fake)) as client:
        response = await client.get("/api/woosync/overview", params=params)
    assert response.status_code == 502

"""Validación, configuración y cache de la integración WooSync."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import date
from typing import Any

from .cache import TTLCache
from .dates import add_months

WOOSYNC_BRANCHES = {
    "nacional": "Nacional",
    "monterrey": "Monterrey",
    "saltillo": "Saltillo",
    "leon": "León",
    "chihuahua": "Chihuahua",
    "cdmx": "CDMX",
}
# WooSync confirmó por API que Nacional es una sucursal independiente, no el
# total agregado de las sucursales físicas; "Todas" debe incluir las seis.
ALL_BRANCHES: tuple[str, ...] = ("nacional", "monterrey", "saltillo", "leon", "chihuahua", "cdmx")
# "previous_period" lo resuelve WooSync. "previous_year" NO está documentado ni
# comprobado en la API real: lo construye este backend con una segunda consulta
# del mismo periodo desplazado exactamente un año (misma sucursal).
WOOSYNC_COMPARE_VALUES = {"previous_period", "previous_year"}
PREVIOUS_YEAR = "previous_year"
DEFAULT_TOP_LIMIT = 10
MAX_TOP_LIMIT = 50


def parse_date_filter(value: str | None, name: str) -> str | None:
    if value is None or value == "":
        return None
    try:
        parsed = date.fromisoformat(value)
    except ValueError:
        raise ValueError(f"{name} debe tener formato YYYY-MM-DD") from None
    if parsed.isoformat() != value:
        raise ValueError(f"{name} debe tener formato YYYY-MM-DD")
    return value


def parse_branches(value: str | None) -> list[str]:
    if value is None or not value.strip():
        return []
    result: list[str] = []
    for raw in value.split(","):
        branch = raw.strip().lower()
        if not branch:
            continue
        if branch not in WOOSYNC_BRANCHES:
            raise ValueError(f"Sucursal no válida: {branch}")
        if branch not in result:
            result.append(branch)
    return result


def shift_year_back(value: str) -> str:
    """Misma fecha un año atrás; 29-feb pasa a 28-feb si el año anterior no es bisiesto."""
    return add_months(date.fromisoformat(value), -12).isoformat()


class WooSyncService:
    def __init__(self, provider: Any | None, cache: TTLCache) -> None:
        self.provider = provider
        self.cache = cache

    def overview(
        self,
        *,
        from_date: str | None,
        to_date: str | None,
        branches: list[str],
        top_limit: int,
        compare_to: str | None,
    ) -> dict[str, Any]:
        if self.provider is None:
            from .providers.base import ProviderNotConfigured

            raise ProviderNotConfigured("WooSync no está configurado en este servidor.")
        # Todas las sedes = lista explícita; omitir branches NO es confiable.
        # La misma normalización se aplica al default y a una lista explícita,
        # para que orden y duplicados no creen entradas de cache diferentes.
        branches = sorted(set(branches or ALL_BRANCHES))
        if compare_to != PREVIOUS_YEAR:
            return self._fetch(from_date, to_date, branches, top_limit, compare_to)
        if not from_date or not to_date:
            raise ValueError("compare_to=previous_year requiere from y to")
        prev_from, prev_to = shift_year_back(from_date), shift_year_back(to_date)
        key = ("woosync-overview", from_date, to_date, tuple(branches), top_limit, PREVIOUS_YEAR, prev_from, prev_to)

        def build() -> dict[str, Any]:
            # Dos consultas independientes en paralelo; cada una con su propia cache.
            with ThreadPoolExecutor(max_workers=2) as pool:
                current_f = pool.submit(self._fetch, from_date, to_date, branches, top_limit, None)
                previous_f = pool.submit(self._fetch, prev_from, prev_to, branches, top_limit, None)
                current, previous = current_f.result(), previous_f.result()
            return {
                **current,
                "comparison": {
                    "enabled": True,
                    "type": PREVIOUS_YEAR,
                    "from": prev_from,
                    "to": prev_to,
                    "branches": branches,
                    "sales_overview": previous["sales_overview"],
                    "applied_filters": previous.get("applied_filters", {}),
                },
            }

        return self.cache.get_or_set(key, build)[0]

    def _fetch(
        self, from_date: str | None, to_date: str | None, branches: list[str], top_limit: int, compare_to: str | None
    ) -> dict[str, Any]:
        key = ("woosync-overview", from_date, to_date, tuple(branches), top_limit, compare_to)
        value, _, _ = self.cache.get_or_set(
            key,
            lambda: self.provider.overview(
                from_date=from_date,
                to_date=to_date,
                branches=branches,
                top_limit=top_limit,
                compare_to=compare_to,
            ),
        )
        return value

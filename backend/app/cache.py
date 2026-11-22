"""Cache en memoria con TTL.

Las claves de rendimiento SIEMPRE incluyen client_id, plataforma, account_id y
fechas (ver `perf_key`), de modo que dos clientes nunca comparten entradas
aunque apunten por error a la misma cuenta.
"""
from __future__ import annotations

import threading
import time
from collections.abc import Callable, Hashable
from typing import Any, TypeVar

T = TypeVar("T")


def perf_key(client_id: str, platform: str, account_id: str, start: str, end: str, kind: str = "performance",
             campaign_ids: tuple[str, ...] | list[str] | None = None,
             adset_ids: tuple[str, ...] | list[str] | None = None) -> tuple:
    """Clave de una consulta de rendimiento.

    Incluye el UNIVERSO EFECTIVO (campañas y ad sets): cambiar la asignación de
    un cliente cambia la clave, así que nunca se reutilizan datos del conjunto
    anterior. None = "todo el nivel superior" y es distinto de una selección
    explícita, aunque coincida en contenido.
    """
    campaigns = "all" if campaign_ids is None else tuple(sorted(campaign_ids))
    adsets = "all" if adset_ids is None else tuple(sorted(adset_ids))
    return ("perf", client_id, platform, account_id, start, end, kind, campaigns, adsets)


class TTLCache:
    def __init__(self, ttl_seconds: int, max_entries: int = 2000) -> None:
        self.ttl = ttl_seconds
        self.max_entries = max_entries
        self._data: dict[Hashable, tuple[float, float, Any]] = {}
        self._lock = threading.Lock()
        self._key_locks: dict[Hashable, threading.Lock] = {}

    def get(self, key: Hashable) -> tuple[Any, float] | None:
        """Devuelve (valor, timestamp_de_guardado) o None si no existe/expiró."""
        with self._lock:
            item = self._data.get(key)
            if not item:
                return None
            expires, stored, value = item
            if expires < time.time():
                del self._data[key]
                return None
            return value, stored

    def set(self, key: Hashable, value: Any, ttl: int | None = None) -> float:
        now = time.time()
        with self._lock:
            if len(self._data) >= self.max_entries:
                self._evict(now)
            self._data[key] = (now + (self.ttl if ttl is None else ttl), now, value)
        return now

    def get_or_set(self, key: Hashable, fn: Callable[[], T], ttl: int | None = None, refresh: bool = False) -> tuple[T, float, bool]:
        """Devuelve (valor, guardado_en, desde_cache). Evita llamadas duplicadas concurrentes por clave."""
        if not refresh:
            hit = self.get(key)
            if hit:
                return hit[0], hit[1], True
        with self._lock:
            key_lock = self._key_locks.setdefault(key, threading.Lock())
        with key_lock:
            if not refresh:
                hit = self.get(key)
                if hit:
                    return hit[0], hit[1], True
            value = fn()
            stored = self.set(key, value, ttl)
            return value, stored, False

    def clear(self) -> None:
        with self._lock:
            self._data.clear()

    def _evict(self, now: float) -> None:
        for k in [k for k, (exp, _, _) in self._data.items() if exp < now]:
            del self._data[k]
        while len(self._data) >= self.max_entries:
            oldest = min(self._data, key=lambda k: self._data[k][1])
            del self._data[oldest]

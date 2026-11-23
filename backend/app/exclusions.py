"""Exclusión opcional de clientes y cuentas por patrón de nombre.

En Pulpix no hay exclusiones hardcodeadas de ninguna empresa externa. Si un
despliegue necesita filtrar patrones específicos, puede definir la variable de
entorno `MP_EXCLUDED_CLIENT_PATTERNS` separada por comas.
"""
from __future__ import annotations

import os
import unicodedata
from typing import Any, Iterable


def _load_excluded_patterns() -> tuple[str, ...]:
    raw = os.environ.get("MP_EXCLUDED_CLIENT_PATTERNS", "")
    items = [normalize(part) for part in raw.split(",") if part.strip()]
    return tuple(p for p in items if p)


ACCOUNT_NAME_FIELDS = ("name", "account_name", "property_name")


def normalize(value: Any) -> str:
    """minúsculas + sin acentos + espacios colapsados, para comparar por subcadena."""
    text = unicodedata.normalize("NFKD", str(value)).encode("ascii", "ignore").decode()
    return " ".join(text.lower().split())


EXCLUDED_CLIENT_PATTERNS: tuple[str, ...] = _load_excluded_patterns()


def matched_pattern(*values: Any) -> str | None:
    """Devuelve el patrón que hace excluir estos textos, o None."""
    if not EXCLUDED_CLIENT_PATTERNS:
        return None
    for value in values:
        if not value:
            continue
        low = normalize(value)
        if not low:
            continue
        for pattern in EXCLUDED_CLIENT_PATTERNS:
            if pattern in low:
                return pattern
    return None


def is_excluded(*values: Any) -> bool:
    return matched_pattern(*values) is not None


def filter_accounts(accounts: Iterable[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Separa las cuentas descubiertas en (visibles, excluidas)."""
    kept: list[dict[str, Any]] = []
    dropped: list[dict[str, Any]] = []
    for a in accounts:
        pattern = matched_pattern(*(a.get(f) for f in ACCOUNT_NAME_FIELDS))
        if pattern is None:
            kept.append(a)
        else:
            dropped.append({
                "id": a.get("id"),
                "name": a.get("name"),
                "account_name": a.get("account_name"),
                "pattern": pattern,
            })
    return kept, dropped

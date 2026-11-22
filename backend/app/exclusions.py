"""Exclusión central de clientes y cuentas que no deben aparecer en la aplicación.

Solo afecta a este dashboard: no toca permisos, datos ni cuentas en Google Ads,
Meta Ads o Google Analytics. Se aplica en varias capas para que "Descubrir
cuentas" no pueda reintroducir lo excluido:

- el registro de clientes (`registry.py`) oculta y rechaza clientes excluidos;
- el descubrimiento (`service.discover`) filtra las cuentas de las tres plataformas;
- los bloques del dashboard (`service.platform_block`) descartan propiedades excluidas.

La comparación normaliza el texto (minúsculas, sin acentos, espacios colapsados)
y busca el patrón como subcadena, así que "Vidanta", "VIDANTA", "Marketing
Vidanta", "Vida Vacations", "VIDA VACATIONS", "The View" y "The View Cancun"
quedan todos fuera.
"""
from __future__ import annotations

import unicodedata
from typing import Any, Iterable

EXCLUDED_CLIENT_PATTERNS: tuple[str, ...] = (
    "vidanta",
    "vida vacations",
    "the view",
)

# Campos de una cuenta descubierta que se revisan al filtrar: SIEMPRE el nombre
# propio de la entidad (la cuenta de Google Ads, la propiedad GA4 y su cuenta
# contenedora, que en GA4 es de la marca).
#
# El Business Manager de Meta (`business`) NO se mira a propósito: es un
# contenedor administrativo que una agencia comparte entre marcas distintas.
# "MarketingVidanta" administra, además de Vidanta, cuentas de clientes que SÍ
# queremos ver (Cirque du Soleil JOYÀ, Jungala); filtrar por él los borraba del
# selector sin que nada lo dijera. Un cliente se excluye por su propio nombre,
# nunca por quién le administra la cuenta.
ACCOUNT_NAME_FIELDS = ("name", "account_name", "property_name")


def normalize(value: Any) -> str:
    """minúsculas + sin acentos + espacios colapsados, para comparar por subcadena.

    Así 'The  View' y 'THE VIEW' coinciden con el patrón 'the view'.
    """
    text = unicodedata.normalize("NFKD", str(value)).encode("ascii", "ignore").decode()
    return " ".join(text.lower().split())


def matched_pattern(*values: Any) -> str | None:
    """Devuelve el patrón que hace excluir estos textos, o None."""
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

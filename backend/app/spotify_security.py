"""Vínculo inmutable entre Santuario y su cuenta de Spotify Ads.

Estos valores son identificadores, no secretos. Centralizarlos evita que una
respuesta de descubrimiento, una coincidencia de nombre o un parámetro del
navegador pueda cambiar la cuenta que alimenta al cliente.
"""
from __future__ import annotations

SPOTIFY_SANTUARIO_CLIENT_SLUG = "santuario-residencial"
SPOTIFY_SANTUARIO_BUSINESS_ID = "e27cd146-ddc1-4551-9757-25fe6bc04016"
SPOTIFY_SANTUARIO_AD_ACCOUNT_ID = "79b62668-1427-4b29-8b73-f4cba3525d0d"

# ID devuelto por GET /businesses al buscar el nombre exacto Tachuela Marketing.
SPOTIFY_TACHUELA_BUSINESS_ID = "bf609158-56f9-419a-b913-e54372c5b049"

ALLOWED_SPOTIFY_BUSINESS_IDS = frozenset({
    SPOTIFY_TACHUELA_BUSINESS_ID,
    SPOTIFY_SANTUARIO_BUSINESS_ID,
})


class SpotifyIsolationError(ValueError):
    """La solicitud o fila no pertenece al vínculo aprobado de Santuario."""


def require_santuario_binding(*, client_slug: str, business_id: str, ad_account_id: str) -> None:
    """Rechaza cualquier combinación que no sea exactamente la aprobada."""
    if client_slug != SPOTIFY_SANTUARIO_CLIENT_SLUG:
        raise SpotifyIsolationError("Spotify Ads solo está autorizado para Santuario Residencial")
    if business_id != SPOTIFY_SANTUARIO_BUSINESS_ID:
        raise SpotifyIsolationError("Business de Spotify Ads no autorizado para Santuario")
    if ad_account_id != SPOTIFY_SANTUARIO_AD_ACCOUNT_ID:
        raise SpotifyIsolationError("Ad account de Spotify Ads no autorizado para Santuario")

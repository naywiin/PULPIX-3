"""Spotify Ads API v3, restringida a la cuenta aprobada de Santuario.

No enumera businesses ni cuentas para decidir qué importar. Todas las rutas de
rendimiento validan el vínculo fijo cliente/business/ad account antes de llamar
a Spotify y vuelven a validarlo al normalizar cada fila.
"""
from __future__ import annotations

import base64
import logging
import threading
import time
from datetime import date
from typing import Any

import requests

from ..config import Settings
from ..metrics import derive
from ..spotify_security import (
    SPOTIFY_SANTUARIO_AD_ACCOUNT_ID,
    SPOTIFY_SANTUARIO_BUSINESS_ID,
    SPOTIFY_SANTUARIO_CLIENT_SLUG,
    require_santuario_binding,
)
from .base import ProviderError, ProviderNotConfigured, sanitize


class SpotifyAuthError(ProviderError):
    """Spotify OAuth rejected configured credentials; message remains sanitized."""

log = logging.getLogger("mp.spotify")

BASE_URL = "https://api-partner.spotify.com/ads/v3"
TOKEN_URL = "https://accounts.spotify.com/api/token"

# CPM no está en el enum vigente de aggregate_reports y se deriva localmente.
# COMPLETION_RATE no se solicita porque está deprecado.
REPORT_FIELDS = (
    "SPEND",
    "IMPRESSIONS",
    "CLICKS",
    "REACH",
    "FREQUENCY",
    "CTR",
    "COMPLETES",
    "STARTS",
    "FIRST_QUARTILES",
    "MIDPOINTS",
    "THIRD_QUARTILES",
    "VIDEO_VIEWS",
    "VIDEO_EXPANDS",
    "VIDEO_EXPAND_RATE",
)

VIDEO_FIELDS = (
    "completes",
    "starts",
    "first_quartiles",
    "midpoints",
    "third_quartiles",
    "video_views",
    "video_expands",
)


def _number(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _payload_rows(data: Any) -> list[dict[str, Any]]:
    """Tolera el envelope documentado y las variantes vistas en API v3."""
    if isinstance(data, list):
        return [r for r in data if isinstance(r, dict)]
    if not isinstance(data, dict):
        return []
    for key in ("rows", "data", "items", "results"):
        value = data.get(key)
        if isinstance(value, list):
            return [r for r in value if isinstance(r, dict)]
        if isinstance(value, dict):
            for nested in ("rows", "items", "results"):
                rows = value.get(nested)
                if isinstance(rows, list):
                    return [r for r in rows if isinstance(r, dict)]
    return []


class SpotifyAdsProvider:
    platform = "spotify_ads"

    def __init__(self, settings: Settings, session: requests.Session | None = None) -> None:
        self.settings = settings
        self.session = session or requests.Session()
        self._token: str | None = None
        self._token_expires_at = 0.0
        self._token_lock = threading.Lock()

    def _credentials(self) -> tuple[str, str, str]:
        values = (
            self.settings.spotify_client_id,
            self.settings.spotify_client_secret,
            self.settings.spotify_refresh_token,
        )
        if not all(values):
            raise ProviderNotConfigured(
                "Spotify Ads no está configurado en el servidor (faltan secretos SPOTIFY_*)."
            )
        return values  # type: ignore[return-value]

    def _access_token(self) -> str:
        now = time.monotonic()
        if self._token and now < self._token_expires_at:
            return self._token
        with self._token_lock:
            now = time.monotonic()
            if self._token and now < self._token_expires_at:
                return self._token
            client_id, client_secret, refresh_token = self._credentials()
            basic = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()
            try:
                response = self.session.post(
                    TOKEN_URL,
                    headers={"Authorization": f"Basic {basic}", "Content-Type": "application/x-www-form-urlencoded"},
                    data={"grant_type": "refresh_token", "refresh_token": refresh_token},
                    timeout=30,
                )
            except requests.RequestException as exc:
                raise ProviderError(f"Error de red al autenticar Spotify: {sanitize(type(exc).__name__)}") from None
            try:
                payload = response.json()
            except ValueError:
                raise ProviderError(f"Respuesta inválida al autenticar Spotify (HTTP {response.status_code})") from None
            token = payload.get("access_token") if isinstance(payload, dict) else None
            if response.status_code >= 400 or not token:
                description = payload.get("error_description") if isinstance(payload, dict) else None
                raise SpotifyAuthError(
                    sanitize(f"Spotify rechazó la renovación OAuth (HTTP {response.status_code}): {description or 'sin detalle'}")
                )
            self._token = str(token)
            # Margen de un minuto para no iniciar una llamada con token al límite.
            self._token_expires_at = now + max(60, int(payload.get("expires_in") or 3600) - 60)
            return self._token

    def _get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        try:
            response = self.session.get(
                f"{BASE_URL}/{path.lstrip('/')}",
                params=params,
                headers={"Authorization": f"Bearer {self._access_token()}"},
                timeout=60,
            )
        except requests.RequestException as exc:
            raise ProviderError(f"Error de red con Spotify Ads: {sanitize(type(exc).__name__)}") from None
        try:
            payload = response.json()
        except ValueError:
            raise ProviderError(f"Respuesta inválida de Spotify Ads (HTTP {response.status_code})") from None
        if response.status_code >= 400:
            detail = payload.get("message") or payload.get("error") if isinstance(payload, dict) else None
            error_type = SpotifyAuthError if response.status_code == 401 else ProviderError
            raise error_type(sanitize(f"Spotify Ads API HTTP {response.status_code}: {detail or 'sin detalle'}"))
        if not isinstance(payload, dict):
            raise ProviderError("Spotify Ads devolvió un formato inesperado")
        return payload

    @staticmethod
    def _authorize(account_id: str) -> None:
        require_santuario_binding(
            client_slug=SPOTIFY_SANTUARIO_CLIENT_SLUG,
            business_id=SPOTIFY_SANTUARIO_BUSINESS_ID,
            ad_account_id=str(account_id),
        )

    def _paged(self, path: str, params: dict[str, Any]) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        next_params = dict(params)
        for _ in range(100):
            payload = self._get(path, next_params)
            rows.extend(_payload_rows(payload))
            token = payload.get("continuation_token") or payload.get("continuationToken")
            if not token and isinstance(payload.get("paging"), dict):
                token = payload["paging"].get("continuation_token")
            if not token:
                break
            # La documentación exige enviar solo continuation_token al continuar.
            next_params = {"continuation_token": token}
        else:  # pragma: no cover - protección ante token cíclico de la API
            raise ProviderError("Spotify Ads excedió el límite seguro de páginas")
        return rows

    def account(self, account_id: str) -> dict[str, Any]:
        self._authorize(account_id)
        return self._get(f"ad_accounts/{account_id}")

    def list_accounts(self) -> list[dict[str, Any]]:
        """Devuelve únicamente la cuenta fija; nunca hace descubrimiento global."""
        raw = self.account(SPOTIFY_SANTUARIO_AD_ACCOUNT_ID)
        item = raw.get("data") if isinstance(raw.get("data"), dict) else raw
        return [{
            "platform": self.platform,
            "id": SPOTIFY_SANTUARIO_AD_ACCOUNT_ID,
            "name": item.get("name") or "Santuario Residencial",
            "currency": item.get("currency") or "MXN",
            "status": item.get("status"),
            "account_type": "ad_account",
            "business_id": SPOTIFY_SANTUARIO_BUSINESS_ID,
        }]

    def list_campaigns(self, account_id: str) -> list[dict[str, Any]]:
        self._authorize(account_id)
        rows: list[dict[str, Any]] = []
        offset = 0
        for _ in range(100):
            payload = self._get(
                f"ad_accounts/{account_id}/campaigns",
                {"limit": 100, "offset": offset},
            )
            page = _payload_rows(payload)
            rows.extend(page)
            if len(page) < 100:
                break
            offset += 100
        return [{
            "platform": self.platform,
            "business_id": SPOTIFY_SANTUARIO_BUSINESS_ID,
            "ad_account_id": SPOTIFY_SANTUARIO_AD_ACCOUNT_ID,
            "campaign_id": str(r.get("id") or r.get("campaign_id") or ""),
            "campaign_name": r.get("name") or r.get("campaign_name"),
            "status": r.get("status"),
        } for r in rows if r.get("id") or r.get("campaign_id")]

    @staticmethod
    def _normalize(row: dict[str, Any], entity_type: str, start: str, end: str,
                   currency: str) -> dict[str, Any]:
        # Defensa final antes de que una fila salga del proveedor.
        require_santuario_binding(
            client_slug=SPOTIFY_SANTUARIO_CLIENT_SLUG,
            business_id=SPOTIFY_SANTUARIO_BUSINESS_ID,
            ad_account_id=SPOTIFY_SANTUARIO_AD_ACCOUNT_ID,
        )
        if isinstance(row.get("stats"), list):
            metrics = {
                str(item.get("field_type")): item.get("field_value")
                for item in row["stats"]
                if isinstance(item, dict) and item.get("field_type")
            }
        else:
            metrics = row.get("metrics") if isinstance(row.get("metrics"), dict) else row
        dimensions = row.get("dimensions") if isinstance(row.get("dimensions"), dict) else row
        parent = row.get("parent_entity") if isinstance(row.get("parent_entity"), dict) else {}
        base: dict[str, Any] = {
            "impressions": int(_number(metrics.get("IMPRESSIONS") or metrics.get("impressions"))),
            "clicks": int(_number(metrics.get("CLICKS") or metrics.get("clicks"))),
            "spend": _number(metrics.get("SPEND") or metrics.get("spend")),
            "reach": int(_number(metrics.get("REACH") or metrics.get("reach"))),
            # Spotify aggregate reports do not provide conversion attribution
            # or revenue in this integration. Preserve unsupported metrics as
            # unavailable instead of fabricating measured zeroes.
            "conversions": None,
            "conversion_value": None,
        }
        for key in VIDEO_FIELDS:
            base[key] = _number(metrics.get(key.upper()) or metrics.get(key))
        out = derive(base)
        # Si la API entrega frequency/video_expand_rate, conservarlos como nativos.
        if metrics.get("FREQUENCY") is not None or metrics.get("frequency") is not None:
            out["frequency"] = _number(metrics.get("FREQUENCY") or metrics.get("frequency"))
        out["video_expand_rate"] = _number(
            metrics.get("VIDEO_EXPAND_RATE") or metrics.get("video_expand_rate")
        )
        out.update({
            "source": "spotify_ads",
            "client_slug": SPOTIFY_SANTUARIO_CLIENT_SLUG,
            "business_id": SPOTIFY_SANTUARIO_BUSINESS_ID,
            "ad_account_id": SPOTIFY_SANTUARIO_AD_ACCOUNT_ID,
            "entity_type": entity_type,
            "campaign_id": (
                row.get("entity_id") if entity_type == "CAMPAIGN" else
                parent.get("id") if entity_type == "AD_SET" else
                dimensions.get("campaign_id") or dimensions.get("CAMPAIGN_ID")
            ),
            "campaign_name": (
                row.get("entity_name") if entity_type == "CAMPAIGN" else
                parent.get("name") if entity_type == "AD_SET" else
                dimensions.get("campaign_name") or dimensions.get("CAMPAIGN_NAME")
            ),
            "ad_set_id": (
                row.get("entity_id") if entity_type == "AD_SET" else
                parent.get("id") if entity_type == "AD" else
                dimensions.get("ad_set_id") or dimensions.get("adset_id") or dimensions.get("AD_SET_ID")
            ),
            "ad_set_name": (
                row.get("entity_name") if entity_type == "AD_SET" else
                parent.get("name") if entity_type == "AD" else
                dimensions.get("ad_set_name") or dimensions.get("adset_name") or dimensions.get("AD_SET_NAME")
            ),
            "ad_id": row.get("entity_id") if entity_type == "AD" else dimensions.get("ad_id") or dimensions.get("AD_ID"),
            "ad_name": row.get("entity_name") if entity_type == "AD" else dimensions.get("ad_name") or dimensions.get("AD_NAME"),
            "entity_status": row.get("entity_status") or dimensions.get("status"),
            "date": str(dimensions.get("start_time") or dimensions.get("date") or dimensions.get("DATE") or start)[:10],
            "date_end": str(dimensions.get("end_time") or end)[:10],
            "currency": currency,
        })
        return out

    def _report(self, account_id: str, entity_type: str, start: str, end: str,
                granularity: str, currency: str, include_parent: bool = False) -> list[dict[str, Any]]:
        self._authorize(account_id)
        params: dict[str, Any] = {
            "entity_type": entity_type,
            # Requests serializa la lista como fields=A&fields=B, formato v3.
            "fields": list(REPORT_FIELDS),
            "report_start": f"{start}T00:00:00Z",
            "report_end": f"{end}T00:00:00Z",
            "granularity": granularity,
        }
        if include_parent:
            params["include_parent_entity"] = "true"
        rows = self._paged(f"ad_accounts/{account_id}/aggregate_reports", params)
        return [self._normalize(r, entity_type, start, end, currency) for r in rows]

    def performance(self, account_id: str, start: str, end: str) -> dict[str, Any]:
        self._authorize(account_id)
        if (date.fromisoformat(end) - date.fromisoformat(start)).days > 89:
            raise ProviderError("Spotify Ads admite un máximo de 90 días por consulta")
        account_payload = self.account(account_id)
        account = account_payload.get("data") if isinstance(account_payload.get("data"), dict) else account_payload
        currency = str(account.get("currency") or "MXN")

        totals_rows = self._report(account_id, "AD_ACCOUNT", start, end, "LIFETIME", currency)
        daily = self._report(account_id, "AD_ACCOUNT", start, end, "DAY", currency)
        campaigns = self._report(account_id, "CAMPAIGN", start, end, "LIFETIME", currency)
        adsets = self._report(account_id, "AD_SET", start, end, "LIFETIME", currency, include_parent=True)
        ads = self._report(account_id, "AD", start, end, "LIFETIME", currency, include_parent=True)
        # El padre inmediato de un AD es el AD_SET. Completa su campaña desde
        # las filas AD_SET obtenidas de la misma cuenta y el mismo periodo.
        campaign_by_adset = {
            str(row.get("ad_set_id")): (row.get("campaign_id"), row.get("campaign_name"))
            for row in adsets if row.get("ad_set_id")
        }
        for row in ads:
            campaign = campaign_by_adset.get(str(row.get("ad_set_id")))
            if campaign:
                row["campaign_id"], row["campaign_name"] = campaign
        totals = totals_rows[0] if totals_rows else derive({
            "impressions": 0, "clicks": 0, "spend": 0, "reach": 0,
            "conversions": None, "conversion_value": None,
        })
        return {
            "account_name": account.get("name") or "Santuario Residencial",
            "currency": currency,
            "totals": totals,
            "campaigns": campaigns,
            "adsets": adsets,
            "ads": ads,
            "daily": daily,
            "reach_is_sum": False,
        }

"""Pinterest Ads API v5. Tokens recibidos solo desde Secret Manager/runtime."""
from __future__ import annotations

import logging
import time
from datetime import date
from typing import Any

import requests

from ..config import Settings
from .base import ProviderError, ProviderNotConfigured, sanitize

log = logging.getLogger("mp.pinterest")
BASE_URL = "https://api.pinterest.com/v5"
MICRO = 1_000_000
ANALYTICS_COLUMNS = (
    # Pinterest includes campaign/date dimensions in each result row; they are
    # not selectable metric columns and sending them causes API HTTP 400.
    "SPEND_IN_MICRO_DOLLAR", "IMPRESSION_1",
    "CLICKTHROUGH_1", "TOTAL_CHECKOUT", "TOTAL_CHECKOUT_VALUE_IN_MICRO_DOLLAR",
)


class PinterestAuthError(ProviderError):
    """Pinterest rechazó el token configurado."""


class PinterestPermissionError(ProviderError):
    """El usuario/token no tiene permisos sobre la cuenta solicitada."""


class PinterestAdsProvider:
    platform = "pinterest_ads"

    def __init__(self, settings: Settings, session: requests.Session | None = None) -> None:
        self.settings = settings
        self.session = session or requests.Session()

    def _token(self) -> str:
        token = self.settings.pinterest_access_token
        if not token:
            raise ProviderNotConfigured("Pinterest Ads no está configurado en el servidor.")
        return token

    def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        for attempt in range(4):
            try:
                response = self.session.get(
                    f"{BASE_URL}/{path.lstrip('/')}",
                    params=params,
                    headers={"Authorization": f"Bearer {self._token()}", "Accept": "application/json"},
                    timeout=45,
                )
            except requests.RequestException as exc:
                raise ProviderError(f"Error de red con Pinterest Ads: {type(exc).__name__}") from None
            if response.status_code == 429 and attempt < 3:
                raw_wait = response.headers.get("Retry-After", "")
                try:
                    wait = float(raw_wait)
                except (TypeError, ValueError):
                    wait = 2 ** attempt
                time.sleep(max(0.25, min(wait, 8.0)))
                continue
            try:
                payload = response.json()
            except ValueError:
                payload = None
            if response.status_code == 401:
                raise PinterestAuthError("Pinterest Ads rechazó la autenticación (HTTP 401).")
            if response.status_code == 403:
                detail = payload.get("message") if isinstance(payload, dict) else None
                raise PinterestPermissionError(sanitize(f"Pinterest Ads rechazó los permisos (HTTP 403): {detail or ''}"))
            if response.status_code >= 400:
                detail = payload.get("message") or payload.get("code") if isinstance(payload, dict) else None
                raise ProviderError(sanitize(f"Pinterest Ads API HTTP {response.status_code}: {detail or 'sin detalle'}"))
            if payload is None:
                raise ProviderError("Pinterest Ads devolvió una respuesta JSON inválida.")
            return payload
        raise ProviderError("Pinterest Ads mantiene el límite de solicitudes (HTTP 429).")

    @staticmethod
    def _items(payload: Any) -> list[dict[str, Any]]:
        if isinstance(payload, list):
            return [row for row in payload if isinstance(row, dict)]
        if isinstance(payload, dict):
            for key in ("items", "data", "results"):
                rows = payload.get(key)
                if isinstance(rows, list):
                    return [row for row in rows if isinstance(row, dict)]
        return []

    def _pages(self, path: str, params: dict[str, Any]) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        next_params = dict(params)
        for _ in range(50):
            payload = self._get(path, next_params)
            rows.extend(self._items(payload))
            bookmark = payload.get("bookmark") if isinstance(payload, dict) else None
            if not bookmark:
                return rows
            next_params = {"bookmark": bookmark, "page_size": 100}
        raise ProviderError("Pinterest Ads excedió el límite seguro de páginas.")

    @staticmethod
    def _status(value: Any) -> str:
        status = str(value or "").strip().upper()
        if status in {"ACTIVE", "RUNNING", "ENABLED"}:
            return "ACTIVE"
        if status in {"PAUSED", "ARCHIVED", "DELETED", "INACTIVE", "DISABLED", "DRAFT"}:
            return status
        return "UNKNOWN"

    def list_accounts(self) -> list[dict[str, Any]]:
        rows = self._pages("ad_accounts", {"page_size": 100})
        return [{
            "platform": self.platform,
            "id": str(row.get("id") or row.get("ad_account_id") or ""),
            "name": row.get("name") or row.get("display_name"),
            "currency": row.get("currency"),
            "status": self._status(row.get("status")),
            "permissions": row.get("permissions") if isinstance(row.get("permissions"), list) else [],
            "account_type": "ad_account",
        } for row in rows if row.get("id") or row.get("ad_account_id")]

    def list_campaigns(self, account_id: str) -> list[dict[str, Any]]:
        rows = self._pages(f"ad_accounts/{account_id}/campaigns", {"page_size": 100})
        return [{
            "platform": self.platform,
            "ad_account_id": str(account_id),
            "campaign_id": str(row.get("id") or row.get("campaign_id") or ""),
            "campaign_name": row.get("name") or row.get("campaign_name"),
            "status": self._status(row.get("effective_status") or row.get("status")),
            "objective_type": row.get("objective_type"),
            "currency": row.get("currency"),
        } for row in rows if row.get("id") or row.get("campaign_id")]

    @staticmethod
    def _number(row: dict[str, Any], *keys: str, divisor: float = 1.0) -> float | None:
        value = next((row[k] for k in keys if k in row), None)
        if value is None:
            return None
        try:
            return float(value) / divisor
        except (TypeError, ValueError, OverflowError):
            return None

    @classmethod
    def _derive(cls, spend: float | None, impressions: float | None, clicks: float | None,
                checkouts: float | None, value: float | None) -> dict[str, Any]:
        ctr = clicks / impressions if clicks is not None and impressions not in (None, 0) else None
        cpc = spend / clicks if spend is not None and clicks not in (None, 0) else None
        cpa = spend / checkouts if spend is not None and checkouts not in (None, 0) else None
        roas = value / spend if value is not None and spend not in (None, 0) else None
        return {
            "spend": round(spend, 2) if spend is not None else None,
            "impressions": int(impressions) if impressions is not None else None,
            "clicks": int(clicks) if clicks is not None else None,
            "ctr": ctr,
            "cpc": cpc,
            "checkouts": checkouts,
            "conversions": None,
            "cpa": cpa,
            "cost_per_checkout": cpa,
            "conversion_value": round(value, 2) if value is not None else None,
            "roas": roas,
            "currency": None,
        }

    @classmethod
    def _aggregate(cls, rows: list[dict[str, Any]]) -> dict[str, Any]:
        def total(key: str) -> float | None:
            values = [row.get(key) for row in rows if row.get(key) is not None]
            return sum(values) if values else None
        return cls._derive(total("spend"), total("impressions"), total("clicks"),
                           total("checkouts"), total("conversion_value"))

    @staticmethod
    def _get_value(row: dict[str, Any], *keys: str) -> Any:
        for key in keys:
            if key in row:
                return row[key]
        lower = {str(key).lower(): value for key, value in row.items()}
        return next((lower[key.lower()] for key in keys if key.lower() in lower), None)

    @classmethod
    def _normalize_analytics_row(cls, row: dict[str, Any]) -> dict[str, Any]:
        return {
            "spend": cls._number(row, "SPEND_IN_MICRO_DOLLAR", "spend_in_micro_dollar", divisor=MICRO),
            "impressions": cls._number(row, "IMPRESSION_1", "impression_1"),
            "clicks": cls._number(row, "CLICKTHROUGH_1", "clickthrough_1"),
            "checkouts": cls._number(row, "TOTAL_CHECKOUT", "total_checkout"),
            "conversion_value": cls._number(
                row, "TOTAL_CHECKOUT_VALUE_IN_MICRO_DOLLAR", "total_checkout_value_in_micro_dollar", divisor=MICRO
            ),
        }

    def performance(self, account_id: str, start: str, end: str,
                    campaign_ids: list[str] | None = None) -> dict[str, Any]:
        # The ad-account analytics endpoint is the source of truth for the
        # summary and daily series. Campaign analytics is a separate optional
        # batch that enriches campaign rows without blocking the summary.
        catalog: list[dict[str, Any]] = []
        catalog_warning: str | None = None
        try:
            catalog = self.list_campaigns(account_id)
        except ProviderError as exc:
            catalog_warning = sanitize(exc)

        requested_ids = [str(value) for value in campaign_ids] if campaign_ids else [
            str(row["campaign_id"]) for row in catalog if row.get("campaign_id")
        ]

        base_params: dict[str, Any] = {
            "start_date": start,
            "end_date": end,
            "columns": ",".join(ANALYTICS_COLUMNS),
        }

        # Request the account aggregate first so all KPI cards are available
        # even when the daily or campaign detail endpoints have a partial error.
        total_params = {**base_params, "granularity": "TOTAL"}
        total_rows = self._items(self._get(f"ad_accounts/{account_id}/analytics", total_params))
        totals = self._aggregate([self._normalize_analytics_row(row) for row in total_rows])

        catalog_by_id = {str(row["campaign_id"]): row for row in catalog if row.get("campaign_id")}
        total_by_campaign: dict[str, dict[str, Any]] = {}
        warnings: list[dict[str, Any]] = []
        if catalog_warning:
            warnings.append({"status": "campaign_catalog_partial", "message": "No fue posible cargar el catálogo completo de campañas."})

        if requested_ids:
            campaign_params = {
                **base_params,
                "campaign_ids": ",".join(requested_ids),
                "granularity": "TOTAL",
            }
            try:
                campaign_rows = self._items(
                    self._get(f"ad_accounts/{account_id}/campaigns/analytics", campaign_params)
                )
                for row in campaign_rows:
                    campaign_id = self._get_value(row, "CAMPAIGN_ID", "campaign_id", "CAMPAIGN_ID__")
                    if campaign_id:
                        total_by_campaign[str(campaign_id)] = self._normalize_analytics_row(row)
            except ProviderError as exc:
                warnings.append({
                    "status": "campaign_analytics_partial",
                    "message": "El resumen de Pinterest está disponible; no se pudo completar el detalle de campañas.",
                    "detail": sanitize(exc),
                })

        daily: list[dict[str, Any]] = []
        try:
            daily_params = {**base_params, "granularity": "DAY"}
            daily_rows = self._items(self._get(f"ad_accounts/{account_id}/analytics", daily_params))
        except ProviderError as exc:
            warnings.append({
                "status": "daily_series_partial",
                "message": "El resumen de Pinterest está disponible; no se pudo cargar la serie diaria.",
                "detail": sanitize(exc),
            })
            daily_rows = []
        for row in daily_rows:
            day = self._get_value(row, "DATE", "date", "DAY")
            if not day:
                continue
            daily.append({"date": str(day), **self._normalize_analytics_row(row)})
        daily.sort(key=lambda row: row["date"])

        campaigns = []
        for campaign_id in requested_ids:
            meta = catalog_by_id.get(campaign_id, {})
            metric = total_by_campaign.get(campaign_id, {})
            summary = self._derive(
                spend=metric.get("spend"),
                impressions=metric.get("impressions"),
                clicks=metric.get("clicks"),
                checkouts=metric.get("checkouts"),
                value=metric.get("conversion_value"),
            )
            campaigns.append({
                **summary,
                "platform": self.platform,
                "campaign_id": campaign_id,
                "campaign_name": meta.get("campaign_name"),
                "status": meta.get("status") or "UNKNOWN",
                "effective_status": meta.get("status") or "UNKNOWN",
                "objective_type": meta.get("objective_type"),
                "currency": meta.get("currency"),
            })
        currencies = {str(row.get("currency")) for row in catalog if row.get("currency")}
        currency = next(iter(currencies)) if len(currencies) == 1 else None
        for row in campaigns:
            row["currency"] = row.get("currency") or currency
        totals["currency"] = currency
        return {
            "currency": currency,
            "totals": totals,
            "campaigns": campaigns,
            "daily": daily,
            "sources_count": 1,
            "warnings": warnings,
        }

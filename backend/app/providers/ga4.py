"""Google Analytics 4 (Data API + Admin API) vía ADC ya configuradas. Solo lectura.

Las credenciales son propias de la plataforma: ADC pedidas con el scope
`analytics.readonly` y pasadas explícitamente a los dos clientes. No se
comparten con Google Ads (que usa su propia configuración y el scope
`adwords`): el token de la cuenta de servicio de Cloud Run sin
`analytics.readonly` provoca 403 ACCESS_TOKEN_SCOPE_INSUFFICIENT.
"""
from __future__ import annotations

import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from ..config import Settings
from .base import ProviderError, sanitize

log = logging.getLogger("mp.ga4")

# Scope mínimo de lectura de GA4 (Admin API v1beta + Data API v1beta).
GA4_SCOPES = ["https://www.googleapis.com/auth/analytics.readonly"]

METRICS = ["activeUsers", "sessions", "engagedSessions", "engagementRate", "keyEvents", "totalRevenue"]
BREAKDOWNS = {
    "by_source_medium": "sessionSourceMedium",
    "by_campaign": "sessionCampaignName",
    "by_landing_page": "landingPagePlusQueryString",
}


def _num(v: str) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def _row_metrics(values: list[str]) -> dict[str, float]:
    """Traduce una fila de la Data API al modelo del dashboard.

    `users` es `activeUsers`: NO es aditivo entre propiedades (un usuario puede
    aparecer en varias), así que al consolidar se suma y se etiqueta como suma.
    """
    m = {name: 0.0 for name in METRICS} | {name: _num(v) for name, v in zip(METRICS, values)}
    return {
        "users": int(m["activeUsers"]),
        "sessions": int(m["sessions"]),
        "engaged_sessions": int(m["engagedSessions"]),
        "engagement_rate": m["engagementRate"],
        "key_events": m["keyEvents"],
        "revenue": round(m["totalRevenue"], 2),
    }


class GA4Provider:
    platform = "ga4"

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._data = None
        self._admin = None
        self._credentials = None
        self._lock = threading.Lock()
        self._currency: dict[str, str | None] = {}
        # property_id → nombre para mostrar en la vista "Por propiedad".
        self._names: dict[str, str | None] = {}

    def _ga4_credentials(self):
        """ADC con el scope analytics.readonly. Se llama con self._lock tomado."""
        if self._credentials is None:
            import google.auth

            try:
                credentials, project_id = google.auth.default(scopes=GA4_SCOPES)
            except Exception as e:
                raise ProviderError(f"No se pudieron obtener credenciales ADC para GA4: {sanitize(e)}") from e
            log.info("GA4: ADC con scope analytics.readonly (proyecto %s)", project_id)
            self._credentials = credentials
        return self._credentials

    def _data_client(self):
        with self._lock:
            if self._data is None:
                try:
                    from google.analytics.data_v1beta import BetaAnalyticsDataClient
                    self._data = BetaAnalyticsDataClient(credentials=self._ga4_credentials())
                except ProviderError:
                    raise
                except Exception as e:
                    raise ProviderError(f"No se pudo inicializar GA4 Data API con ADC: {sanitize(e)}") from e
            return self._data

    def _admin_client(self):
        with self._lock:
            if self._admin is None:
                try:
                    from google.analytics.admin_v1beta import AnalyticsAdminServiceClient
                    self._admin = AnalyticsAdminServiceClient(credentials=self._ga4_credentials())
                except ProviderError:
                    raise
                except Exception as e:
                    raise ProviderError(f"No se pudo inicializar GA4 Admin API con ADC: {sanitize(e)}") from e
            return self._admin

    # ------------------------------------------------------------------ cuentas
    def list_accounts(self) -> list[dict[str, Any]]:
        """Todas las cuentas y todas sus propiedades vía Admin API ListAccountSummaries.

        Recorre explícitamente las páginas: una cuenta puede tener N propiedades y
        la respuesta puede venir paginada. Cada propiedad se devuelve con su cuenta
        para poder mostrar "Cuenta → Propiedad — ID" en los selectores.
        """
        admin = self._admin_client()
        try:
            from google.analytics.admin_v1beta.types import ListAccountSummariesRequest

            pager = admin.list_account_summaries(request=ListAccountSummariesRequest(page_size=200))
            summaries = []
            pages = 0
            for page in pager.pages:  # sigue next_page_token hasta agotarlo
                pages += 1
                summaries.extend(page.account_summaries)
        except Exception as e:
            raise ProviderError(sanitize(e)) from e

        props = []
        for acc in summaries:
            account_id = acc.account.split("/")[-1]
            for p in acc.property_summaries:
                property_id = p.property.split("/")[-1]
                props.append({
                    "platform": "ga4",
                    # id/name: claves que usan el registro y los selectores.
                    "id": property_id,
                    "name": p.display_name,
                    "property_id": property_id,
                    "property_name": p.display_name,
                    "property_resource": p.property,          # "properties/123456789"
                    "account_id": account_id,
                    "account_name": acc.display_name,
                    "account_resource": acc.account,          # "accounts/987654"
                    "property_type": _enum(p.property_type).replace("PROPERTY_TYPE_", "").lower(),
                    "account_type": _enum(p.property_type).replace("PROPERTY_TYPE_", "").lower(),
                    "currency": None, "timezone": None, "status": "ACTIVE",
                })
                self._names[property_id] = p.display_name
        log.info("GA4: %d cuentas y %d propiedades en %d página(s) de ListAccountSummaries",
                 len(summaries), len(props), pages)

        def enrich(p: dict[str, Any]) -> dict[str, Any]:
            try:
                d = admin.get_property(name=f"properties/{p['id']}")
                p["currency"] = d.currency_code or None
                p["timezone"] = d.time_zone or None
                self._currency[p["id"]] = p["currency"]
            except Exception as e:  # sin permiso de lectura de detalles: se deja sin datos
                p["status"] = "LIMITED"
                p["error"] = sanitize(e, 160)
            return p

        with ThreadPoolExecutor(max_workers=8) as pool:
            props = list(pool.map(enrich, props))
        return sorted(props, key=lambda p: ((p["account_name"] or "").lower(), (p["name"] or "").lower()))

    def _property_details(self, property_id: str) -> tuple[str | None, str | None]:
        """(moneda, nombre) de la propiedad. Best-effort: sin permiso, (None, None)."""
        if property_id not in self._currency:
            try:
                d = self._admin_client().get_property(name=f"properties/{property_id}")
                self._currency[property_id] = d.currency_code or None
                self._names.setdefault(property_id, d.display_name or None)
            except Exception:
                self._currency[property_id] = None
        return self._currency[property_id], self._names.get(property_id)

    # -------------------------------------------------------------- rendimiento
    def performance(self, property_id: str, start: str, end: str) -> dict[str, Any]:
        from google.analytics.data_v1beta.types import (
            BatchRunReportsRequest, DateRange, Dimension, Metric, OrderBy, RunReportRequest,
        )

        prop = f"properties/{property_id}"
        rng = [DateRange(start_date=start, end_date=end)]
        metrics = [Metric(name=m) for m in METRICS]
        by_sessions = [OrderBy(metric=OrderBy.MetricOrderBy(metric_name="sessions"), desc=True)]
        requests_ = [
            RunReportRequest(property=prop, date_ranges=rng, metrics=metrics),
            RunReportRequest(property=prop, date_ranges=rng, metrics=metrics, dimensions=[Dimension(name="date")],
                             order_bys=[OrderBy(dimension=OrderBy.DimensionOrderBy(dimension_name="date"))], limit=1000),
            *[
                RunReportRequest(property=prop, date_ranges=rng, metrics=metrics, dimensions=[Dimension(name=dim)],
                                 order_bys=by_sessions, limit=50)
                for dim in BREAKDOWNS.values()
            ],
        ]
        try:
            resp = self._data_client().batch_run_reports(BatchRunReportsRequest(property=prop, requests=requests_))
        except Exception as e:
            raise ProviderError(sanitize(getattr(e, "message", None) or e)) from e

        totals_r, daily_r, *breakdown_r = resp.reports
        totals = _row_metrics([v.value for v in totals_r.rows[0].metric_values]) if totals_r.rows else _row_metrics([])
        daily = []
        for row in daily_r.rows:
            d = row.dimension_values[0].value  # YYYYMMDD
            daily.append({"date": f"{d[:4]}-{d[4:6]}-{d[6:]}", **_row_metrics([v.value for v in row.metric_values])})
        currency, name = self._property_details(property_id)
        out: dict[str, Any] = {
            "property_id": property_id,
            "property_name": name,
            "currency": currency,
            "totals": totals,
            "daily": daily,
        }
        for key, report in zip(BREAKDOWNS, breakdown_r):
            out[key] = [{"name": r.dimension_values[0].value, **_row_metrics([v.value for v in r.metric_values])} for r in report.rows]
        return out


def _enum(v: Any) -> str:
    return getattr(v, "name", str(v))

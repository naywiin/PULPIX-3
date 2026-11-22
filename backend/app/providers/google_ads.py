"""Google Ads API vía Application Default Credentials (ADC) ya configuradas.

No crea credenciales: usa las ADC del servidor (gcloud auth application-default).
Si existe GOOGLE_ADS_DEVELOPER_TOKEN en el entorno se agrega a la config.
Solo consultas de lectura (GoogleAdsService.search / CustomerService.list).
"""
from __future__ import annotations

import logging
import threading
from collections import defaultdict
from typing import Any

from ..config import Settings
from ..metrics import derive, empty_base
from .base import ProviderError, sanitize

log = logging.getLogger("mp.google_ads")

CAMPAIGN_QUERY = """
SELECT
  campaign.id,
  campaign.name,
  campaign.status,
  campaign.advertising_channel_type,
  segments.date,
  customer.currency_code,
  metrics.impressions,
  metrics.clicks,
  metrics.ctr,
  metrics.average_cpc,
  metrics.cost_micros,
  metrics.conversions,
  metrics.all_conversions,
  metrics.conversions_value,
  metrics.all_conversions_value
FROM campaign
WHERE segments.date BETWEEN '{start}' AND '{end}'
"""

# Catálogo de campañas de una cuenta (para asignarlas a un cliente). Sin
# métricas: es una lista para elegir, no un informe.
CAMPAIGN_LIST_QUERY = """
SELECT
  campaign.id,
  campaign.name,
  campaign.status,
  campaign.advertising_channel_type,
  customer.id,
  customer.descriptive_name,
  customer.currency_code
FROM campaign
ORDER BY campaign.name
"""

CUSTOMER_QUERY = """
SELECT customer.id, customer.descriptive_name, customer.manager, customer.currency_code,
       customer.time_zone, customer.status, customer.test_account
FROM customer LIMIT 1
"""

AD_CATALOG_QUERY = """
SELECT
  campaign.id,
  campaign.name,
  campaign.status,
  ad_group.id,
  ad_group.name,
  ad_group_ad.ad.id,
  ad_group_ad.ad.name,
  ad_group_ad.ad.type,
  ad_group_ad.status,
  customer.currency_code
FROM ad_group_ad
"""

AD_PERFORMANCE_QUERY = """
SELECT
  campaign.id,
  campaign.name,
  campaign.status,
  ad_group.id,
  ad_group.name,
  ad_group_ad.ad.id,
  ad_group_ad.ad.name,
  ad_group_ad.ad.type,
  ad_group_ad.status,
  customer.currency_code,
  metrics.impressions,
  metrics.clicks,
  metrics.cost_micros,
  metrics.conversions,
  metrics.conversions_value
FROM ad_group_ad
WHERE segments.date BETWEEN '{start}' AND '{end}'
"""

# Only primary conversion actions that represent first-touch acquisition intent.
# Qualified/converted lead categories describe later pipeline stages and are
# deliberately excluded to prevent counting one lead as multiple acquisitions.
LEAD_CONVERSION_CATEGORIES = {
    "LEAD", "SUBMIT_LEAD_FORM", "PHONE_CALL_LEAD", "CONTACT",
    "BOOK_APPOINTMENT", "REQUEST_QUOTE",
}
LEAD_ACTIONS_QUERY = """
SELECT conversion_action.resource_name, conversion_action.category,
       conversion_action.primary_for_goal, conversion_action.status
FROM conversion_action
WHERE conversion_action.status != 'REMOVED'
"""

CAMPAIGN_CONVERSION_ACTION_QUERY = """
SELECT campaign.id, segments.date, segments.conversion_action, metrics.conversions
FROM campaign
WHERE segments.date BETWEEN '{start}' AND '{end}'
"""

# Jerarquía completa del MCC raíz: clientes directos (level 1) e indirectos
# (level >= 2, colgando de un MCC secundario). Es la fuente de verdad de las
# cuentas; list_accessible_customers() solo ve lo que se compartió directamente
# con la identidad que autentica (en Cloud Run, la cuenta de servicio).
HIERARCHY_QUERY = """
SELECT
  customer_client.id,
  customer_client.descriptive_name,
  customer_client.manager,
  customer_client.level,
  customer_client.status,
  customer_client.currency_code,
  customer_client.time_zone,
  customer_client.test_account,
  customer_client.hidden
FROM customer_client
"""


def _enum_name(v: Any) -> str:
    return getattr(v, "name", str(v))


def _digits_only(value: Any, field: str) -> str:
    """Solo dígitos: estos valores se interpolan en GAQL, nunca aceptar otra cosa."""
    v = str(value).replace("-", "").strip()
    if not v.isdigit():
        raise ProviderError(f"{field} inválido: se esperaba un ID numérico")
    return v


class GoogleAdsProvider:
    platform = "google_ads"

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._clients: dict[str | None, Any] = {}
        self._lock = threading.Lock()

    def _client(self, login_customer_id: str | None = None):
        with self._lock:
            if login_customer_id not in self._clients:
                try:
                    from google.ads.googleads.client import GoogleAdsClient
                except ImportError as e:  # pragma: no cover
                    raise ProviderError("Librería google-ads no instalada en el backend") from e
                config: dict[str, Any] = {"use_application_default_credentials": True, "use_proto_plus": True}
                dev = self.settings.google_ads_developer_token
                if dev:
                    config["developer_token"] = dev
                if login_customer_id:
                    config["login_customer_id"] = login_customer_id
                try:
                    self._clients[login_customer_id] = GoogleAdsClient.load_from_dict(config)
                except Exception as e:
                    raise ProviderError(f"No se pudo inicializar Google Ads con ADC: {sanitize(e)}") from e
            return self._clients[login_customer_id]

    def _search(self, customer_id: str, query: str, login_customer_id: str | None = None):
        client = self._client(login_customer_id)
        service = client.get_service("GoogleAdsService")
        try:
            for batch in service.search_stream(customer_id=customer_id, query=query):
                yield from batch.results
        except Exception as e:
            raise ProviderError(_format_error(e)) from e

    # ------------------------------------------------------------------ cuentas
    @property
    def manager_customer_id(self) -> str:
        """MCC raíz de la agencia (fuente de la jerarquía y login-customer-id)."""
        return self.settings.google_ads_manager_customer_id

    def _hierarchy_rows(self) -> list[Any]:
        """Todas las filas de customer_client del MCC raíz, sin filtrar."""
        mcc = self.manager_customer_id
        return [r.customer_client for r in self._search(mcc, HIERARCHY_QUERY, login_customer_id=mcc)]

    def _account(self, cc: Any) -> dict[str, Any]:
        mcc = self.manager_customer_id
        cid = str(cc.id)
        level = int(cc.level)
        return {
            "platform": "google_ads",
            "id": cid,
            "name": cc.descriptive_name or None,
            "currency": cc.currency_code or None,
            "timezone": cc.time_zone or None,
            "status": _enum_name(cc.status),
            # manager = MCC secundario; client = cuenta anunciante.
            "account_type": "manager" if cc.manager else "client",
            "level": level,
            "test_account": bool(cc.test_account),
            "hidden": bool(cc.hidden),
            # Toda consulta a una cuenta de la jerarquía viaja con el MCC raíz en
            # login-customer-id: sin él la API rechaza las cuentas cliente.
            "login_customer_id": mcc,
            "access": "MCC raíz" if level == 0 else f"vía MCC {mcc} · nivel {level}",
        }

    def list_accounts(self, include_hidden: bool = False, include_root: bool = False) -> list[dict[str, Any]]:
        """Jerarquía del MCC raíz: clientes directos (nivel 1) e indirectos (nivel ≥ 2).

        No depende de list_accessible_customers() (que solo ve lo compartido con
        la identidad que autentica). Por defecto excluye el propio MCC (nivel 0)
        y las cuentas ocultas.
        """
        accounts: dict[str, dict[str, Any]] = {}
        for cc in self._hierarchy_rows():
            if not include_root and int(cc.level) == 0:
                continue
            if not include_hidden and cc.hidden:
                continue
            a = self._account(cc)
            prev = accounts.get(a["id"])
            # Una cuenta puede aparecer por varias rutas: gana la más corta.
            if prev is None or a["level"] < prev["level"]:
                accounts[a["id"]] = a
        managers = sum(1 for a in accounts.values() if a["account_type"] == "manager")
        log.info(
            "Jerarquía del MCC %s: %d cuentas (%d manager, %d anunciantes)",
            self.manager_customer_id, len(accounts), managers, len(accounts) - managers,
        )
        return sorted(accounts.values(), key=lambda a: ((a["status"] != "ENABLED"), (a["name"] or "").lower()))

    def diagnostics(self) -> dict[str, Any]:
        """Depuración de accesos (no es la fuente de clientes): resume la
        jerarquía del MCC y, aparte, qué ve list_accessible_customers()."""
        out: dict[str, Any] = {
            "manager_customer_id": self.manager_customer_id,
            "developer_token_present": bool(self.settings.google_ads_developer_token),
        }
        try:
            rows = self._hierarchy_rows()
        except ProviderError as e:
            out["hierarchy"] = {"status": "error", "detail": str(e)}
        else:
            by_level: dict[int, int] = defaultdict(int)
            for cc in rows:
                by_level[int(cc.level)] += 1
            visible = [cc for cc in rows if not cc.hidden and int(cc.level) > 0]
            out["hierarchy"] = {
                "status": "ok",
                "rows": len(rows),
                "by_level": dict(sorted(by_level.items())),
                "hidden": sum(1 for cc in rows if cc.hidden),
                "returned": len(visible),
                "managers": sum(1 for cc in visible if cc.manager),
                "advertisers": sum(1 for cc in visible if not cc.manager),
                "test_accounts": sum(1 for cc in visible if cc.test_account),
                "by_status": _counts(_enum_name(cc.status) for cc in visible),
            }
        try:
            names = self._client().get_service("CustomerService").list_accessible_customers().resource_names
        except Exception as e:
            out["accessible_customers"] = {"status": "error", "detail": _format_error(e)}
        else:
            out["accessible_customers"] = {
                "status": "ok",
                "count": len(names),
                "ids": [n.split("/")[-1] for n in names],
                "note": "Solo diagnóstico: la lista de clientes sale de customer_client en el MCC raíz.",
            }
        # El estado global lo marca la jerarquía: es la fuente real de clientes.
        out["status"] = "ok" if out["hierarchy"].get("status") == "ok" else "error"
        return out

    # -------------------------------------------------------------- rendimiento
    def list_campaigns(self, customer_id: str, login_customer_id: str | None = None) -> list[dict[str, Any]]:
        """Campañas reales de una cuenta, para elegirlas en Settings.

        No se inventa ninguna: salen de la API con el mismo MCC/login del resto
        del proveedor.
        """
        customer_id = _digits_only(customer_id, "customer_id")
        login_customer_id = login_customer_id or self.manager_customer_id
        out: list[dict[str, Any]] = []
        for row in self._search(customer_id, CAMPAIGN_LIST_QUERY, login_customer_id):
            out.append({
                "platform": "google_ads",
                "customer_id": str(row.customer.id),
                "account_name": row.customer.descriptive_name or None,
                "currency": row.customer.currency_code or None,
                "campaign_id": str(row.campaign.id),
                "campaign_name": row.campaign.name,
                "status": _enum_name(row.campaign.status),
                "advertising_channel_type": _enum_name(row.campaign.advertising_channel_type),
            })
        log.info("Google Ads: %d campañas en la cuenta %s", len(out), customer_id)
        return out

    def performance(self, customer_id: str, login_customer_id: str | None, start: str, end: str,
                    campaign_ids: list[str] | None = None) -> dict[str, Any]:
        """Agrega por campaña y por día. start/end ya vienen validados como YYYY-MM-DD.

        `campaign_ids` restringe la consulta a esas campañas (filtro en GAQL, no
        en memoria: no se traen datos que no se van a usar). Vacío/None = todas.
        """
        query = CAMPAIGN_QUERY.format(start=start, end=end)
        if campaign_ids:
            # Los IDs se validan como dígitos antes de interpolarse en GAQL.
            ids = ", ".join(_digits_only(c, "campaign_id") for c in campaign_ids)
            query += f"  AND campaign.id IN ({ids})\n"
        # Las cuentas de la jerarquía se consultan siempre a través del MCC raíz.
        login_customer_id = login_customer_id or self.manager_customer_id
        by_campaign: dict[str, dict[str, Any]] = {}
        by_day: dict[str, dict[str, float]] = defaultdict(lambda: empty_base())
        by_campaign_day: dict[tuple[str, str], dict[str, float]] = defaultdict(lambda: empty_base())
        currency = None
        fields = ("impressions", "clicks", "spend", "conversions", "conversion_value", "all_conversions", "all_conversions_value")

        for row in self._search(customer_id, query, login_customer_id):
            m = row.metrics
            currency = currency or row.customer.currency_code
            vals = {
                "impressions": m.impressions,
                "clicks": m.clicks,
                "spend": m.cost_micros / 1_000_000,
                "conversions": m.conversions,
                "conversion_value": m.conversions_value,
                "all_conversions": m.all_conversions,
                "all_conversions_value": m.all_conversions_value,
            }
            cid = str(row.campaign.id)
            day_key = str(row.segments.date)
            camp = by_campaign.setdefault(cid, {
                "campaign_id": cid,
                "campaign_name": row.campaign.name,
                "status": _enum_name(row.campaign.status),
                "channel": _enum_name(row.campaign.advertising_channel_type),
                **empty_base(all_conversions=0.0, all_conversions_value=0.0),
            })
            day = by_day[day_key]
            campaign_day = by_campaign_day[(cid, day_key)]
            for f in fields:
                camp[f] += vals[f]
                day[f] = day.get(f, 0) + vals[f]
                campaign_day[f] = campaign_day.get(f, 0) + vals[f]

        # `metrics.conversions` above remains the platform's conversion count.
        # Leads are a separate, explicit acquisition-action breakdown.
        leads_by_campaign: dict[str, float] = defaultdict(float)
        leads_by_day: dict[str, float] = defaultdict(float)
        leads_by_campaign_day: dict[tuple[str, str], float] = defaultdict(float)
        leads_available = False
        try:
            lead_action_names = {
                str(row.conversion_action.resource_name)
                for row in self._search(customer_id, LEAD_ACTIONS_QUERY, login_customer_id)
                if bool(row.conversion_action.primary_for_goal)
                and _enum_name(row.conversion_action.category) in LEAD_CONVERSION_CATEGORIES
            }
            if lead_action_names:
                leads_available = True
                action_query = CAMPAIGN_CONVERSION_ACTION_QUERY.format(start=start, end=end)
                if campaign_ids:
                    ids = ", ".join(_digits_only(c, "campaign_id") for c in campaign_ids)
                    action_query += f" AND campaign.id IN ({ids})\n"
                for row in self._search(customer_id, action_query, login_customer_id):
                    action_name = str(row.segments.conversion_action)
                    if action_name not in lead_action_names:
                        continue
                    campaign_id = str(row.campaign.id)
                    count = float(row.metrics.conversions)
                    leads_by_campaign[campaign_id] += count
                    action_day = str(row.segments.date)
                    leads_by_day[action_day] += count
                    leads_by_campaign_day[(campaign_id, action_day)] += count
        except Exception as exc:
            # A lead-breakdown failure must not hide spend/conversion reporting.
            log.warning("Google Ads: desglose de leads no disponible (%s)", type(exc).__name__)
            leads_available = False

        campaigns = []
        for campaign_id, campaign in by_campaign.items():
            if not (campaign["impressions"] or campaign["clicks"] or campaign["spend"]
                    or campaign["conversions"] or leads_by_campaign.get(campaign_id, 0)):
                continue
            campaign["leads"] = leads_by_campaign.get(campaign_id, 0.0) if leads_available else None
            campaigns.append(derive(campaign))
        totals = empty_base(all_conversions=0.0, all_conversions_value=0.0)
        for c in campaigns:
            for f in fields:
                totals[f] += c[f]
        totals["leads"] = sum(leads_by_campaign.values()) if leads_available else None
        daily = [{"date": d, **derive({**v, "leads": leads_by_day.get(d, 0.0) if leads_available else None})} for d, v in sorted(by_day.items())]
        campaign_daily = []
        for campaign_id, day in sorted(by_campaign_day):
            values = by_campaign_day[(campaign_id, day)]
            campaign_daily.append({
                "campaign_id": campaign_id,
                "date": day,
                "spend": values.get("spend", 0.0),
                "conversions": values.get("conversions", 0.0),
                "conversion_value": values.get("conversion_value", 0.0),
                "leads": leads_by_campaign_day.get((campaign_id, day), 0.0) if leads_available else None,
            })
        return {"currency": currency, "totals": derive(totals), "campaigns": campaigns, "daily": daily,
                "campaign_daily": campaign_daily}

    def detail_ads(self, customer_id: str, login_customer_id: str | None, start: str, end: str,
                   campaign_ids: list[str] | None = None) -> list[dict[str, Any]]:
        """Catálogo actual de anuncios unido a métricas agregadas del periodo.

        Un anuncio sin fila de métricas en una consulta exitosa tiene cero entrega
        durante ese rango; el catálogo conserva anuncios activos sin gasto.
        """
        customer_id = _digits_only(customer_id, "customer_id")
        login_customer_id = login_customer_id or self.manager_customer_id
        campaign_filter = ""
        if campaign_ids is not None:
            if not campaign_ids:
                return []
            ids = ", ".join(_digits_only(value, "campaign_id") for value in campaign_ids)
            campaign_filter = f"campaign.id IN ({ids})"

        ads: dict[str, dict[str, Any]] = {}
        catalog_query = AD_CATALOG_QUERY + (f"WHERE {campaign_filter}" if campaign_filter else "")
        for row in self._search(customer_id, catalog_query, login_customer_id):
            if campaign_ids is not None and str(row.campaign.id) not in campaign_ids:
                continue
            ad_id = str(row.ad_group_ad.ad.id)
            ads[ad_id] = {
                "ad_id": ad_id,
                "ad_name": row.ad_group_ad.ad.name or None,
                "ad_type": _enum_name(row.ad_group_ad.ad.type),
                "ad_status": _enum_name(row.ad_group_ad.status),
                "status": _enum_name(row.ad_group_ad.status),
                "campaign_id": str(row.campaign.id),
                "campaign_name": row.campaign.name,
                "campaign_status": _enum_name(row.campaign.status),
                "ad_group_id": str(row.ad_group.id),
                "ad_group_name": row.ad_group.name,
                "currency": row.customer.currency_code or None,
                "source_account_id": customer_id,
            }

        metrics: dict[str, dict[str, Any]] = {}
        currency = None
        query = AD_PERFORMANCE_QUERY.format(start=start, end=end)
        if campaign_filter:
            query += f" AND {campaign_filter}"
        for row in self._search(customer_id, query, login_customer_id):
            if campaign_ids is not None and str(row.campaign.id) not in campaign_ids:
                continue
            item = row.ad_group_ad
            ad_id = str(item.ad.id)
            values = {
                "impressions": int(row.metrics.impressions),
                "clicks": int(row.metrics.clicks),
                "spend": float(row.metrics.cost_micros) / 1_000_000,
                "conversions": float(row.metrics.conversions),
                "conversion_value": float(row.metrics.conversions_value),
                "leads": None,
            }
            metrics[ad_id] = values
            currency = currency or row.customer.currency_code

        lead_counts: dict[str, float] = defaultdict(float)
        leads_available = False
        try:
            lead_action_names = {
                str(row.conversion_action.resource_name)
                for row in self._search(customer_id, LEAD_ACTIONS_QUERY, login_customer_id)
                if bool(row.conversion_action.primary_for_goal)
                and _enum_name(row.conversion_action.category) in LEAD_CONVERSION_CATEGORIES
            }
            if lead_action_names:
                leads_available = True
                lead_query = f"""
SELECT ad_group_ad.ad.id, segments.conversion_action, metrics.conversions
FROM ad_group_ad
WHERE segments.date BETWEEN '{start}' AND '{end}'
"""
                if campaign_filter:
                    lead_query += f" AND {campaign_filter}"
                for row in self._search(customer_id, lead_query, login_customer_id):
                    if str(row.segments.conversion_action) in lead_action_names:
                        lead_counts[str(row.ad_group_ad.ad.id)] += float(row.metrics.conversions)
        except Exception as exc:
            log.warning("Google Ads: desglose de leads por anuncio no disponible (%s)", type(exc).__name__)

        out = []
        for ad_id, ad in ads.items():
            base = metrics.get(ad_id) or {
                "impressions": 0, "clicks": 0, "spend": 0.0,
                "conversions": 0.0, "conversion_value": 0.0, "leads": None,
            }
            base["leads"] = lead_counts.get(ad_id, 0.0) if leads_available else None
            out.append({**ad, **derive(base), "currency": currency or ad.get("currency")})
        return out


def _counts(values: Any) -> dict[str, int]:
    out: dict[str, int] = defaultdict(int)
    for v in values:
        out[v] += 1
    return dict(sorted(out.items()))


def _format_error(e: Exception) -> str:
    failure = getattr(e, "failure", None)
    if failure is not None and getattr(failure, "errors", None):
        msgs = "; ".join(err.message for err in failure.errors[:3])
        req = getattr(e, "request_id", None)
        return sanitize(f"{msgs}" + (f" (request_id {req})" if req else ""))
    return sanitize(f"{type(e).__name__}: {e}")

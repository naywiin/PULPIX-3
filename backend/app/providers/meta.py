"""Meta Marketing API (Graph API) — solo lectura.

El token se lee de la variable de entorno META_ACCESS_TOKEN en cada petición y
se envía en el header Authorization (no en la URL), así nunca aparece en URLs,
mensajes de excepción ni logs. Nunca se escribe en archivos ni se imprime.
"""
from __future__ import annotations

import json
import logging
from math import isfinite
from typing import Any

import requests

from ..config import Settings
from ..ads_consolidate import merge_campaigns, merge_daily, sum_rows
from ..metrics import derive, empty_base
from .base import ProviderError, ProviderNotConfigured, sanitize

log = logging.getLogger("mp.meta")

# Prioridad histórica para las cuentas sin una conversión de compra propia.
PURCHASE_TYPES = (
    "omni_purchase",
    "purchase",
    "offsite_conversion.fb_pixel_purchase",
    "onsite_web_purchase",
    "onsite_web_app_purchase",
    "app_custom_event.fb_mobile_purchase",
)

# La cuenta Meta exclusiva de Cirque JOYÀ usa una conversión personalizada
# llamada "purchase", cuya regla corresponde al evento purchase. El ID está
# confirmado en /customconversions. Ninguna otra cuenta hereda esta prioridad.
PURCHASE_ACTION_PRIORITY_BY_ACCOUNT = {
    "2048742145193546": ("offsite_conversion.custom.1366991912161544", *PURCHASE_TYPES),
}


def purchase_action_priority(account_id: str | None) -> tuple[str, ...]:
    return PURCHASE_ACTION_PRIORITY_BY_ACCOUNT.get(str(account_id), PURCHASE_TYPES)

# Meta entrega acciones de lead separadas de las compras. Se elige una variante
# por prioridad para evitar sumar acciones equivalentes reportadas varias veces.
LEAD_TYPES = (
    "omni_lead", "lead", "onsite_conversion.lead_grouped",
    "offsite_conversion.fb_pixel_lead", "onsite_web_lead",
    "onsite_conversion.lead",
)

OBJECTIVE_MAP = {
    "OUTCOME_SALES": "SALES", "CONVERSIONS": "SALES", "PRODUCT_CATALOG_SALES": "SALES", "STORE_VISITS": "SALES",
    "OUTCOME_LEADS": "LEADS", "LEAD_GENERATION": "LEADS", "MESSAGES": "LEADS",
    "OUTCOME_TRAFFIC": "TRAFFIC", "LINK_CLICKS": "TRAFFIC",
    "OUTCOME_AWARENESS": "AWARENESS", "REACH": "AWARENESS", "BRAND_AWARENESS": "AWARENESS", "VIDEO_VIEWS": "AWARENESS",
    "OUTCOME_ENGAGEMENT": "ENGAGEMENT", "POST_ENGAGEMENT": "ENGAGEMENT", "PAGE_LIKES": "ENGAGEMENT",
    "EVENT_RESPONSES": "ENGAGEMENT", "OUTCOME_APP_PROMOTION": "OTHER", "APP_INSTALLS": "OTHER",
}

ACCOUNT_STATUS = {1: "ACTIVE", 2: "DISABLED", 3: "UNSETTLED", 7: "PENDING_RISK_REVIEW", 8: "PENDING_SETTLEMENT",
                  9: "IN_GRACE_PERIOD", 100: "PENDING_CLOSURE", 101: "CLOSED", 201: "ANY_ACTIVE", 202: "ANY_CLOSED"}

METRIC_FIELDS = ["impressions", "reach", "clicks", "inline_link_clicks", "ctr", "cpc", "cpm", "spend", "frequency",
                 "actions", "action_values", "purchase_roas", "account_currency"]


def pick_action(items: list[dict[str, Any]] | None) -> float:
    """Toma el primer tipo de compra disponible según PURCHASE_TYPES (sin sumar duplicados)."""
    if not items:
        return 0.0
    by_type = {i.get("action_type"): i.get("value") for i in items}
    for t in PURCHASE_TYPES:
        if t in by_type and by_type[t] is not None:
            try:
                return float(by_type[t])
            except (TypeError, ValueError):
                return 0.0
    return 0.0


def selected_purchase_action(items: list[dict[str, Any]] | None,
                             priority: tuple[str, ...] = PURCHASE_TYPES) -> tuple[str | None, float | None]:
    """Elige una sola acción válida; no suma variantes equivalentes."""
    if items is None:
        return None, None
    by_type = {item.get("action_type"): item.get("value") for item in items}
    for action_type in priority:
        value = by_type.get(action_type)
        if value is not None:
            try:
                number = float(value)
                if isfinite(number):
                    return action_type, number
            except (TypeError, ValueError):
                continue
    return None, None


def pick_purchases(items: list[dict[str, Any]] | None,
                   priority: tuple[str, ...] = PURCHASE_TYPES) -> float | None:
    """Compras medidas por Meta; ausencia de la acción no equivale a cero."""
    return selected_purchase_action(items, priority)[1]


def matching_purchase_value(items: list[dict[str, Any]] | None, action_type: str | None) -> float | None:
    """Valor de la misma acción que produjo Purchase, nunca de otra variante."""
    if action_type is None:
        return None
    for item in items or []:
        if item.get("action_type") == action_type and item.get("value") is not None:
            try:
                number = float(item["value"])
                return number if isfinite(number) else None
            except (TypeError, ValueError):
                return None
    return None


def pick_leads(items: list[dict[str, Any]] | None) -> float | None:
    if items is None:
        return None
    by_type = {item.get("action_type"): item.get("value") for item in items}
    for action_type in LEAD_TYPES:
        value = by_type.get(action_type)
        if value is not None:
            try:
                return float(value)
            except (TypeError, ValueError):
                return None
    return None


def normalize_row(row: dict[str, Any], account_id: str | None = None) -> dict[str, Any]:
    """Convierte una fila de /insights al modelo común."""
    action_type, purchases = selected_purchase_action(
        row.get("actions"), purchase_action_priority(account_id)
    )
    purchase_value = matching_purchase_value(row.get("action_values"), action_type)
    base = {
        "impressions": int(row.get("impressions") or 0),
        "reach": int(row.get("reach") or 0),
        "clicks": int(row.get("clicks") or 0),
        "inline_link_clicks": int(row.get("inline_link_clicks") or 0),
        "spend": float(row.get("spend") or 0),
        "conversions": purchases if purchases is not None else 0.0,
        "purchases": purchases,
        "leads": pick_leads(row.get("actions")),
        # Sin action_value de la acción canónica no existe valor atribuible;
        # mantener null evita publicar ROAS=0 como si fuera un dato medido.
        "conversion_value": purchase_value,
        "value_available": purchase_value is not None,
    }
    if row.get("frequency"):
        base["frequency"] = float(row["frequency"])
    out = derive(base)
    out["cost_per_result"] = (
        out["spend"] / out["leads"] if out["leads"] not in (None, 0) else None
    )
    out["cost_per_purchase"] = (
        out["spend"] / out["purchases"] if out["purchases"] not in (None, 0) else None
    )
    # purchase_roas nativo de Meta (si existe) solo como referencia; el ROAS
    # mostrado se calcula igual en todas las plataformas: value / spend.
    out["purchase_roas_native"] = pick_action(row.get("purchase_roas")) or None
    return out


class MetaProvider:
    platform = "meta_ads"

    def __init__(self, settings: Settings, session: requests.Session | None = None) -> None:
        self.settings = settings
        self.session = session or requests.Session()

    @property
    def base_url(self) -> str:
        return f"https://graph.facebook.com/{self.settings.meta_api_version}"

    def _headers(self) -> dict[str, str]:
        token = self.settings.meta_access_token
        if not token:
            raise ProviderNotConfigured("Meta Ads no está configurado en el servidor (falta la variable META_ACCESS_TOKEN).")
        return {"Authorization": f"Bearer {token}"}

    def _get(self, url: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        headers = self._headers()
        try:
            resp = self.session.get(url, params=params, headers=headers, timeout=45)
        except requests.RequestException as e:
            raise ProviderError(f"Error de red con Meta: {sanitize(type(e).__name__)}") from None
        try:
            data = resp.json()
        except ValueError:
            raise ProviderError(f"Respuesta inválida de Meta (HTTP {resp.status_code})") from None
        if resp.status_code >= 400 or "error" in data:
            err = data.get("error", {}) if isinstance(data, dict) else {}
            code = err.get("code")
            msg = err.get("error_user_msg") or err.get("message") or f"HTTP {resp.status_code}"
            if code == 190:
                raise ProviderNotConfigured(f"El token de Meta es inválido o expiró (code 190): {sanitize(msg)}")
            raise ProviderError(sanitize(f"Meta API error {code}: {msg}"))
        return data

    def _paged(self, path: str, params: dict[str, Any], max_pages: int = 25) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        data = self._get(f"{self.base_url}/{path}", params)
        for _ in range(max_pages):
            out.extend(data.get("data", []))
            nxt = data.get("paging", {}).get("next")
            if not nxt:
                break
            # La URL "next" puede incluir el token si se hubiera mandado por query;
            # como usamos header no lo incluye, pero igual la pedimos sin params extra.
            data = self._get(nxt)
        return out

    def _insights(self, account_id: str, start: str, end: str, level: str, fields: list[str],
                  campaign_ids: list[str] | None = None, adset_ids: list[str] | None = None,
                  **extra: Any) -> list[dict[str, Any]]:
        params = {
            "level": level,
            "time_range": json.dumps({"since": start, "until": end}),
            "fields": ",".join(fields),
            "limit": 500,
            **extra,
        }
        # El filtro viaja a la API: en niveles adset/ad restringe a los que
        # pertenecen a esas campañas o ad sets, así nunca llegan ad sets ni ads
        # de recursos que no son de este cliente.
        filters = []
        if campaign_ids:
            filters.append({"field": "campaign.id", "operator": "IN", "value": list(campaign_ids)})
        if adset_ids:
            filters.append({"field": "adset.id", "operator": "IN", "value": list(adset_ids)})
        if filters:
            params["filtering"] = json.dumps(filters)
        return self._paged(f"act_{account_id}/insights", params)

    def list_campaigns(self, account_id: str) -> list[dict[str, Any]]:
        """Campañas reales de una cuenta publicitaria, para elegirlas en Settings."""
        account = self._paged("me/adaccounts", {"fields": "account_id,name,currency", "limit": 200})
        matched_account = next((a for a in account if str(a.get("account_id")) == str(account_id)), {})
        name = matched_account.get("name")
        currency = matched_account.get("currency")
        rows = self._paged(f"act_{account_id}/campaigns", {
            "fields": "id,name,status,effective_status,objective",
            "limit": 500,
        })
        out = [{
            "platform": "meta_ads",
            "ad_account_id": str(account_id),
            "account_name": name,
            "campaign_id": str(r.get("id")),
            "campaign_name": r.get("name"),
            "status": r.get("status"),
            "effective_status": r.get("effective_status"),
            "currency": currency,
            "objective": OBJECTIVE_MAP.get(r.get("objective") or "", "OTHER"),
        } for r in rows]
        log.info("Meta: %d campañas en la cuenta %s", len(out), account_id)
        return out

    # ------------------------------------------------------------------ cuentas
    def list_accounts(self) -> list[dict[str, Any]]:
        rows = self._paged("me/adaccounts", {
            "fields": "account_id,name,currency,timezone_name,account_status,business{id,name}",
            "limit": 200,
        })
        accounts = []
        for r in rows:
            status = ACCOUNT_STATUS.get(r.get("account_status"), str(r.get("account_status")))
            accounts.append({
                "platform": "meta_ads", "id": str(r.get("account_id")), "name": r.get("name"),
                "currency": r.get("currency"), "timezone": r.get("timezone_name"), "status": status,
                "account_type": "ad_account", "business": (r.get("business") or {}).get("name"),
            })
        return sorted(accounts, key=lambda a: (a["status"] != "ACTIVE", (a["name"] or "").lower()))

    # -------------------------------------------------------------- rendimiento
    def list_adsets(self, account_id: str, campaign_id: str) -> list[dict[str, Any]]:
        """Conjuntos de anuncios reales de una campaña, para elegirlos en Settings."""
        campaign = self._get(f"{self.base_url}/{campaign_id}", {"fields": "id,name,account_id"})
        rows = self._paged(f"{campaign_id}/adsets", {
            "fields": "id,name,status,effective_status,optimization_goal,billing_event",
            "limit": 500,
        })
        out = [{
            "platform": "meta_ads",
            "ad_account_id": str(account_id),
            "account_name": None,
            "campaign_id": str(campaign_id),
            "campaign_name": campaign.get("name"),
            "adset_id": str(r.get("id")),
            "adset_name": r.get("name"),
            "status": r.get("status"),
            "effective_status": r.get("effective_status"),
            "optimization_goal": r.get("optimization_goal"),
            "billing_event": r.get("billing_event"),
        } for r in rows]
        log.info("Meta: %d ad sets en la campaña %s", len(out), campaign_id)
        return out

    def performance_adsets(self, account_id: str, start: str, end: str,
                           adset_ids: list[str]) -> dict[str, Any]:
        """Rendimiento de UNOS ad sets concretos.

        Todo sale del nivel adset: los totales y la serie diaria son la suma de
        esos ad sets y las filas de campaña se reconstruyen agregándolos. Nunca
        se pide la campaña entera para luego atribuirla completa.
        """
        rows = self._insights(account_id, start, end, "adset",
                              ["campaign_id", "campaign_name", "adset_id", "adset_name", "objective", *METRIC_FIELDS],
                              adset_ids=adset_ids)
        daily_rows = self._insights(account_id, start, end, "adset",
                                    ["adset_id", "impressions", "clicks", "spend", "actions", "action_values"],
                                    adset_ids=adset_ids, time_increment=1)
        currency = next((r.get("account_currency") for r in rows if r.get("account_currency")), None)
        adsets = []
        for r in rows:
            n = normalize_row(r, account_id)
            n.update({
                "campaign_id": str(r.get("campaign_id")),
                "campaign_name": r.get("campaign_name"),
                "ad_set_id": str(r.get("adset_id")),
                "ad_set_name": r.get("adset_name"),
                "objective": OBJECTIVE_MAP.get(r.get("objective") or "", "OTHER"),
            })
            adsets.append(n)
        # Las campañas del cliente son la suma de SUS ad sets, no la campaña real.
        campaigns = merge_campaigns(adsets, key="campaign_id")
        for c in campaigns:
            c.pop("ad_set_id", None)
            c.pop("ad_set_name", None)
        return {
            "currency": currency,
            "totals": sum_rows(adsets),
            "campaigns": campaigns,
            "adsets": adsets,
            "daily": merge_daily([{**normalize_row(r, account_id), "date": r.get("date_start")} for r in daily_rows]),
            # Suma de ad sets independientes: el alcance no está deduplicado.
            "reach_is_sum": True,
        }

    def performance(self, account_id: str, start: str, end: str,
                    campaign_ids: list[str] | None = None) -> dict[str, Any]:
        """Rendimiento de una cuenta. `campaign_ids` la restringe a esas campañas."""
        camp_rows = self._insights(account_id, start, end, "campaign",
                                   ["campaign_id", "campaign_name", "objective", *METRIC_FIELDS],
                                   campaign_ids=campaign_ids)
        # Con filtro de campañas los totales y la serie diaria NO pueden salir
        # del nivel "account" (incluiría campañas ajenas): se piden al nivel
        # campaign y se suman desde las métricas base.
        level = "campaign" if campaign_ids else "account"
        acct_rows = self._insights(account_id, start, end, level, METRIC_FIELDS, campaign_ids=campaign_ids)
        daily_rows = self._insights(account_id, start, end, level,
                                    ["impressions", "clicks", "spend", "actions", "action_values"],
                                    campaign_ids=campaign_ids, time_increment=1)
        # La vista de conversiones filtrada necesita conservar la campaña en
        # cada punto diario para aplicar Estado/Gasto antes de sumar.
        campaign_daily_rows = self._insights(
            account_id, start, end, "campaign",
            ["campaign_id", "campaign_name", "objective", *METRIC_FIELDS],
            campaign_ids=campaign_ids, time_increment=1,
        )

        currency = next((r.get("account_currency") for r in camp_rows + acct_rows if r.get("account_currency")), None)
        campaigns = []
        for r in camp_rows:
            n = normalize_row(r, account_id)
            n.update({
                "campaign_id": str(r.get("campaign_id")),
                "campaign_name": r.get("campaign_name"),
                "objective": OBJECTIVE_MAP.get(r.get("objective") or "", "OTHER"),
                "objective_raw": r.get("objective"),
            })
            campaigns.append(n)
        # Sin filtro, los totales salen del nivel cuenta (el reach total NO es
        # la suma de campañas). Con filtro hay que sumar las campañas elegidas y
        # recalcular los ratios desde las métricas base.
        if campaign_ids:
            totals = sum_rows([normalize_row(r, account_id) for r in acct_rows])
            daily = merge_daily([{**normalize_row(r, account_id), "date": r.get("date_start")} for r in daily_rows])
            # Totales sumados de varias campañas: el alcance NO está deduplicado.
            out_reach_is_sum = True
        else:
            totals = normalize_row(acct_rows[0], account_id) if acct_rows else {
                **derive(empty_base(reach=0)), "purchases": None, "leads": None,
                "cost_per_purchase": None, "cost_per_result": None,
            }
            daily = [{"date": r.get("date_start"), **normalize_row(r, account_id)} for r in daily_rows]
            # Nivel cuenta: Meta entrega el alcance ya deduplicado.
            out_reach_is_sum = False
        daily.sort(key=lambda d: d["date"] or "")
        campaign_daily = []
        for row in campaign_daily_rows:
            point = normalize_row(row, account_id)
            point.update({
                "campaign_id": str(row.get("campaign_id") or ""),
                "date": row.get("date_start"),
            })
            campaign_daily.append(point)
        return {"currency": currency, "totals": totals, "campaigns": campaigns, "daily": daily,
                "reach_is_sum": out_reach_is_sum, "campaign_daily": campaign_daily}

    def adsets(self, account_id: str, start: str, end: str,
               campaign_ids: list[str] | None = None, adset_ids: list[str] | None = None) -> list[dict[str, Any]]:
        """Ad sets de la cuenta, restringidos a esas campañas o a esos ad sets."""
        rows = self._insights(account_id, start, end, "adset",
                              ["campaign_id", "campaign_name", "adset_id", "adset_name", "objective", *METRIC_FIELDS],
                              campaign_ids=campaign_ids, adset_ids=adset_ids)
        out = []
        for r in rows:
            n = normalize_row(r, account_id)
            n.update({
                "campaign_id": str(r.get("campaign_id")),
                "campaign_name": r.get("campaign_name"),
                "ad_set_id": str(r.get("adset_id")),
                "ad_set_name": r.get("adset_name"),
                "objective": OBJECTIVE_MAP.get(r.get("objective") or "", "OTHER"),
            })
            out.append(n)
        return out

    def list_ads(self, account_id: str, campaign_ids: list[str] | None = None,
                 adset_ids: list[str] | None = None) -> list[dict[str, Any]]:
        """Catálogo de anuncios con sus estados efectivos actuales y jerarquía."""
        if campaign_ids == [] or adset_ids == []:
            return []
        filters = []
        if campaign_ids:
            filters.append({"field": "campaign.id", "operator": "IN", "value": list(campaign_ids)})
        if adset_ids:
            filters.append({"field": "adset.id", "operator": "IN", "value": list(adset_ids)})
        params: dict[str, Any] = {
            "fields": "id,name,status,effective_status,campaign{id,name,status,effective_status},adset{id,name,status,effective_status}",
            "limit": 500,
        }
        if filters:
            params["filtering"] = json.dumps(filters)
        rows = self._paged(f"act_{account_id}/ads", params)
        out = []
        for row in rows:
            campaign = row.get("campaign") or {}
            adset = row.get("adset") or {}
            if campaign_ids and str(campaign.get("id")) not in campaign_ids:
                continue
            if adset_ids and str(adset.get("id")) not in adset_ids:
                continue
            out.append({
                "ad_id": str(row.get("id")),
                "ad_name": row.get("name"),
                "status": row.get("status"),
                "effective_status": row.get("effective_status"),
                "campaign_id": str(campaign.get("id")) if campaign.get("id") is not None else None,
                "campaign_name": campaign.get("name"),
                "campaign_status": campaign.get("status"),
                "campaign_effective_status": campaign.get("effective_status"),
                "ad_set_id": str(adset.get("id")) if adset.get("id") is not None else None,
                "ad_set_name": adset.get("name"),
                "ad_set_status": adset.get("status"),
                "ad_set_effective_status": adset.get("effective_status"),
                "source_account_id": str(account_id),
            })
        return out

    def performance_ads(self, account_id: str, start: str, end: str,
                        campaign_ids: list[str] | None = None,
                        adset_ids: list[str] | None = None) -> list[dict[str, Any]]:
        """Métricas de Meta Insights a level=ad para el periodo indicado."""
        if campaign_ids == [] or adset_ids == []:
            return []
        fields = [
            "ad_id", "ad_name", "campaign_id", "campaign_name",
            "adset_id", "adset_name", "objective", *METRIC_FIELDS,
        ]
        rows = self._insights(
            account_id, start, end, "ad", fields,
            campaign_ids=campaign_ids, adset_ids=adset_ids,
        )
        out = []
        for row in rows:
            if campaign_ids and str(row.get("campaign_id")) not in campaign_ids:
                continue
            if adset_ids and str(row.get("adset_id")) not in adset_ids:
                continue
            normalized = normalize_row(row, account_id)
            # No reinterpretar una métrica omitida por la API como cero.
            for key, source in (("spend", "spend"), ("impressions", "impressions"), ("clicks", "clicks")):
                normalized[key] = float(row[source]) if row.get(source) is not None else None
            normalized.update({
                "ad_id": str(row.get("ad_id")) if row.get("ad_id") is not None else None,
                "ad_name": row.get("ad_name"),
                "campaign_id": str(row.get("campaign_id")) if row.get("campaign_id") is not None else None,
                "campaign_name": row.get("campaign_name"),
                "ad_set_id": str(row.get("adset_id")) if row.get("adset_id") is not None else None,
                "ad_set_name": row.get("adset_name"),
                "objective": OBJECTIVE_MAP.get(row.get("objective") or "", "OTHER"),
                "leads": normalized["leads"],
                "currency": row.get("account_currency"),
            })
            out.append(normalized)
        return out

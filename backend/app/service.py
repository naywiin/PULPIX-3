"""Orquestación del dashboard: una plataforma que falla nunca rompe a las demás."""
from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timezone
from typing import Any

from .ads_consolidate import merge_campaigns, merge_daily, select_campaigns, sum_rows, tag_source
from .cache import TTLCache, perf_key
from .exclusions import filter_accounts, is_excluded
from .ga4_consolidate import consolidate, currencies
from .models import AD_PLATFORMS, AdSource, Client, account_ids, ad_sources, blocked_resources
from .providers.base import ProviderError, ProviderNotConfigured, sanitize
from .providers.spotify import SpotifyAuthError
from .providers.pinterest import PinterestAuthError, PinterestPermissionError
from .performance_detail import build_detail_response
from .spotify_security import require_santuario_binding

log = logging.getLogger("mp.service")

PLATFORMS = ("google_ads", "meta_ads", "spotify_ads", "pinterest_ads", "ga4")
LABELS = {
    "google_ads": "Google Ads",
    "meta_ads": "Meta Ads",
    "spotify_ads": "Spotify Ads",
    "pinterest_ads": "Pinterest Ads",
    "ga4": "Google Analytics",
}

# Consultas simultáneas a las APIs de plataforma (cada propiedad GA4 y cada
# periodo es una). Límite propio para no disparar N llamadas a la vez cuando un
# cliente tiene muchas propiedades.
FETCH_WORKERS = 8

Range = tuple[date, date]


def _iso_ts(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat(timespec="seconds")


def _summary(accounts: list[dict[str, Any]]) -> dict[str, int]:
    """Separa cuentas manager (MCC secundarios en Google Ads) de anunciantes."""
    managers = sum(1 for a in accounts if a.get("account_type") == "manager")
    return {"total": len(accounts), "managers": managers, "advertisers": len(accounts) - managers}


def _groups(accounts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Agrupa por cuenta contenedora (GA4: cuenta → propiedades). Vacío si no aplica."""
    tree: dict[str, dict[str, Any]] = {}
    for a in accounts:
        account_id = a.get("account_id")
        if not account_id:
            continue
        group = tree.setdefault(str(account_id), {
            "account_id": str(account_id),
            "account_name": a.get("account_name"),
            "properties": [],
        })
        group["properties"].append(a)
    return sorted(tree.values(), key=lambda g: ((g["account_name"] or "").lower(), g["account_id"]))


def _source_level(src: AdSource) -> str:
    """Nivel al que está definida una fuente: account, campaign o adset."""
    if getattr(src, "adset_selections", None):
        return "adset" if not src.campaign_ids else "mixed"
    return "campaign" if src.campaign_ids else "account"


def _combine_ads(periods: list[dict[str, Any]]) -> dict[str, Any]:
    """Un periodo único a partir de los de N cuentas de origen.

    Las métricas base se suman y las derivadas (CTR, CPC, CPM, CPA, ROAS) se
    recalculan desde esos totales; nunca se promedian entre cuentas.
    """
    campaigns = merge_campaigns([c for p in periods for c in (p.get("campaigns") or [])])
    campaigns.sort(key=lambda c: (-(c.get("spend") or 0), c.get("campaign_name") or ""))
    adsets = merge_campaigns([a for p in periods for a in (p.get("adsets") or [])], key="ad_set_id")
    adsets.sort(key=lambda a: (-(a.get("spend") or 0), a.get("ad_set_name") or ""))
    out: dict[str, Any] = {
        # Los totales salen de los totales de cada cuenta (no de las campañas):
        # en Meta el reach de la cuenta no es la suma de sus campañas.
        "totals": sum_rows([p.get("totals") or {} for p in periods]),
        "campaigns": campaigns,
        "daily": merge_daily([d for p in periods for d in (p.get("daily") or [])]),
        "sources_count": len(periods),
        # reach (Meta) no está deduplicado si se sumaron cuentas, o si el
        # proveedor ya avisó de que sumó campañas o ad sets.
        "reach_is_sum": len(periods) > 1 or any(p.get("reach_is_sum") for p in periods),
    }
    if adsets:
        out["adsets"] = adsets
    return out


class DashboardService:
    def __init__(self, providers: dict[str, Any], cache: TTLCache, discovery_cache: TTLCache) -> None:
        self.providers = providers
        self.cache = cache
        self.discovery_cache = discovery_cache
        # Dos pools: el de bloques nunca espera tareas de su propio pool
        # (un bloque GA4 con N propiedades no puede quedarse sin workers).
        self._pool = ThreadPoolExecutor(max_workers=12, thread_name_prefix="mp-block")
        self._fetch_pool = ThreadPoolExecutor(max_workers=FETCH_WORKERS, thread_name_prefix="mp-fetch")

    # ---------------------------------------------------------------- periodo
    def _fetch(self, platform: str, account_id: str, start: str, end: str,
               campaign_ids: list[str] | None = None, adset_ids: list[str] | None = None,
               login_customer_id: str | None = None) -> dict[str, Any]:
        p = self.providers[platform]
        if platform == "google_ads":
            return p.performance(account_id, login_customer_id, start, end, campaign_ids)
        if platform == "meta_ads":
            if adset_ids:
                # Selección por ad set: se consulta a nivel adset y las filas de
                # campaña se construyen sumando SOLO esos ad sets. Nunca se pide
                # la campaña entera para luego atribuirla completa.
                return p.performance_adsets(account_id, start, end, adset_ids)
            data = p.performance(account_id, start, end, campaign_ids)
            # El mismo filtro baja a los ad sets: un cliente que recibe una
            # campaña concreta NO debe ver ad sets de otras campañas de la cuenta.
            data["adsets"] = p.adsets(account_id, start, end, campaign_ids)
            return data
        if platform == "pinterest_ads":
            return p.performance(account_id, start, end, campaign_ids)
        return p.performance(account_id, start, end)

    def _period(self, client: Client, platform: str, account_id: str, rng: Range, refresh: bool,
                campaign_ids: list[str] | None = None, adset_ids: list[str] | None = None,
                login_customer_id: str | None = None):
        start, end = rng[0].isoformat(), rng[1].isoformat()
        key = perf_key(client.id, platform, account_id, start, end,
                       campaign_ids=campaign_ids, adset_ids=adset_ids)
        return self.cache.get_or_set(
            key,
            lambda: self._fetch(platform, account_id, start, end, campaign_ids, adset_ids, login_customer_id),
            refresh=refresh,
        )

    # --------------------------------------------------------------- bloque
    def _block_base(self, client: Client, platform: str, ids: list[str], rng: Range, cmp: Range | None) -> dict[str, Any]:
        block: dict[str, Any] = {
            "platform": platform,
            "label": LABELS[platform],
            "client_id": client.id,
            # account_id sigue siendo el principal (compatibilidad con la UI previa).
            "account_id": ids[0] if ids else None,
            "source": "real",
            "range": {"start": rng[0].isoformat(), "end": rng[1].isoformat()},
            "compare_range": {"start": cmp[0].isoformat(), "end": cmp[1].isoformat()} if cmp else None,
        }
        if platform == "ga4":
            block["property_ids"] = list(ids)
        return block

    def platform_block(self, client: Client, platform: str, rng: Range, cmp: Range | None, refresh: bool = False,
                       owners: dict[tuple[str, str, str], str] | None = None) -> dict[str, Any]:
        """Bloque de una plataforma para un cliente.

        `owners` es el mapa de campañas reclamadas en exclusiva por CUALQUIER
        cliente (ver models.exclusive_claims). Sin él, un cliente vería también
        las campañas que ya cedió a otro y se contarían dos veces.
        """
        label = LABELS[platform]
        ids = account_ids(client, platform)
        block = self._block_base(client, platform, ids, rng, cmp)
        if not ids:
            noun = "propiedad de Google Analytics" if platform == "ga4" else f"cuenta de {label}"
            status = "not_configured" if platform in ("spotify_ads", "pinterest_ads") else "not_linked"
            message = f"{label} no configurado para este cliente." if platform in ("spotify_ads", "pinterest_ads") else f"{client.name} no tiene una {noun} asignada."
            return {**block, "status": status, "message": message}
        if platform == "ga4":
            return self._ga4_block(client, block, ids, rng, cmp, refresh)
        if platform == "spotify_ads":
            return self._spotify_block(client, block, rng, cmp, refresh)
        return self._ads_block(client, block, platform, rng, cmp, refresh, owners or {})

    def _spotify_block(self, client: Client, block: dict[str, Any], rng: Range,
                       cmp: Range | None, refresh: bool) -> dict[str, Any]:
        """Carga la única fuente aprobada sin pasar por descubrimiento/asignación."""
        source = client.spotify_ads_sources[0]
        try:
            require_santuario_binding(
                client_slug=client.slug,
                business_id=source.business_id,
                ad_account_id=source.ad_account_id,
            )
        except ValueError as exc:
            return {**block, "status": "error", "current": None, "previous": None,
                    "message": "La asociación de Spotify Ads fue rechazada", "detail": sanitize(exc)}

        cur_future = self._fetch_pool.submit(
            self._period, client, "spotify_ads", source.ad_account_id, rng, refresh
        )
        prev_future = self._fetch_pool.submit(
            self._period, client, "spotify_ads", source.ad_account_id, cmp, refresh
        ) if cmp else None
        try:
            current, cur_ts, cur_hit = cur_future.result(timeout=180)
        except Exception as exc:
            status, detail = self._ga4_error(exc)
            if isinstance(exc, SpotifyAuthError):
                status = "auth_error"
            else:
                status = "provider_error"
            if prev_future:
                prev_future.cancel()
            return {**block, "status": status, "current": None, "previous": None,
                    "message": "Spotify Ads temporalmente no disponible", "detail": detail}

        previous = None
        prev_ts, prev_hit, compare_error = cur_ts, cur_hit, None
        if prev_future:
            try:
                previous, prev_ts, prev_hit = prev_future.result(timeout=180)
            except Exception as exc:
                _, compare_error = self._ga4_error(exc)
        source_result = {
            "business_id": source.business_id,
            "account_id": source.ad_account_id,
            "account_name": current.get("account_name"),
            "status": "ok",
        }
        warnings = []
        if compare_error:
            warnings.append({"account_id": source.ad_account_id, "status": "compare_error",
                             "message": "Sin datos del periodo de comparación para Spotify Ads.",
                             "detail": compare_error})
        return {
            **block,
            "business_id": source.business_id,
            "currency": current.get("currency") or client.currency,
            "status": "ok",
            "fetched_at": _iso_ts(min(cur_ts, prev_ts)),
            "cached": cur_hit and prev_hit,
            "sources": [source_result],
            "warnings": warnings,
            "current": current,
            "previous": previous,
        }

    # ------------------------------------------------- Google Ads / Meta Ads
    def _campaign_catalog(self, platform: str, src: AdSource, refresh: bool) -> list[dict[str, Any]]:
        """Campañas existentes en una cuenta (cacheadas con el descubrimiento)."""
        provider = self.providers[platform]
        args = (src.account_id, src.login_customer_id) if platform == "google_ads" else (src.account_id,)
        value, _, _ = self.discovery_cache.get_or_set(
            ("campaigns", platform, src.account_id),
            lambda: provider.list_campaigns(*args),
            refresh=refresh,
        )
        return value

    def _adset_catalog(self, platform: str, account_id: str, campaign_id: str, refresh: bool) -> list[dict[str, Any]]:
        """Ad sets existentes en una campaña (solo Meta)."""
        provider = self.providers[platform]
        if not hasattr(provider, "list_adsets"):
            return []
        value, _, _ = self.discovery_cache.get_or_set(
            ("adsets", platform, str(account_id), str(campaign_id)),
            lambda: provider.list_adsets(account_id, campaign_id),
            refresh=refresh,
        )
        return value

    def _plan(self, client: Client, platform: str, src: AdSource, owners: dict[Any, str],
              refresh: bool) -> tuple[list[str] | None, dict[str, list[str]]]:
        """Universo EFECTIVO de una fuente: (campañas enteras, {campaña: ad sets}).

        Aplica el recorte jerárquico — una selección más específica de otro
        cliente recorta una más amplia de este:

            cuenta entera
              − campañas que otro tiene en exclusiva
              − ad sets que otro tiene en exclusiva (esa campaña pasa a parcial)

        Devuelve `None` como lista de campañas cuando no hay ningún recorte: así
        la cuenta se consulta a nivel cuenta y Meta entrega el alcance
        deduplicado de verdad.
        """
        blocked_campaigns, blocked_adsets = blocked_resources(client, platform, src.account_id, owners)
        partial: dict[str, list[str]] = {}

        # Nivel más específico: ad sets elegidos explícitamente por este cliente.
        for sel in getattr(src, "adset_selections", []) or []:
            partial[sel.campaign_id] = list(sel.adset_ids)

        def carve(campaign_id: str) -> None:
            """Una campaña que otro recortó por ad sets pasa a consultarse parcial."""
            taken = blocked_adsets.get(campaign_id, set())
            every = [str(a.get("adset_id")) for a in self._adset_catalog(platform, src.account_id, campaign_id, refresh)]
            remaining = [a for a in every if a not in taken]
            if remaining:
                partial[campaign_id] = remaining

        if src.campaign_ids:
            whole = []
            for cid in src.campaign_ids:
                if cid in partial:
                    continue
                if cid in blocked_adsets:
                    carve(cid)
                else:
                    whole.append(cid)
            return whole, partial

        if partial and not src.campaign_ids:
            # Fuente exclusivamente de ad sets: nada más de esta cuenta.
            return [], partial

        # Cuenta entera.
        if not blocked_campaigns and not blocked_adsets:
            return None, {}
        whole = []
        for row in self._campaign_catalog(platform, src, refresh):
            cid = str(row.get("campaign_id"))
            if cid in blocked_campaigns or cid in partial:
                continue
            if cid in blocked_adsets:
                carve(cid)
            else:
                whole.append(cid)
        return whole, partial

    def _ads_source(self, client: Client, platform: str, src: AdSource, parts: list[dict[str, Any]],
                    plan: tuple[list[str] | None, dict[str, list[str]]]) -> dict[str, Any]:
        """Resultado de UNA cuenta de origen. Nunca lanza: devuelve su estado.

        `parts` son los trozos del universo efectivo (campañas enteras y ad sets
        parciales), cada uno con sus futures de periodo actual y comparación.
        """
        whole, partial = plan
        entry: dict[str, Any] = {
            "account_id": src.account_id,
            "campaign_ids": list(src.campaign_ids),
            "allocation": src.allocation,
            "level": _source_level(src),
            "adset_selections": [
                {"campaign_id": sel.campaign_id, "adset_ids": list(sel.adset_ids), "allocation": sel.allocation}
                for sel in getattr(src, "adset_selections", []) or []
            ],
            "effective": {
                "campaigns": None if whole is None else list(whole),
                "adsets": {k: list(v) for k, v in partial.items()},
            },
        }
        cur_periods: list[dict[str, Any]] = []
        prev_periods: list[dict[str, Any]] = []
        t_cur = t_prev = None
        cached = True
        compare_error = None
        account_name = None
        currency = None

        for part in parts:
            try:
                cur, ts, hit = part["cur"].result(timeout=120)
            except Exception as e:
                status, detail = self._ga4_error(e)
                log.warning("%s falló cliente=%s cuenta=%s: %s", LABELS[platform], client.id, src.account_id, detail)
                for p2 in parts:
                    if p2["prev"] is not None:
                        p2["prev"].cancel()
                return {**entry, "status": status,
                        "message": f"La cuenta {src.account_id} no pudo consultarse", "detail": detail}
            cur = self._shape_source_period(cur, src, part.get("partial_of"))
            cur_periods.append(cur)
            account_name = account_name or cur.get("account_name")
            currency = currency or cur.get("currency")
            t_cur = ts if t_cur is None else min(t_cur, ts)
            cached = cached and hit

            if part["prev"] is not None:
                try:
                    prev, ts_p, hit_p = part["prev"].result(timeout=120)
                except Exception as e:
                    _, compare_error = self._ga4_error(e)
                    log.warning("%s sin comparación cliente=%s cuenta=%s: %s",
                                LABELS[platform], client.id, src.account_id, compare_error)
                    prev_periods = []
                    continue
                prev_periods.append(self._shape_source_period(prev, src, part.get("partial_of")))
                t_prev = ts_p if t_prev is None else min(t_prev, ts_p)
                cached = cached and hit_p

        has_prev = bool(prev_periods) and compare_error is None and len(prev_periods) == len(parts)
        return {
            **entry,
            "account_name": account_name,
            "currency": currency,
            "status": "ok",
            "compare_error": compare_error,
            "fetched_at": _iso_ts(min(t_cur, t_prev if t_prev is not None else t_cur)),
            "cached": cached,
            "current": _combine_ads(cur_periods) if len(cur_periods) > 1 else cur_periods[0],
            "previous": (_combine_ads(prev_periods) if len(prev_periods) > 1 else prev_periods[0]) if has_prev else None,
        }

    @staticmethod
    def _shape_source_period(period: dict[str, Any], src: AdSource,
                             partial_of: dict[str, list[str]] | None) -> dict[str, Any]:
        """Etiqueta la procedencia y marca las campañas consumidas parcialmente.

        La consulta ya viajó filtrada (por campaña o por ad set), así que aquí
        no se descarta nada: solo se anota de dónde viene cada fila y, cuando la
        campaña se consume por ad sets, cuántos se seleccionaron.
        """
        account_name = period.get("account_name")
        campaigns = tag_source(period.get("campaigns") or [], src.account_id, account_name)
        if partial_of:
            campaigns = [
                {**c, "partial": True, "selected_adsets": len(partial_of.get(str(c.get("campaign_id")), []))}
                if str(c.get("campaign_id")) in partial_of else c
                for c in campaigns
            ]
        out = {**period, "campaigns": campaigns}
        if period.get("adsets"):
            out["adsets"] = tag_source(period["adsets"], src.account_id, account_name)
        return out

    def _ads_block(self, client: Client, block: dict[str, Any], platform: str, rng: Range, cmp: Range | None,
                   refresh: bool, owners: dict[tuple[str, str, str], str]) -> dict[str, Any]:
        """Consolida todas las fuentes del cliente. Una cuenta caída no tumba al resto."""
        label = LABELS[platform]
        sources = ad_sources(client, platform)
        submitted: list[tuple[AdSource, list[dict[str, Any]], Any]] = []
        results: list[dict[str, Any]] = []

        for src in sources:
            try:
                whole, partial = self._plan(client, platform, src, owners, refresh)
            except Exception as e:
                status, detail = self._ga4_error(e)
                results.append({"account_id": src.account_id, "campaign_ids": list(src.campaign_ids),
                                "allocation": src.allocation, "level": _source_level(src), "status": status,
                                "message": f"La cuenta {src.account_id} no pudo consultarse", "detail": detail})
                continue
            if whole is not None and not whole and not partial:
                # Todo lo de esta cuenta está asignado a otros clientes.
                results.append({"account_id": src.account_id, "campaign_ids": list(src.campaign_ids),
                                "allocation": src.allocation, "level": _source_level(src), "status": "empty",
                                "message": f"La cuenta {src.account_id} no aporta campañas a este cliente."})
                continue

            login = src.login_customer_id if platform == "google_ads" else None
            parts: list[dict[str, Any]] = []

            def submit(campaign_ids, adset_ids, partial_of=None):
                parts.append({
                    "cur": self._fetch_pool.submit(self._period, client, platform, src.account_id, rng,
                                                   refresh, campaign_ids, adset_ids, login),
                    "prev": self._fetch_pool.submit(self._period, client, platform, src.account_id, cmp,
                                                    refresh, campaign_ids, adset_ids, login) if cmp else None,
                    "partial_of": partial_of,
                })

            if whole is None or whole:
                submit(whole, None)
            if partial:
                # Un solo lote de ad sets: el filtro va a la API y las filas de
                # campaña se reconstruyen sumando exclusivamente esos ad sets.
                submit(None, [a for ids in partial.values() for a in ids], partial_of=partial)
            submitted.append((src, parts, (whole, partial)))

        results += [self._ads_source(client, platform, src, parts, plan) for src, parts, plan in submitted]
        # Orden estable: el de las fuentes del cliente (la principal primero).
        order = {s.account_id: i for i, s in enumerate(sources)}
        results.sort(key=lambda r: order.get(r["account_id"], 99))

        ok = [r for r in results if r["status"] == "ok"]
        failed = [r for r in results if r["status"] not in ("ok", "empty")]
        warnings = [{"account_id": r["account_id"], "status": r["status"],
                     "message": r.get("message"), "detail": r.get("detail")} for r in failed]
        warnings += [{"account_id": r["account_id"], "status": "compare_error",
                      "message": f"Sin datos del periodo de comparación para la cuenta {r['account_id']}.",
                      "detail": r["compare_error"]} for r in ok if r.get("compare_error")]
        if failed and ok:
            warnings.insert(0, {
                "account_id": None, "status": "partial",
                "message": f"{len(ok)} de {len(ok) + len(failed)} fuentes de {label} cargadas.",
            })

        if not ok:
            if not failed:  # todas vacías: el cliente no recibe ninguna campaña
                return {**block, "status": "not_linked", "sources": results, "warnings": warnings,
                        "current": None, "previous": None,
                        "message": f"{client.name} no tiene campañas de {label} asignadas."}
            if platform == "pinterest_ads" and len(failed) == 1 and failed[0]["status"] in ("not_configured", "auth_error", "permission_error"):
                status = failed[0]["status"]
            else:
                status = "provider_error" if platform == "pinterest_ads" else "not_configured" if all(r["status"] == "not_configured" for r in failed) else "error"
            detail = "; ".join(f"{r['account_id']}: {r.get('detail') or r.get('message')}" for r in failed)
            return {**block, "status": status, "sources": results, "warnings": warnings,
                    "current": None, "previous": None,
                    "message": f"{label} temporalmente no disponible", "detail": sanitize(detail) if detail else None}

        found = []
        for r in ok:
            c = r.get("currency")
            if c and c not in found:
                found.append(c)
        if platform == "pinterest_ads":
            source_currencies = [source.currency for source in client.pinterest_ads_sources if source.currency]
            for currency in source_currencies:
                if currency not in found:
                    found.append(currency)
        if len(found) > 1:
            warnings.append({"account_id": None, "status": "mixed_currency",
                             "message": f"Las cuentas usan monedas distintas ({', '.join(found)}): los importes se suman sin convertir."})

        cur = (ok[0]["current"] if platform == "pinterest_ads" and len(ok) == 1
               else _combine_ads([r["current"] for r in ok]))
        provider_warnings: list[dict[str, Any]] = []
        if platform == "pinterest_ads":
            for result in ok:
                for warning in (result.get("current") or {}).get("warnings", []):
                    provider_warnings.append({
                        "account_id": result["account_id"],
                        "status": warning.get("status", "partial"),
                        "message": warning.get("message", "Algunos datos de Pinterest no están disponibles."),
                        **({"detail": sanitize(warning["detail"])} if warning.get("detail") else {}),
                    })
            warnings.extend(provider_warnings)
        prev_periods = [r["previous"] for r in ok if r.get("previous")]
        prev = ((prev_periods[0] if platform == "pinterest_ads" and len(prev_periods) == 1 else _combine_ads(prev_periods))
                if cmp and len(prev_periods) == len(ok) else None)
        return {
            **block,
            "status": "partial" if provider_warnings else "ok",
            "message": "Resumen de Pinterest disponible con datos parciales" if provider_warnings else None,
            "currency": found[0] if len(found) == 1 else client.currency,
            "mixed_currency": len(found) > 1,
            "fetched_at": min(r["fetched_at"] for r in ok),
            "cached": all(r["cached"] for r in ok),
            "sources": results,
            "warnings": warnings,
            "current": cur,
            "previous": prev,
        }

    # ------------------------------------------------------- GA4 multi-propiedad
    @staticmethod
    def _ga4_error(e: Exception) -> tuple[str, str]:
        """(estado, detalle ya saneado) de un fallo de una propiedad."""
        if isinstance(e, ProviderNotConfigured):
            return "not_configured", sanitize(e)
        if isinstance(e, PinterestAuthError):
            return "auth_error", sanitize(e)
        if isinstance(e, PinterestPermissionError):
            return "permission_error", sanitize(e)
        if isinstance(e, ProviderError):
            return "error", sanitize(e)
        return "error", sanitize(type(e).__name__)

    def _ga4_property(self, property_id: str, client: Client, cur_fut, prev_fut) -> dict[str, Any]:
        """Arma el resultado de una propiedad a partir de sus futures ya lanzados.

        Nunca lanza: una propiedad rota devuelve su estado y las demás siguen.
        El periodo actual y el de comparación usan SIEMPRE la misma propiedad.
        Los dos periodos se resuelven por separado a propósito: si falla solo el
        de comparación, se pierde la comparación, no los datos actuales.
        """
        entry: dict[str, Any] = {"property_id": property_id}
        try:
            cur, t_cur, hit_cur = cur_fut.result(timeout=120)
        except Exception as e:
            status, detail = self._ga4_error(e)
            log.warning("GA4 falló para cliente=%s propiedad=%s: %s", client.id, property_id, detail)
            if prev_fut is not None:
                prev_fut.cancel()
            return {**entry, "status": status, "message": f"Propiedad {property_id} no disponible", "detail": detail}

        prev, t_prev, hit_prev, compare_error = None, t_cur, hit_cur, None
        if prev_fut is not None:
            try:
                prev, t_prev, hit_prev = prev_fut.result(timeout=120)
            except Exception as e:
                _, compare_error = self._ga4_error(e)
                t_prev, hit_prev = t_cur, hit_cur
                log.warning("GA4 sin comparación para cliente=%s propiedad=%s: %s", client.id, property_id, compare_error)
        return {
            **entry,
            "property_name": cur.get("property_name"),
            "currency": cur.get("currency"),
            "status": "ok",
            "compare_error": compare_error,
            "fetched_at": _iso_ts(min(t_cur, t_prev)),
            "cached": hit_cur and hit_prev,
            "current": cur,
            "previous": prev,
        }

    def _ga4_block(self, client: Client, block: dict[str, Any], ids: list[str], rng: Range, cmp: Range | None, refresh: bool) -> dict[str, Any]:
        """Consolida N propiedades. Las que fallan salen como aviso, no rompen el resto.

        Todas las consultas (propiedad × periodo) se lanzan en una sola tanda al
        pool de fetch, que tiene el límite de concurrencia. Nada dentro de ese
        pool espera a otra tarea del mismo pool: no puede quedarse sin workers.
        """
        submitted = [
            (
                pid,
                self._fetch_pool.submit(self._period, client, "ga4", pid, rng, refresh),
                self._fetch_pool.submit(self._period, client, "ga4", pid, cmp, refresh) if cmp else None,
            )
            for pid in ids
        ]
        props = [self._ga4_property(pid, client, cur_fut, prev_fut) for pid, cur_fut, prev_fut in submitted]

        # Exclusión central: una propiedad de la lista negra no se muestra ni
        # entra en el consolidado, aunque siga asignada en el registro.
        props = [p for p in props if not is_excluded(p.get("property_name"))]

        ok = [p for p in props if p["status"] == "ok"]
        failed = [p for p in props if p["status"] != "ok"]
        warnings = [
            {"property_id": p["property_id"], "status": p["status"], "message": p.get("message"), "detail": p.get("detail")}
            for p in failed
        ]
        # Una propiedad que sí trajo el periodo actual pero no el de comparación:
        # sus datos se muestran igual, solo se avisa que no hay contra qué comparar.
        warnings += [
            {"property_id": p["property_id"], "status": "compare_error",
             "message": f"Sin datos del periodo de comparación para la propiedad {p['property_id']}.",
             "detail": p["compare_error"]}
            for p in ok if p.get("compare_error")
        ]

        if not ok:
            status = "not_configured" if failed and all(p["status"] == "not_configured" for p in failed) else "error"
            detail = "; ".join(f"{p['property_id']}: {p.get('detail') or p.get('message')}" for p in failed) or None
            return {**block, "status": status, "properties": props, "warnings": warnings,
                    "current": None, "previous": None,
                    "message": "Google Analytics temporalmente no disponible", "detail": sanitize(detail) if detail else None}

        cur = consolidate([p["current"] for p in ok])
        prev = consolidate([p["previous"] for p in ok if p.get("previous")]) if cmp and all(p.get("previous") for p in ok) else None

        # Si las propiedades usan monedas distintas, revenue suma importes no
        # homogéneos: se avisa y no se declara una moneda única.
        found = currencies([p["current"] for p in ok])
        if len(found) > 1:
            warnings.append({
                "property_id": None, "status": "mixed_currency",
                "message": f"Las propiedades usan monedas distintas ({', '.join(found)}): Revenue es una suma sin convertir.",
            })
        return {
            **block,
            "status": "ok",
            "currency": found[0] if len(found) == 1 else client.currency,
            "mixed_currency": len(found) > 1,
            # Todas las marcas vienen de _iso_ts (UTC, mismo formato): el mínimo
            # lexicográfico es también el más antiguo.
            "fetched_at": min(p["fetched_at"] for p in ok),
            "cached": all(p["cached"] for p in ok),
            "properties": props,
            "warnings": warnings,
            "current": cur,
            "previous": prev,
        }

    def dashboard(self, client: Client, rng: Range, cmp: Range | None, refresh: bool = False,
                  owners: dict[tuple[str, str, str], str] | None = None) -> dict[str, Any]:
        futures = {p: self._pool.submit(self.platform_block, client, p, rng, cmp, refresh, owners) for p in PLATFORMS}
        platforms = {p: f.result() for p, f in futures.items()}
        return {
            "client": client.model_dump(),
            "source": "real",
            "range": {"start": rng[0].isoformat(), "end": rng[1].isoformat()},
            "compare_range": {"start": cmp[0].isoformat(), "end": cmp[1].isoformat()} if cmp else None,
            "generated_at": datetime.now(tz=timezone.utc).isoformat(timespec="seconds"),
            "platforms": platforms,
        }

    # ------------------------------------------------------------- discovery
    def discover(self, platform: str, refresh: bool = False) -> dict[str, Any]:
        label = LABELS[platform]
        try:
            found, ts, hit = self.discovery_cache.get_or_set(("discovery", platform), self.providers[platform].list_accounts, refresh=refresh)
        except SpotifyAuthError as e:
            return {"platform": platform, "status": "auth_error", "message": "No se pudo autenticar Spotify Ads", "detail": sanitize(e), "accounts": []}
        except PinterestAuthError as e:
            return {"platform": platform, "status": "auth_error", "message": "No se pudo autenticar Pinterest Ads", "detail": sanitize(e), "accounts": []}
        except PinterestPermissionError as e:
            return {"platform": platform, "status": "permission_error", "message": "No hay permisos para consultar Pinterest Ads", "detail": sanitize(e), "accounts": []}
        except ProviderNotConfigured as e:
            return {"platform": platform, "status": "not_configured", "message": f"{label} no configurado en el servidor", "detail": sanitize(e), "accounts": []}
        except Exception as e:
            log.warning("Descubrimiento %s falló: %s", label, sanitize(e))
            status = "provider_error" if platform in ("spotify_ads", "pinterest_ads") else "error"
            return {"platform": platform, "status": status, "message": f"{label} temporalmente no disponible", "detail": sanitize(e), "accounts": []}
        # Las cuentas excluidas no salen del backend: ni sus datos ni sus nombres.
        # El detalle (qué se excluyó) queda solo en el log del servidor.
        accounts, excluded = filter_accounts(found)
        if excluded:
            log.info(
                "Descubrimiento %s: %d cuenta(s) excluidas por la lista central: %s",
                label, len(excluded),
                "; ".join(f"{e['name'] or e['id']} [{e['account_name'] or '—'}]" for e in excluded),
            )
        return {"platform": platform, "status": "ok", "fetched_at": _iso_ts(ts), "cached": hit,
                "summary": _summary(accounts),
                # Solo el conteo: ni los nombres ni los patrones viajan al navegador.
                "excluded": {"count": len(excluded)},
                "groups": _groups(accounts), "accounts": accounts}

    def campaigns(self, platform: str, account_id: str, refresh: bool = False) -> dict[str, Any]:
        """Campañas reales de una cuenta, para elegirlas en Settings.

        Se cachea igual que el descubrimiento de cuentas: es un catálogo, no un
        informe. Las campañas NO se filtran por la lista de exclusión: esa mira
        clientes y cuentas, no campañas dentro de una cuenta ya visible.
        """
        label = LABELS[platform]
        provider = self.providers[platform]
        if not hasattr(provider, "list_campaigns"):
            return {"platform": platform, "account_id": account_id, "status": "unsupported",
                    "message": f"{label} no expone catálogo de campañas", "campaigns": []}
        args = (str(account_id),) if platform != "google_ads" else (str(account_id), None)
        try:
            found, ts, hit = self.discovery_cache.get_or_set(
                ("campaigns", platform, str(account_id)),
                lambda: provider.list_campaigns(*args),
                refresh=refresh,
            )
        except ProviderNotConfigured as e:
            return {"platform": platform, "account_id": account_id, "status": "not_configured",
                    "message": f"{label} no configurado en el servidor", "detail": sanitize(e), "campaigns": []}
        except PinterestAuthError as e:
            return {"platform": platform, "account_id": account_id, "status": "auth_error",
                    "message": "No se pudo autenticar Pinterest Ads", "detail": sanitize(e), "campaigns": []}
        except PinterestPermissionError as e:
            return {"platform": platform, "account_id": account_id, "status": "permission_error",
                    "message": "No hay permisos para consultar Pinterest Ads", "detail": sanitize(e), "campaigns": []}
        except Exception as e:
            log.warning("Campañas de %s cuenta=%s: %s", label, account_id, sanitize(e))
            return {"platform": platform, "account_id": account_id, "status": "provider_error" if platform == "pinterest_ads" else "error",
                    "message": f"{label} temporalmente no disponible", "detail": sanitize(e), "campaigns": []}
        return {"platform": platform, "account_id": str(account_id), "status": "ok",
                "fetched_at": _iso_ts(ts), "cached": hit, "count": len(found), "campaigns": found}

    def adsets_catalog(self, platform: str, account_id: str, campaign_id: str,
                       refresh: bool = False) -> dict[str, Any]:
        """Conjuntos de anuncios de una campaña, para elegirlos en Settings."""
        label = LABELS[platform]
        provider = self.providers[platform]
        base = {"platform": platform, "account_id": str(account_id), "campaign_id": str(campaign_id)}
        if not hasattr(provider, "list_adsets"):
            return {**base, "status": "unsupported",
                    "message": f"{label} no expone catálogo de conjuntos de anuncios", "adsets": []}
        try:
            found, ts, hit = self.discovery_cache.get_or_set(
                ("adsets", platform, str(account_id), str(campaign_id)),
                lambda: provider.list_adsets(str(account_id), str(campaign_id)),
                refresh=refresh,
            )
        except ProviderNotConfigured as e:
            return {**base, "status": "not_configured", "message": f"{label} no configurado en el servidor",
                    "detail": sanitize(e), "adsets": []}
        except Exception as e:
            log.warning("Ad sets de %s cuenta=%s campaña=%s: %s", label, account_id, campaign_id, sanitize(e))
            return {**base, "status": "error", "message": f"{label} temporalmente no disponible",
                    "detail": sanitize(e), "adsets": []}
        return {**base, "status": "ok", "fetched_at": _iso_ts(ts), "cached": hit,
                "count": len(found), "adsets": found}

    def performance_detail(
        self,
        client: Client,
        platform: str,
        start: date,
        end: date,
        level: str = "campaign",
        status: str = "all",
        spend: str = "all",
        owners: dict[tuple[str, str, str], str] | None = None,
        refresh: bool = False,
    ) -> dict[str, Any]:
        """Detalle de campaña/anuncio dentro del mismo alcance del dashboard."""
        if platform not in ("google_ads", "meta_ads", "spotify_ads", "pinterest_ads"):
            raise ValueError("Plataforma no compatible con el detalle.")
        if level not in ("campaign", "ad"):
            raise ValueError("level debe ser campaign o ad.")
        if status not in ("all", "active", "inactive") or spend not in ("all", "with_spend", "without_spend"):
            raise ValueError("Filtros de estado o gasto no válidos.")

        if platform == "spotify_ads":
            if level == "ad" and not hasattr(self.providers[platform], "performance"):
                raise ValueError("Spotify Ads no expone detalle de anuncios.")
            sources_spotify = list(client.spotify_ads_sources or [])
            if not sources_spotify:
                return build_detail_response([], client_id=client.id, platform=platform, level=level,
                                             status=status, spend=spend,
                                             start_date=start.isoformat(), end_date=end.isoformat())
            source = sources_spotify[0]
            require_santuario_binding(client_slug=client.slug, business_id=source.business_id,
                                      ad_account_id=source.ad_account_id)
            period, _, cached = self.cache.get_or_set(
                ("spotify-detail", client.id, source.ad_account_id, start.isoformat(), end.isoformat()),
                lambda: self.providers[platform].performance(source.ad_account_id,
                                                            start.isoformat(), end.isoformat()),
                refresh=refresh,
            )
            catalogue = self.providers[platform].list_campaigns(source.ad_account_id)
            campaign_status = {str(item.get("campaign_id")): item.get("status") for item in catalogue}
            raw_rows = []
            entities = period.get("campaigns", []) if level == "campaign" else period.get("ads", [])
            for item in entities:
                entity_id = str(item.get("campaign_id") if level == "campaign" else item.get("ad_id") or "")
                if not entity_id:
                    continue
                raw_rows.append({
                    **item, "level": level, "platform": platform,
                    "campaign_id": item.get("campaign_id") or (entity_id if level == "campaign" else None),
                    "campaign_name": item.get("campaign_name"),
                    "ad_id": item.get("ad_id") if level == "ad" else None,
                    "ad_name": item.get("ad_name") if level == "ad" else None,
                    "status": item.get("status") or campaign_status.get(str(item.get("campaign_id"))),
                    "source_account_id": source.ad_account_id,
                    "currency": period.get("currency") or client.currency,
                    "conversions": None, "purchases": None, "leads": None,
                    "conversion_value": None, "value_available": False,
                })
            result = build_detail_response(raw_rows, client_id=client.id, platform=platform, level=level,
                                           status=status, spend=spend, start_date=start.isoformat(),
                                           end_date=end.isoformat())
            result["cached"] = cached
            result["currency"] = period.get("currency") or client.currency or "MXN"
            return result

        if platform == "pinterest_ads":
            sources_pinterest = list(client.pinterest_ads_sources or [])
            if not sources_pinterest:
                return build_detail_response([], client_id=client.id, platform=platform, level="campaign",
                                             status=status, spend=spend,
                                             start_date=start.isoformat(), end_date=end.isoformat())
            raw_rows = []
            for source in sources_pinterest:
                whole, partial = self._plan(client, platform, source, owners or {}, refresh)
                if partial or whole == []:
                    # Pinterest reporta campañas; no se atribuyen selecciones parciales artificialmente.
                    continue
                period, _, _ = self.cache.get_or_set(
                    ("pinterest-detail", client.id, source.ad_account_id, start.isoformat(), end.isoformat()),
                    lambda s=source, ids=whole: self.providers[platform].performance(
                        s.ad_account_id, start.isoformat(), end.isoformat(), ids
                    ),
                    refresh=refresh,
                )
                for item in period.get("campaigns", []):
                    raw_rows.append({
                        **item, "level": "campaign", "platform": platform,
                        "source_account_id": source.ad_account_id,
                        "currency": period.get("currency") or source.currency or client.currency,
                        "conversions": None, "purchases": item.get("checkouts"), "leads": None,
                        "conversion_value": item.get("conversion_value"),
                        "value_available": item.get("conversion_value") is not None,
                        "reach": None,
                    })
            result = build_detail_response(raw_rows, client_id=client.id, platform=platform, level="campaign",
                                           status=status, spend=spend, start_date=start.isoformat(),
                                           end_date=end.isoformat())
            result["currency"] = sources_pinterest[0].currency or client.currency
            return result

        sources = ad_sources(client, platform)
        owners = owners or {}
        planned: list[tuple[AdSource, tuple[list[str] | None, dict[str, list[str]]]]] = []
        for src in sources:
            planned.append((src, self._plan(client, platform, src, owners, refresh)))

        scope_key = tuple(
            (
                src.account_id,
                tuple(src.campaign_ids),
                src.allocation,
                tuple((item.campaign_id, tuple(item.adset_ids), item.allocation)
                      for item in (getattr(src, "adset_selections", None) or [])),
                None if plan[0] is None else tuple(plan[0]),
                tuple(sorted((campaign, tuple(ids)) for campaign, ids in plan[1].items())),
            )
            for src, plan in planned
        )
        cache_key = (
            "performance-detail", client.id, platform, start.isoformat(), end.isoformat(),
            level, status, spend, scope_key,
        )

        def fetch_rows() -> list[dict[str, Any]]:
            rows: dict[tuple[str, str], dict[str, Any]] = {}
            daily_by_campaign: dict[tuple[str, str], list[dict[str, Any]]] = {}
            provider = self.providers[platform]
            for src, (whole, partial) in planned:
                if whole == [] and not partial:
                    continue

                if level == "campaign":
                    if platform == "google_ads":
                        catalog = self._campaign_catalog(platform, src, refresh)
                        ids = None if whole is None else list(whole)
                        period, _, _ = self._period(client, platform, src.account_id, (start, end), refresh,
                                                    campaign_ids=ids, login_customer_id=src.login_customer_id)
                        for point in period.get("campaign_daily") or []:
                            cid = str(point.get("campaign_id") or "")
                            if cid:
                                daily_by_campaign.setdefault((src.account_id, cid), []).append({
                                    "date": point.get("date"), "spend": point.get("spend"),
                                    "conversions": point.get("conversions"), "leads": point.get("leads"),
                                })
                        metrics = {str(row.get("campaign_id")): row for row in period.get("campaigns") or []
                                   if whole is None or str(row.get("campaign_id")) in whole}
                        selected_ids = set(metrics)
                        selected_ids.update(str(row.get("campaign_id")) for row in catalog
                                            if whole is None or str(row.get("campaign_id")) in (whole or []))
                        by_id = {str(row.get("campaign_id")): row for row in catalog}
                        for campaign_id in selected_ids:
                            descriptive = by_id.get(campaign_id) or metrics.get(campaign_id) or {}
                            metric = metrics.get(campaign_id) or {}
                            row = {
                                **metric,
                                **descriptive,
                                "level": "campaign",
                                "platform": platform,
                                "campaign_id": campaign_id,
                                "campaign_name": descriptive.get("campaign_name") or metric.get("campaign_name"),
                                "status": descriptive.get("status") or metric.get("status"),
                                "source_account_id": src.account_id,
                                "currency": descriptive.get("currency") or period.get("currency") or client.currency,
                                "leads": metric.get("leads"),
                            }
                            row.update({field: metric.get(field, 0) for field in
                                        ("spend", "impressions", "clicks", "conversions", "conversion_value")})
                            rows[(src.account_id, campaign_id)] = row
                    else:
                        catalog = self._campaign_catalog(platform, src, refresh)
                        source_currency = next((item.get("currency") for item in catalog if item.get("currency")),
                                               client.currency)
                        selected: list[dict[str, Any]] = []
                        if whole is None or whole:
                            period, _, _ = self._period(
                                client, platform, src.account_id, (start, end), refresh,
                                campaign_ids=None if whole is None else list(whole),
                            )
                            selected.extend(period.get("campaigns") or [])
                            for point in period.get("campaign_daily") or []:
                                cid = str(point.get("campaign_id") or "")
                                if cid:
                                    daily_by_campaign.setdefault((src.account_id, cid), []).append({
                                        "date": point.get("date"), "spend": point.get("spend"),
                                        "conversions": point.get("conversions"),
                                        "purchases": point.get("purchases"), "leads": point.get("leads"),
                                    })
                        if partial:
                            adset_ids = sorted({adset for values in partial.values() for adset in values})
                            detail_key = ("detail-meta-adsets", client.id, src.account_id, start.isoformat(),
                                          end.isoformat(), tuple(sorted(partial.items())), tuple(adset_ids))
                            period, _, _ = self.cache.get_or_set(
                                detail_key,
                                lambda: provider.performance_adsets(src.account_id, start.isoformat(),
                                                                    end.isoformat(), adset_ids),
                                refresh=refresh,
                            )
                            selected.extend(period.get("campaigns") or [])
                        metric_by_id: dict[str, dict[str, Any]] = {}
                        for item in selected:
                            cid = str(item.get("campaign_id"))
                            if whole is not None and cid not in whole and cid not in partial:
                                continue
                            metric_by_id[cid] = {**metric_by_id.get(cid, {}), **item}
                        catalog_by_id = {str(item.get("campaign_id")): item for item in catalog
                                         if whole is None or str(item.get("campaign_id")) in (whole or []) or
                                         str(item.get("campaign_id")) in partial}
                        for cid in dict.fromkeys([*catalog_by_id, *metric_by_id]):
                            descriptive = catalog_by_id.get(cid, {})
                            metric = metric_by_id.get(cid, {})
                            row = {
                                **metric,
                                **descriptive,
                                "level": "campaign",
                                "platform": platform,
                                "campaign_id": cid,
                                "campaign_name": descriptive.get("campaign_name") or metric.get("campaign_name"),
                                "source_account_id": src.account_id,
                                "currency": metric.get("currency") or descriptive.get("currency") or source_currency,
                                "leads": None,
                                "purchases": metric.get("purchases"),
                            }
                            for field in ("spend", "impressions", "clicks", "conversions", "conversion_value"):
                                if field not in metric:
                                    row[field] = 0
                            rows[(src.account_id, cid)] = row
                    continue

                if platform == "google_ads":
                    detail_key = ("detail-ad", client.id, platform, src.account_id,
                                  start.isoformat(), end.isoformat(), None if whole is None else tuple(whole))
                    detail_rows, _, _ = self.cache.get_or_set(
                        detail_key,
                        lambda: provider.detail_ads(src.account_id, src.login_customer_id,
                                                    start.isoformat(), end.isoformat(),
                                                    None if whole is None else list(whole)),
                        refresh=refresh,
                    )
                    for item in detail_rows:
                        row = {**item, "level": "ad", "platform": platform,
                               "status": item.get("ad_status"), "source_account_id": src.account_id}
                        rows[(src.account_id, str(item.get("ad_id")))] = row
                    continue

                # Meta: el catálogo da status actual; Insights nivel ad da métricas
                # del rango. Cada alcance efectivo se consulta por separado para
                # respetar campañas completas y selecciones parciales de ad sets.
                source_catalog = self._campaign_catalog(platform, src, refresh)
                source_currency = next((item.get("currency") for item in source_catalog if item.get("currency")),
                                       client.currency)
                scopes: list[tuple[list[str] | None, list[str] | None]] = []
                if whole is None or whole:
                    scopes.append((None if whole is None else list(whole), None))
                if partial:
                    scopes.append((sorted(partial), sorted({a for values in partial.values() for a in values})))
                for campaign_scope, adset_scope in scopes:
                    key = ("detail-meta-ad", client.id, src.account_id, start.isoformat(), end.isoformat(),
                           None if campaign_scope is None else tuple(campaign_scope),
                           None if adset_scope is None else tuple(adset_scope))
                    def load_meta(scope_campaigns=campaign_scope, scope_adsets=adset_scope,
                                  account_currency=source_currency):
                        catalog = provider.list_ads(src.account_id, scope_campaigns, scope_adsets)
                        insights = provider.performance_ads(src.account_id, start.isoformat(), end.isoformat(),
                                                            scope_campaigns, scope_adsets)
                        metrics_by_id = {str(row.get("ad_id")): row for row in insights if row.get("ad_id") is not None}
                        result = []
                        for ad in catalog:
                            ad_id = str(ad.get("ad_id"))
                            metric = metrics_by_id.get(ad_id)
                            base = metric or {"spend": 0, "impressions": 0, "clicks": 0,
                                              "conversions": 0, "conversion_value": 0, "leads": None,
                                              "purchases": None}
                            result.append({**base, **ad,
                                           "currency": (metric or {}).get("currency") or account_currency,
                                           "level": "ad", "platform": platform, "status": ad.get("status")})
                            metrics_by_id.pop(ad_id, None)
                        # Si Insights devolvió una fila fuera del catálogo por lag entre endpoints,
                        # conservar el dato pero no inventar un status activo.
                        result.extend({**row, "currency": row.get("currency") or account_currency,
                                       "level": "ad", "platform": platform,
                                       "source_account_id": src.account_id} for row in metrics_by_id.values())
                        return result
                    detail_rows, _, _ = self.cache.get_or_set(key, load_meta, refresh=refresh)
                    for item in detail_rows:
                        rows[(src.account_id, str(item.get("ad_id")))] = item

            result = []
            for (account_id, campaign_id), row in rows.items():
                if (account_id, campaign_id) in daily_by_campaign:
                    row["daily"] = sorted(daily_by_campaign[(account_id, campaign_id)], key=lambda point: point.get("date") or "")
                result.append(row)
            return result

        all_rows, _, cached = self.cache.get_or_set(cache_key, fetch_rows, refresh=refresh)
        result = build_detail_response(
            all_rows, client_id=client.id, platform=platform, level=level,
            status=status, spend=spend, start_date=start.isoformat(), end_date=end.isoformat(),
        )
        result["cached"] = cached
        result["currency"] = client.currency or "MXN"
        return result

    def diagnostics(self, platform: str) -> dict[str, Any]:
        """Diagnóstico de accesos de una plataforma (no alimenta el registro)."""
        provider = self.providers[platform]
        if not hasattr(provider, "diagnostics"):
            return {"platform": platform, "status": "unsupported",
                    "message": f"{LABELS[platform]} no expone diagnóstico"}
        try:
            # El propio proveedor puede sobreescribir "status" (p. ej. error de permisos).
            return {"platform": platform, "status": "ok", **provider.diagnostics()}
        except ProviderNotConfigured as e:
            return {"platform": platform, "status": "not_configured", "detail": sanitize(e)}
        except Exception as e:
            log.warning("Diagnóstico %s falló: %s", LABELS[platform], sanitize(e))
            return {"platform": platform, "status": "error", "detail": sanitize(e)}

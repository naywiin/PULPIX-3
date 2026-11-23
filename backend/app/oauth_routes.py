"""Endpoints de autenticación, conexiones OAuth (Meta Ads y Google Ads) e importación rápida de clientes."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlencode

import requests
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field

from .auth import get_current_user, get_current_user_uid
from .config import Settings
from .connections import (
    ConnectionStoreError,
    UserConnectionsStore,
    create_oauth_state,
    verify_oauth_state,
)
from .models import ClientCreate, GoogleAdsSource, MetaAdsSource
from .registry import ClientRepository, RegistryError

log = logging.getLogger("mp.oauth")

GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_USERINFO_URL = "https://www.googleapis.com/oauth2/v2/userinfo"
GOOGLE_ADS_SCOPES = "https://www.googleapis.com/auth/adwords openid email profile"
META_SCOPES = "ads_read,business_management"


class ImportAccountItem(BaseModel):
    platform: str
    account_id: str
    name: str
    currency: str = "MXN"
    timezone: str = "America/Mexico_City"
    login_customer_id: str | None = None


class ImportClientsRequest(BaseModel):
    accounts: list[ImportAccountItem] = Field(default_factory=list)


def build_oauth_router(
    settings: Settings,
    connections: UserConnectionsStore,
    repo: ClientRepository,
    service: Any,
) -> APIRouter:
    router = APIRouter()

    # ------------------------------------------------------------ auth info
    @router.get("/api/auth/config")
    def auth_config() -> dict[str, Any]:
        return {
            "auth_required": settings.auth_required,
            "providers": ["google", "password"],
        }

    @router.get("/api/auth/me")
    def auth_me() -> dict[str, Any]:
        user = get_current_user()
        uid = get_current_user_uid()
        return {
            "user": user.as_dict() if user else {"uid": uid, "email": None, "name": None, "picture": None},
            "integrations": connections.status(uid),
        }

    @router.get("/api/integrations/status")
    def integrations_status() -> dict[str, Any]:
        return connections.status(get_current_user_uid())

    # ------------------------------------------------------------ Meta Ads OAuth
    @router.get("/api/integrations/meta/start")
    def meta_oauth_start() -> dict[str, str]:
        if not settings.meta_app_id or not settings.meta_app_secret:
            raise HTTPException(
                status_code=503,
                detail="Falta configurar META_APP_ID y META_APP_SECRET en el servidor.",
            )
        uid = get_current_user_uid()
        state = create_oauth_state(uid, "meta_ads", settings)
        redirect_uri = f"{settings.oauth_redirect_base_url}/api/integrations/meta/callback"
        params = urlencode({
            "client_id": settings.meta_app_id,
            "redirect_uri": redirect_uri,
            "state": state,
            "scope": META_SCOPES,
            "response_type": "code",
        })
        return {"auth_url": f"https://www.facebook.com/{settings.meta_api_version}/dialog/oauth?{params}"}

    @router.get("/api/integrations/meta/callback")
    def meta_oauth_callback(
        code: str | None = Query(default=None),
        state: str | None = Query(default=None),
        error: str | None = Query(default=None),
        error_description: str | None = Query(default=None),
    ):
        base_redirect = f"{settings.frontend_url}/#/settings-clients"
        if error:
            msg = error_description or error
            return RedirectResponse(f"{base_redirect}?oauth_error={urlencode({'m': msg})[2:]}")
        if not code or not state:
            return RedirectResponse(f"{base_redirect}?oauth_error=missing_code")
        try:
            uid = verify_oauth_state(state, "meta_ads", settings)
        except ValueError as exc:
            return RedirectResponse(f"{base_redirect}?oauth_error={urlencode({'m': str(exc)})[2:]}")

        redirect_uri = f"{settings.oauth_redirect_base_url}/api/integrations/meta/callback"
        graph_base = f"https://graph.facebook.com/{settings.meta_api_version}"
        try:
            short_resp = requests.get(
                f"{graph_base}/oauth/access_token",
                params={
                    "client_id": settings.meta_app_id,
                    "client_secret": settings.meta_app_secret,
                    "redirect_uri": redirect_uri,
                    "code": code,
                },
                timeout=30,
            )
            short_data = short_resp.json()
            short_token = short_data.get("access_token")
            if not short_token:
                raise RuntimeError(short_data.get("error", {}).get("message") or "No se obtuvo token de Meta.")

            # Intercambio por long-lived token (~60 días)
            long_resp = requests.get(
                f"{graph_base}/oauth/access_token",
                params={
                    "grant_type": "fb_exchange_token",
                    "client_id": settings.meta_app_id,
                    "client_secret": settings.meta_app_secret,
                    "fb_exchange_token": short_token,
                },
                timeout=30,
            )
            long_data = long_resp.json() if long_resp.ok else {}
            access_token = long_data.get("access_token") or short_token
            expires_in = int(long_data.get("expires_in") or short_data.get("expires_in") or 5184000)
            expires_at = (datetime.now(timezone.utc) + timedelta(seconds=expires_in)).isoformat()

            me_resp = requests.get(
                f"{graph_base}/me",
                params={"fields": "id,name"},
                headers={"Authorization": f"Bearer {access_token}"},
                timeout=20,
            )
            me_data = me_resp.json() if me_resp.ok else {}

            connections.save_connection(uid, "meta_ads", {
                "access_token": access_token,
                "expires_at": expires_at,
                "meta_user_id": me_data.get("id"),
                "name": me_data.get("name"),
                "account_label": me_data.get("name") or "Cuenta de Meta Ads",
            })
            service.discovery_cache.clear()
        except Exception as exc:
            log.error("Error en callback OAuth de Meta: %s", exc)
            return RedirectResponse(f"{base_redirect}?oauth_error={urlencode({'m': str(exc)})[2:]}")

        return RedirectResponse(f"{base_redirect}?connected=meta_ads")

    @router.delete("/api/integrations/meta")
    def meta_disconnect() -> dict[str, bool]:
        connections.delete_connection(get_current_user_uid(), "meta_ads")
        service.discovery_cache.clear()
        return {"disconnected": True}

    # ------------------------------------------------------------ Google Ads OAuth
    @router.get("/api/integrations/google-ads/start")
    def google_ads_oauth_start() -> dict[str, str]:
        if not settings.google_oauth_client_id or not settings.google_oauth_client_secret:
            raise HTTPException(
                status_code=503,
                detail="Falta configurar GOOGLE_OAUTH_CLIENT_ID y GOOGLE_OAUTH_CLIENT_SECRET en el servidor.",
            )
        if not settings.google_ads_developer_token:
            raise HTTPException(
                status_code=503,
                detail="Falta configurar GOOGLE_ADS_DEVELOPER_TOKEN en el servidor.",
            )
        uid = get_current_user_uid()
        state = create_oauth_state(uid, "google_ads", settings)
        redirect_uri = f"{settings.oauth_redirect_base_url}/api/integrations/google-ads/callback"
        params = urlencode({
            "client_id": settings.google_oauth_client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": GOOGLE_ADS_SCOPES,
            "access_type": "offline",
            "prompt": "consent",
            "include_granted_scopes": "true",
            "state": state,
        })
        return {"auth_url": f"{GOOGLE_AUTH_URL}?{params}"}

    @router.get("/api/integrations/google-ads/callback")
    def google_ads_oauth_callback(
        code: str | None = Query(default=None),
        state: str | None = Query(default=None),
        error: str | None = Query(default=None),
    ):
        base_redirect = f"{settings.frontend_url}/#/settings-clients"
        if error:
            return RedirectResponse(f"{base_redirect}?oauth_error={urlencode({'m': error})[2:]}")
        if not code or not state:
            return RedirectResponse(f"{base_redirect}?oauth_error=missing_code")
        try:
            uid = verify_oauth_state(state, "google_ads", settings)
        except ValueError as exc:
            return RedirectResponse(f"{base_redirect}?oauth_error={urlencode({'m': str(exc)})[2:]}")

        redirect_uri = f"{settings.oauth_redirect_base_url}/api/integrations/google-ads/callback"
        try:
            token_resp = requests.post(
                GOOGLE_TOKEN_URL,
                data={
                    "code": code,
                    "client_id": settings.google_oauth_client_id,
                    "client_secret": settings.google_oauth_client_secret,
                    "redirect_uri": redirect_uri,
                    "grant_type": "authorization_code",
                },
                timeout=30,
            )
            token_data = token_resp.json()
            refresh_token = token_data.get("refresh_token")
            access_token = token_data.get("access_token")
            if not refresh_token:
                # Si el usuario ya había autorizado antes, conservamos el refresh_token previo si existe.
                prev = connections.get_connection(uid, "google_ads")
                refresh_token = prev.get("refresh_token") if prev else None
            if not refresh_token:
                raise RuntimeError("Google no devolvió refresh_token. Intenta reconectar aceptando permisos.")

            email = None
            name = None
            if access_token:
                info_resp = requests.get(
                    GOOGLE_USERINFO_URL,
                    headers={"Authorization": f"Bearer {access_token}"},
                    timeout=20,
                )
                if info_resp.ok:
                    info = info_resp.json()
                    email = info.get("email")
                    name = info.get("name")

            connections.save_connection(uid, "google_ads", {
                "refresh_token": refresh_token,
                "email": email,
                "name": name,
                "account_label": email or name or "Cuenta de Google Ads",
            })
            service.discovery_cache.clear()
        except Exception as exc:
            log.error("Error en callback OAuth de Google Ads: %s", exc)
            return RedirectResponse(f"{base_redirect}?oauth_error={urlencode({'m': str(exc)})[2:]}")

        return RedirectResponse(f"{base_redirect}?connected=google_ads")

    @router.delete("/api/integrations/google-ads")
    def google_ads_disconnect() -> dict[str, bool]:
        connections.delete_connection(get_current_user_uid(), "google_ads")
        service.discovery_cache.clear()
        return {"disconnected": True}

    # ------------------------------------------------------------ Importación rápida de cuentas como clientes
    @router.post("/api/integrations/import-clients")
    def import_clients(body: ImportClientsRequest) -> dict[str, Any]:
        """Crea clientes en el espacio del usuario a partir de cuentas descubiertas de Meta/Google Ads."""
        created: list[dict[str, Any]] = []
        skipped: list[dict[str, Any]] = []
        existing = repo.list()
        existing_meta = {
            src.ad_account_id
            for c in existing
            for src in (c.meta_ads_sources or [])
            if not src.campaign_ids
        }
        existing_gads = {
            src.customer_id
            for c in existing
            for src in (c.google_ads_sources or [])
            if not src.campaign_ids
        }

        for item in body.accounts:
            clean_id = "".join(ch for ch in item.account_id if ch.isdigit())
            if not clean_id:
                skipped.append({"account_id": item.account_id, "reason": "ID inválido"})
                continue
            if item.platform == "meta_ads" and clean_id in existing_meta:
                skipped.append({"account_id": clean_id, "reason": "Ya vinculada a un cliente"})
                continue
            if item.platform == "google_ads" and clean_id in existing_gads:
                skipped.append({"account_id": clean_id, "reason": "Ya vinculada a un cliente"})
                continue

            payload_kwargs: dict[str, Any] = {
                "name": (item.name or f"Cliente {clean_id}").strip()[:80],
                "currency": (item.currency or "MXN").upper()[:3],
                "timezone": item.timezone or "America/Mexico_City",
                "active": True,
            }
            if item.platform == "meta_ads":
                payload_kwargs["meta_ad_account_id"] = clean_id
                payload_kwargs["meta_ads_sources"] = [
                    MetaAdsSource(ad_account_id=clean_id, campaign_ids=[], allocation="exclusive")
                ]
                existing_meta.add(clean_id)
            elif item.platform == "google_ads":
                payload_kwargs["google_ads_customer_id"] = clean_id
                payload_kwargs["google_ads_login_customer_id"] = item.login_customer_id
                payload_kwargs["google_ads_sources"] = [
                    GoogleAdsSource(
                        customer_id=clean_id,
                        login_customer_id=item.login_customer_id,
                        campaign_ids=[],
                        allocation="exclusive",
                    )
                ]
                existing_gads.add(clean_id)
            else:
                skipped.append({"account_id": clean_id, "reason": f"Plataforma no soportada: {item.platform}"})
                continue

            try:
                client = repo.create(ClientCreate(**payload_kwargs))
                created.append(client.model_dump(mode="json"))
            except (RegistryError, ConnectionStoreError, ValueError) as exc:
                skipped.append({"account_id": clean_id, "reason": str(exc)})

        return {"created": created, "skipped": skipped}

    return router

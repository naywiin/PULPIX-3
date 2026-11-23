"""Proveedores multi-usuario para Meta Ads y Google Ads.

Cada petición utiliza el token OAuth cifrado del usuario autenticado (`uid`),
permitiendo que cualquier usuario conecte sus propias cuentas de Meta Ads y
Google Ads y descubra automáticamente a sus clientes.
"""
from __future__ import annotations

import hashlib
import logging
from typing import Any

from .auth import get_current_user_uid
from .config import Settings
from .connections import ConnectionStoreError, UserConnectionsStore
from .providers import meta as meta_module
from .providers.base import ProviderError, ProviderNotConfigured, sanitize
from .providers.google_ads import CUSTOMER_QUERY, HIERARCHY_QUERY, GoogleAdsProvider, _enum_name
from .providers.meta import MetaProvider

log = logging.getLogger("mp.user_providers")

# En Pulpix no se heredan conversiones personalizadas hardcodeadas de terceros.
meta_module.PURCHASE_ACTION_PRIORITY_BY_ACCOUNT.clear()


class UserMetaProvider(MetaProvider):
    """MetaProvider que usa el token OAuth del usuario autenticado."""

    def __init__(self, settings: Settings, connections: UserConnectionsStore) -> None:
        super().__init__(settings)
        self.connections = connections

    def _headers(self) -> dict[str, str]:
        uid = get_current_user_uid()
        token: str | None = None
        try:
            conn = self.connections.get_connection(uid, "meta_ads")
            if conn:
                token = conn.get("access_token")
        except ConnectionStoreError as exc:
            raise ProviderNotConfigured(str(exc)) from None

        if not token:
            token = self.settings.meta_access_token
        if not token:
            raise ProviderNotConfigured(
                "Conecta tu cuenta de Meta Ads en Settings → Clientes para ver tus cuentas y campañas."
            )
        return {"Authorization": f"Bearer {token}"}


class UserGoogleAdsProvider(GoogleAdsProvider):
    """GoogleAdsProvider que usa el refresh_token OAuth del usuario autenticado."""

    def __init__(self, settings: Settings, connections: UserConnectionsStore) -> None:
        super().__init__(settings)
        self.connections = connections
        self._user_clients: dict[tuple[str, str | None, str], Any] = {}

    def _user_refresh_token(self) -> str | None:
        uid = get_current_user_uid()
        try:
            conn = self.connections.get_connection(uid, "google_ads")
            return conn.get("refresh_token") if conn else None
        except ConnectionStoreError as exc:
            raise ProviderNotConfigured(str(exc)) from None

    def _client(self, login_customer_id: str | None = None):
        refresh_token = self._user_refresh_token()
        if not refresh_token:
            # Respaldo opcional para entornos locales con ADC + MCC configurado.
            if self.settings.google_ads_manager_customer_id and self.settings.google_ads_developer_token:
                return super()._client(login_customer_id)
            raise ProviderNotConfigured(
                "Conecta tu cuenta de Google Ads en Settings → Clientes para ver tus cuentas y campañas."
            )

        if not self.settings.google_ads_developer_token:
            raise ProviderNotConfigured("Falta GOOGLE_ADS_DEVELOPER_TOKEN en el servidor.")
        if not self.settings.google_oauth_client_id or not self.settings.google_oauth_client_secret:
            raise ProviderNotConfigured("Faltan GOOGLE_OAUTH_CLIENT_ID / GOOGLE_OAUTH_CLIENT_SECRET en el servidor.")

        uid = get_current_user_uid()
        token_hash = hashlib.sha256(refresh_token.encode("utf-8")).hexdigest()[:12]
        cache_key = (uid, login_customer_id, token_hash)

        with self._lock:
            if cache_key not in self._user_clients:
                try:
                    from google.ads.googleads.client import GoogleAdsClient
                except ImportError as exc:  # pragma: no cover
                    raise ProviderError("Librería google-ads no instalada en el backend") from exc

                config: dict[str, Any] = {
                    "developer_token": self.settings.google_ads_developer_token,
                    "client_id": self.settings.google_oauth_client_id,
                    "client_secret": self.settings.google_oauth_client_secret,
                    "refresh_token": refresh_token,
                    "use_proto_plus": True,
                }
                if login_customer_id:
                    config["login_customer_id"] = login_customer_id
                try:
                    self._user_clients[cache_key] = GoogleAdsClient.load_from_dict(config)
                except Exception as exc:
                    raise ProviderError(f"No se pudo inicializar Google Ads OAuth: {sanitize(exc)}") from exc
            return self._user_clients[cache_key]

    def list_accounts(self, include_hidden: bool = False, include_root: bool = False) -> list[dict[str, Any]]:
        """Descubre todas las cuentas Google Ads accesibles por el usuario autenticado.

        Usa `list_accessible_customers()`: si una cuenta accesible es un MCC,
        expande su jerarquía (`customer_client`); si es una cuenta anunciante
        directa, la incluye directamente.
        """
        refresh_token = self._user_refresh_token()
        if not refresh_token and self.settings.google_ads_manager_customer_id:
            return super().list_accounts(include_hidden=include_hidden, include_root=include_root)

        client = self._client(login_customer_id=None)
        customer_service = client.get_service("CustomerService")
        try:
            accessible = customer_service.list_accessible_customers()
            resource_names = list(accessible.resource_names or [])
        except Exception as exc:
            raise ProviderError(f"No se pudieron listar las cuentas de Google Ads: {sanitize(exc)}") from exc

        accounts: dict[str, dict[str, Any]] = {}

        for resource in resource_names:
            cid = str(resource).split("/")[-1].replace("-", "").strip()
            if not cid.isdigit():
                continue
            try:
                rows = list(self._search(cid, CUSTOMER_QUERY, login_customer_id=cid))
            except ProviderError as exc:
                log.debug("No se pudo leer detalle de cuenta %s: %s", cid, exc)
                continue
            if not rows:
                continue
            cust = rows[0].customer
            is_manager = bool(cust.manager)

            if is_manager:
                if include_root:
                    accounts.setdefault(cid, {
                        "platform": "google_ads",
                        "id": cid,
                        "name": cust.descriptive_name or f"MCC {cid}",
                        "currency": cust.currency_code or None,
                        "timezone": cust.time_zone or None,
                        "status": _enum_name(cust.status),
                        "account_type": "manager",
                        "level": 0,
                        "test_account": bool(cust.test_account),
                        "hidden": False,
                        "login_customer_id": cid,
                        "access": "MCC propio",
                    })
                try:
                    hierarchy = [r.customer_client for r in self._search(cid, HIERARCHY_QUERY, login_customer_id=cid)]
                except ProviderError as exc:
                    log.warning("No se pudo expandir MCC %s: %s", cid, exc)
                    continue
                for cc in hierarchy:
                    if not include_root and int(cc.level) == 0:
                        continue
                    if not include_hidden and cc.hidden:
                        continue
                    sub_id = str(cc.id)
                    level = int(cc.level)
                    entry = {
                        "platform": "google_ads",
                        "id": sub_id,
                        "name": cc.descriptive_name or None,
                        "currency": cc.currency_code or None,
                        "timezone": cc.time_zone or None,
                        "status": _enum_name(cc.status),
                        "account_type": "manager" if cc.manager else "client",
                        "level": level,
                        "test_account": bool(cc.test_account),
                        "hidden": bool(cc.hidden),
                        "login_customer_id": cid,
                        "access": f"vía MCC {cid} · nivel {level}",
                    }
                    prev = accounts.get(sub_id)
                    if prev is None or entry["level"] < prev["level"]:
                        accounts[sub_id] = entry
            else:
                accounts.setdefault(cid, {
                    "platform": "google_ads",
                    "id": cid,
                    "name": cust.descriptive_name or None,
                    "currency": cust.currency_code or None,
                    "timezone": cust.time_zone or None,
                    "status": _enum_name(cust.status),
                    "account_type": "client",
                    "level": 0,
                    "test_account": bool(cust.test_account),
                    "hidden": False,
                    "login_customer_id": None,
                    "access": "acceso directo",
                })

        return sorted(accounts.values(), key=lambda a: ((a["status"] != "ENABLED"), (a["name"] or "").lower()))

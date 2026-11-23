"""Almacenamiento cifrado de credenciales OAuth (Meta Ads y Google Ads) por usuario.

Los tokens NUNCA se devuelven al navegador ni se escriben en texto plano en
producción: se cifran con Fernet usando `PULPIX_TOKEN_ENCRYPTION_KEY` y se
guardan aislados por `uid` (en archivo local o en Firestore según `MP_STORE`).
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import secrets
import tempfile
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from cryptography.fernet import Fernet, InvalidToken

from .config import Settings

log = logging.getLogger("mp.connections")

SUPPORTED_PROVIDERS = ("meta_ads", "google_ads")


class ConnectionStoreError(Exception):
    """Error seguro al leer/escribir conexiones OAuth de usuario."""


def _safe_uid(uid: str) -> str:
    cleaned = "".join(ch for ch in uid if ch.isalnum() or ch in ("-", "_"))
    if not cleaned:
        raise ConnectionStoreError("Identificador de usuario inválido.")
    return cleaned


def _resolve_fernet(settings: Settings) -> Fernet:
    raw_key = settings.token_encryption_key
    if raw_key:
        try:
            return Fernet(raw_key.encode("utf-8"))
        except Exception as exc:
            raise ConnectionStoreError(
                "PULPIX_TOKEN_ENCRYPTION_KEY no es una clave Fernet válida (usa Fernet.generate_key())."
            ) from exc
    if settings.auth_required and os.environ.get("K_SERVICE"):
        raise ConnectionStoreError(
            "Falta PULPIX_TOKEN_ENCRYPTION_KEY en producción para cifrar tokens OAuth."
        )
    # Solo desarrollo local: clave derivada determinísticamente para no romper pruebas locales.
    digest = hashlib.sha256(b"pulpix-local-dev-encryption-key").digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def _state_secret(settings: Settings) -> bytes:
    key = settings.token_encryption_key or "pulpix-local-dev-state-secret"
    return hashlib.sha256(key.encode("utf-8")).digest()


def create_oauth_state(uid: str, provider: str, settings: Settings, ttl_seconds: int = 600) -> str:
    """Genera un parámetro `state` firmado (HMAC-SHA256) para proteger el callback OAuth."""
    payload = {
        "uid": uid,
        "provider": provider,
        "nonce": secrets.token_urlsafe(12),
        "exp": int(time.time()) + ttl_seconds,
    }
    raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    b64 = base64.urlsafe_b64encode(raw).decode("ascii")
    sig = hmac.new(_state_secret(settings), b64.encode("ascii"), hashlib.sha256).hexdigest()
    return f"{b64}.{sig}"


def verify_oauth_state(state: str, expected_provider: str, settings: Settings) -> str:
    """Verifica el `state` firmado y devuelve el `uid` que inició el flujo OAuth."""
    if not state or "." not in state:
        raise ValueError("Parámetro state inválido.")
    b64, sig = state.rsplit(".", 1)
    expected_sig = hmac.new(_state_secret(settings), b64.encode("ascii"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(sig, expected_sig):
        raise ValueError("Firma de state OAuth inválida.")
    try:
        payload = json.loads(base64.urlsafe_b64decode(b64.encode("ascii")).decode("utf-8"))
    except Exception as exc:
        raise ValueError("Contenido de state OAuth corrupto.") from exc
    if payload.get("provider") != expected_provider:
        raise ValueError("El state OAuth no corresponde a esta plataforma.")
    if int(payload.get("exp") or 0) < int(time.time()):
        raise ValueError("La sesión de conexión OAuth expiró. Intenta conectar nuevamente.")
    uid = str(payload.get("uid") or "").strip()
    if not uid:
        raise ValueError("State OAuth sin usuario.")
    return uid


class UserConnectionsStore:
    """Guarda y recupera tokens OAuth cifrados por `uid` y proveedor."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._lock = threading.RLock()
        self._firestore_client = None
        self._memory: dict[str, dict[str, str]] = {}

    @property
    def backend(self) -> str:
        return (self.settings.store_backend or "file").lower()

    def _fernet(self) -> Fernet:
        return _resolve_fernet(self.settings)

    def _encrypt(self, data: dict[str, Any]) -> str:
        raw = json.dumps(data, ensure_ascii=False).encode("utf-8")
        return self._fernet().encrypt(raw).decode("ascii")

    def _decrypt(self, token: str) -> dict[str, Any]:
        try:
            raw = self._fernet().decrypt(token.encode("ascii"))
            parsed = json.loads(raw.decode("utf-8"))
            return parsed if isinstance(parsed, dict) else {}
        except (InvalidToken, ValueError) as exc:
            raise ConnectionStoreError("No se pudo descifrar la conexión guardada. Reconecta tu cuenta.") from exc

    def _file_path(self, uid: str) -> Path:
        return self.settings.data_dir / "users" / _safe_uid(uid) / "connections.json"

    def _db(self):
        with self._lock:
            if self._firestore_client is None:
                from google.cloud import firestore

                kwargs: dict[str, Any] = {}
                if self.settings.firestore_project:
                    kwargs["project"] = self.settings.firestore_project
                if self.settings.firestore_database:
                    kwargs["database"] = self.settings.firestore_database
                self._firestore_client = firestore.Client(**kwargs)
            return self._firestore_client

    def _load_raw_map(self, uid: str) -> dict[str, str]:
        if self.backend == "memory":
            with self._lock:
                return dict(self._memory.get(uid, {}))
        if self.backend == "firestore":
            col = self._db().collection(self.settings.users_firestore_collection).document(_safe_uid(uid)).collection("connections")
            out: dict[str, str] = {}
            for doc in col.stream():
                data = doc.to_dict() or {}
                if isinstance(data.get("ciphertext"), str):
                    out[doc.id] = data["ciphertext"]
            return out
        path = self._file_path(uid)
        with self._lock:
            if not path.exists():
                return {}
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                return data if isinstance(data, dict) else {}
            except (OSError, ValueError) as exc:
                raise ConnectionStoreError(f"Error leyendo conexiones: {exc}") from exc

    def _save_raw_entry(self, uid: str, provider: str, ciphertext: str) -> None:
        if self.backend == "memory":
            with self._lock:
                self._memory.setdefault(uid, {})[provider] = ciphertext
            return
        if self.backend == "firestore":
            doc = (
                self._db()
                .collection(self.settings.users_firestore_collection)
                .document(_safe_uid(uid))
                .collection("connections")
                .document(provider)
            )
            doc.set({"ciphertext": ciphertext, "updated_at": datetime.now(timezone.utc).isoformat()})
            return
        path = self._file_path(uid)
        with self._lock:
            current = self._load_raw_map(uid)
            current[provider] = ciphertext
            path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".connections.", suffix=".json")
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as handle:
                    json.dump(current, handle, ensure_ascii=False, indent=2)
                    handle.write("\n")
                os.replace(tmp, path)
            except OSError as exc:
                Path(tmp).unlink(missing_ok=True)
                raise ConnectionStoreError(f"Error guardando conexión: {exc}") from exc

    def save_connection(self, uid: str, provider: str, data: dict[str, Any]) -> None:
        if provider not in SUPPORTED_PROVIDERS:
            raise ConnectionStoreError(f"Proveedor no soportado: {provider}")
        record = {
            **data,
            "connected_at": datetime.now(timezone.utc).isoformat(),
        }
        self._save_raw_entry(uid, provider, self._encrypt(record))

    def get_connection(self, uid: str, provider: str) -> dict[str, Any] | None:
        raw_map = self._load_raw_map(uid)
        ciphertext = raw_map.get(provider)
        if not ciphertext:
            return None
        return self._decrypt(ciphertext)

    def delete_connection(self, uid: str, provider: str) -> None:
        if self.backend == "memory":
            with self._lock:
                self._memory.get(uid, {}).pop(provider, None)
            return
        if self.backend == "firestore":
            (
                self._db()
                .collection(self.settings.users_firestore_collection)
                .document(_safe_uid(uid))
                .collection("connections")
                .document(provider)
                .delete()
            )
            return
        path = self._file_path(uid)
        with self._lock:
            current = self._load_raw_map(uid)
            if provider not in current:
                return
            current.pop(provider, None)
            path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".connections.", suffix=".json")
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as handle:
                    json.dump(current, handle, ensure_ascii=False, indent=2)
                    handle.write("\n")
                os.replace(tmp, path)
            except OSError as exc:
                Path(tmp).unlink(missing_ok=True)
                raise ConnectionStoreError(f"Error eliminando conexión: {exc}") from exc

    def status(self, uid: str) -> dict[str, Any]:
        """Resumen público seguro para el frontend (nunca expone tokens)."""
        result: dict[str, Any] = {}
        for provider in SUPPORTED_PROVIDERS:
            try:
                conn = self.get_connection(uid, provider)
            except ConnectionStoreError:
                conn = None
            if conn:
                result[provider] = {
                    "connected": True,
                    "account_label": conn.get("account_label") or conn.get("email") or conn.get("name"),
                    "connected_at": conn.get("connected_at"),
                    "expires_at": conn.get("expires_at"),
                    "oauth_configured": self._is_oauth_configured(provider),
                }
            else:
                result[provider] = {
                    "connected": False,
                    "account_label": None,
                    "connected_at": None,
                    "expires_at": None,
                    "oauth_configured": self._is_oauth_configured(provider),
                }
        return result

    def _is_oauth_configured(self, provider: str) -> bool:
        if provider == "meta_ads":
            return bool(self.settings.meta_app_id and self.settings.meta_app_secret)
        if provider == "google_ads":
            return bool(
                self.settings.google_oauth_client_id
                and self.settings.google_oauth_client_secret
                and self.settings.google_ads_developer_token
            )
        return False

"""Persistencia del registro de clientes (aislada por usuario en Pulpix).

Soporta:
- `MemoryClientStore`    : pruebas en memoria.
- `FileClientStore`      : archivo JSON con escritura atómica.
- `FirestoreClientStore` : Firestore (producción en Cloud Run).
- `UserScopedClientStore`: delega automáticamente a un store independiente por
  cada `uid` autenticado (`users/{uid}/clients` en Firestore o
  `data/users/{uid}/clients.json` en disco).
"""
from __future__ import annotations

import json
import logging
import os
import tempfile
import threading
from pathlib import Path
from typing import Any, Protocol

from .auth import get_current_user_uid

log = logging.getLogger("mp.store")

Payload = dict[str, Any]


class ClientStore(Protocol):
    """Persistencia cruda del registro. No valida ni migra: solo guarda y lee."""

    name: str

    def load(self) -> Payload: ...
    def save(self, payload: Payload) -> None: ...
    def is_empty(self) -> bool: ...


def _empty() -> Payload:
    return {"clients": []}


def _safe_uid(uid: str) -> str:
    cleaned = "".join(ch for ch in uid if ch.isalnum() or ch in ("-", "_"))
    return cleaned or "anonymous"


class MemoryClientStore:
    """Store en memoria: solo para pruebas. Se pierde con el proceso."""

    name = "memory"

    def __init__(self, payload: Payload | None = None) -> None:
        self._payload: Payload = json.loads(json.dumps(payload)) if payload else _empty()
        self._lock = threading.RLock()

    def load(self) -> Payload:
        with self._lock:
            return json.loads(json.dumps(self._payload))

    def save(self, payload: Payload) -> None:
        with self._lock:
            self._payload = json.loads(json.dumps(payload))

    def is_empty(self) -> bool:
        return not self.load().get("clients")


class FileClientStore:
    """Un archivo JSON con escritura atómica."""

    name = "file"

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._lock = threading.RLock()

    def load(self) -> Payload:
        with self._lock:
            if not self.path.exists():
                return _empty()
            try:
                data = json.loads(self.path.read_text(encoding="utf-8"))
            except (OSError, ValueError) as e:
                raise StoreError(f"No se pudo leer el registro en {self.path}: {e}") from e
            return data if isinstance(data, dict) else _empty()

    def save(self, payload: Payload) -> None:
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=self.path.parent, prefix=".clients.", suffix=".json")
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    json.dump(payload, f, ensure_ascii=False, indent=2)
                    f.write("\n")
                os.replace(tmp, self.path)
            except OSError as e:
                Path(tmp).unlink(missing_ok=True)
                raise StoreError(f"No se pudo guardar el registro en {self.path}: {e}") from e

    def is_empty(self) -> bool:
        return not self.load().get("clients")


class FirestoreClientStore:
    """Firestore: el registro sobrevive a revisiones, instancias y escalados."""

    name = "firestore"
    META_DOC = "_meta"

    def __init__(self, collection: str = "clients", project: str | None = None,
                 database: str | None = None, parent_doc_path: tuple[str, str] | None = None) -> None:
        self.collection = collection
        self.project = project
        self.database = database
        self.parent_doc_path = parent_doc_path
        self._client = None
        self._lock = threading.RLock()

    def _db(self):
        with self._lock:
            if self._client is None:
                try:
                    from google.cloud import firestore
                except ImportError as e:  # pragma: no cover
                    raise StoreError(
                        "Falta la librería google-cloud-firestore en el backend. "
                        "Instálala (está en requirements.txt) para usar MP_STORE=firestore."
                    ) from e
                kwargs: dict[str, Any] = {}
                if self.project:
                    kwargs["project"] = self.project
                if self.database:
                    kwargs["database"] = self.database
                try:
                    self._client = firestore.Client(**kwargs)
                except Exception as e:
                    raise StoreError(f"No se pudo conectar con Firestore: {type(e).__name__}") from e
            return self._client

    def _col(self):
        db = self._db()
        if self.parent_doc_path:
            parent_col, parent_doc = self.parent_doc_path
            return db.collection(parent_col).document(parent_doc).collection(self.collection)
        return db.collection(self.collection)

    def load(self) -> Payload:
        col = self._col()
        try:
            docs = list(col.stream())
        except Exception as e:
            raise StoreError(f"No se pudo leer Firestore: {type(e).__name__}") from e
        clients: list[dict[str, Any]] = []
        meta: dict[str, Any] = {}
        for doc in docs:
            data = doc.to_dict() or {}
            if doc.id == self.META_DOC:
                meta = data
                continue
            clients.append({**data, "id": data.get("id") or doc.id})
        clients.sort(key=lambda c: str(c.get("_order", "")) or str(c.get("name", "")).lower())
        payload: Payload = {**meta, "clients": clients}
        return payload

    def save(self, payload: Payload) -> None:
        db = self._db()
        col = self._col()
        clients = payload.get("clients") or []
        keep = {str(c["id"]) for c in clients if c.get("id")}
        try:
            batch = db.batch()
            for i, client in enumerate(clients):
                cid = str(client["id"])
                batch.set(col.document(cid), {**client, "_order": f"{i:04d}"})
            batch.set(
                col.document(self.META_DOC),
                {key: value for key, value in payload.items() if key != "clients"},
            )
            for doc in col.list_documents():
                if doc.id != self.META_DOC and doc.id not in keep:
                    batch.delete(doc)
            batch.commit()
        except StoreError:
            raise
        except Exception as e:
            raise StoreError(f"No se pudo guardar en Firestore: {type(e).__name__}") from e

    def is_empty(self) -> bool:
        col = self._col()
        try:
            for doc in col.list_documents():
                if doc.id != self.META_DOC:
                    return False
        except Exception as e:
            raise StoreError(f"No se pudo consultar Firestore: {type(e).__name__}") from e
        return True


class UserScopedClientStore:
    """Aísla el registro de clientes por usuario autenticado (`uid`)."""

    def __init__(self, settings) -> None:
        self.settings = settings
        self.name = (settings.store_backend or "file").lower()
        self._stores: dict[str, ClientStore] = {}
        self._lock = threading.RLock()

    def _for_current_user(self) -> ClientStore:
        uid = _safe_uid(get_current_user_uid())
        with self._lock:
            store = self._stores.get(uid)
            if store is None:
                backend = self.name
                if backend == "memory":
                    store = MemoryClientStore()
                elif backend == "firestore":
                    store = FirestoreClientStore(
                        collection=self.settings.firestore_collection,
                        project=self.settings.firestore_project,
                        database=self.settings.firestore_database,
                        parent_doc_path=(self.settings.users_firestore_collection, uid),
                    )
                elif backend == "file":
                    store = FileClientStore(self.settings.data_dir / "users" / uid / "clients.json")
                else:
                    raise StoreError(f"MP_STORE desconocido: {backend!r}. Usa file, firestore o memory.")
                self._stores[uid] = store
            return store

    def load(self) -> Payload:
        return self._for_current_user().load()

    def save(self, payload: Payload) -> None:
        self._for_current_user().save(payload)

    def is_empty(self) -> bool:
        return self._for_current_user().is_empty()


class StoreError(Exception):
    """Fallo de la capa de persistencia. El mensaje es seguro para mostrarse."""


def create_store(settings) -> ClientStore:
    """Construye el store según `MP_STORE`. Por defecto, archivo."""
    backend = (settings.store_backend or "file").lower()
    if backend == "memory":
        return MemoryClientStore()
    if backend == "firestore":
        return FirestoreClientStore(
            collection=settings.firestore_collection,
            project=settings.firestore_project,
            database=settings.firestore_database,
        )
    if backend != "file":
        raise StoreError(f"MP_STORE desconocido: {backend!r}. Usa file, firestore o memory.")
    return FileClientStore(settings.clients_file)

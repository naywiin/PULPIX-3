"""Dónde viven REALMENTE los datos del registro de clientes.

El problema que resuelve este módulo
------------------------------------
Hasta ahora el registro se leía y escribía en `backend/data/clients.json`, que
el Dockerfile copia DENTRO de la imagen. En Cloud Run eso significa que:

- cada escritura va al sistema de archivos efímero del contenedor;
- cada instancia tiene su propia copia divergente;
- al reciclarse una instancia, o al escalar a cero, lo escrito se pierde;
- **al desplegar una revisión nueva el archivo vuelve al de la imagen**, así que
  la configuración del usuario desaparece.

Por eso la persistencia se separa de la lógica del registro: `ClientStore` solo
carga y guarda documentos; quién valida, migra y resuelve conflictos sigue
siendo `registry.ClientRegistry`.

Backends
--------
- `MemoryClientStore`    : pruebas. No persiste nada a propósito.
- `FileClientStore`      : desarrollo local, o Cloud Run con un volumen montado
                           (bucket de Cloud Storage). Escritura atómica.
- `FirestoreClientStore` : producción. Sobrevive a revisiones e instancias.

El backend se elige con `MP_STORE` (ver `config.Settings.store_backend`).
"""
from __future__ import annotations

import json
import logging
import os
import tempfile
import threading
from pathlib import Path
from typing import Any, Protocol

log = logging.getLogger("mp.store")

#: Envoltorio del documento guardado. `clients` es la lista de clientes.
Payload = dict[str, Any]


class ClientStore(Protocol):
    """Persistencia cruda del registro. No valida ni migra: solo guarda y lee."""

    #: Nombre legible del backend, para logs y diagnóstico.
    name: str

    def load(self) -> Payload: ...
    def save(self, payload: Payload) -> None: ...
    def is_empty(self) -> bool: ...


def _empty() -> Payload:
    return {"clients": []}


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
    """Un archivo JSON. Sirve para desarrollo local y para un volumen montado.

    AVISO: si la ruta apunta dentro de la imagen del contenedor, esto NO es
    almacenamiento duradero en Cloud Run (ver la cabecera del módulo). Solo es
    duradero si la ruta está en un volumen persistente montado.
    """

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
            # Escritura atómica: un fallo a mitad nunca deja el registro a medias.
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
    """Firestore: el registro sobrevive a revisiones, instancias y escalados.

    Cada cliente es un documento de la colección (`MP_FIRESTORE_COLLECTION`,
    por defecto `clients`), con su `id` como identificador del documento. La
    versión de esquema vive en un documento aparte (`_meta`) para no confundirse
    con un cliente.

    La librería se importa aquí dentro a propósito: quien use el backend de
    archivo no necesita tener instalado `google-cloud-firestore`.
    """

    name = "firestore"
    META_DOC = "_meta"

    def __init__(self, collection: str = "clients", project: str | None = None,
                 database: str | None = None) -> None:
        self.collection = collection
        self.project = project
        self.database = database
        self._client = None
        self._lock = threading.RLock()

    def _db(self):
        with self._lock:
            if self._client is None:
                try:
                    from google.cloud import firestore
                except ImportError as e:  # pragma: no cover - depende del entorno
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

    def load(self) -> Payload:
        col = self._db().collection(self.collection)
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
        # Orden estable: el registro no tiene un orden natural en Firestore.
        clients.sort(key=lambda c: str(c.get("_order", "")) or str(c.get("name", "")).lower())
        # Conserva toda la metadata del registro, incluidos los respaldos
        # lógicos backup_vN creados antes de una migración. Guardar solo
        # schema_version haría que Firestore perdiera precisamente el backup.
        payload: Payload = {**meta, "clients": clients}
        return payload

    def save(self, payload: Payload) -> None:
        db = self._db()
        col = db.collection(self.collection)
        clients = payload.get("clients") or []
        keep = {str(c["id"]) for c in clients if c.get("id")}
        try:
            batch = db.batch()
            for i, client in enumerate(clients):
                cid = str(client["id"])
                # `_order` conserva el orden del registro entre lecturas.
                batch.set(col.document(cid), {**client, "_order": f"{i:04d}"})
            batch.set(
                col.document(self.META_DOC),
                {key: value for key, value in payload.items() if key != "clients"},
            )
            # Lo que ya no está en el registro se borra explícitamente.
            for doc in col.list_documents():
                if doc.id != self.META_DOC and doc.id not in keep:
                    batch.delete(doc)
            batch.commit()
        except StoreError:
            raise
        except Exception as e:
            raise StoreError(f"No se pudo guardar en Firestore: {type(e).__name__}") from e

    def is_empty(self) -> bool:
        col = self._db().collection(self.collection)
        try:
            for doc in col.list_documents():
                if doc.id != self.META_DOC:
                    return False
        except Exception as e:
            raise StoreError(f"No se pudo consultar Firestore: {type(e).__name__}") from e
        return True


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

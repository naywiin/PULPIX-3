"""Registro central de clientes.

`ClientRepository` es la interfaz; `JsonClientRepository` la implementa sobre
un archivo JSON (data/clients.json). Para migrar a una base de datos basta con
crear otra implementación con los mismos métodos y pasarla a `create_app`.
"""
from __future__ import annotations

import re
import threading
import unicodedata
from pathlib import Path
from typing import Protocol

from .exclusions import EXCLUDED_CLIENT_PATTERNS, is_excluded
from .migrations import SCHEMA_VERSION, migrate
from .stores import ClientStore, FileClientStore
from .models import (
    ACCOUNT_FIELDS,
    AD_PLATFORMS,
    Client,
    ClientCreate,
    ClientUpdate,
    account_ids,
    ad_sources,
    exclusive_claims,
    key_level,
)


class RegistryError(Exception):
    status_code = 400


class ClientNotFound(RegistryError):
    status_code = 404


class ClientConflict(RegistryError):
    status_code = 409


def slugify(text: str) -> str:
    norm = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", norm.lower()).strip("-") or "cliente"


def _reject_excluded(name: str) -> None:
    if is_excluded(name):
        raise RegistryError(
            f"'{name}' está en la lista de exclusión de la aplicación "
            f"({', '.join(EXCLUDED_CLIENT_PATTERNS)}): no se puede dar de alta aquí."
        )


#: (plataforma, campo suelto histórico, lista canónica de fuentes, clave del ID)
AD_SYNC = (
    ("google_ads", "google_ads_customer_id", "google_ads_sources", "customer_id"),
    ("meta_ads", "meta_ad_account_id", "meta_ads_sources", "ad_account_id"),
)


def _sync_ads_patch(merged: dict, patch: dict) -> None:
    """Deja coherentes el campo de "cuenta principal" y la lista de fuentes.

    - Si el PATCH trae la lista de fuentes, esa manda y `Client` deriva el
      espejo (la lista es canónica).
    - Si el PATCH solo trae el campo suelto, se está cambiando LA CUENTA
      PRINCIPAL: se reescribe la primera fuente y se conservan las demás, que
      son asignaciones independientes (campañas de otras cuentas).
    """
    for _platform, id_field, list_field, id_key in AD_SYNC:
        if list_field in patch:
            # La lista es canónica, incluso cuando viene vacía. Hay que
            # derivar (o limpiar) el espejo legacy ANTES de construir Client;
            # si se conserva el ID anterior, el validador backward-compatible
            # podría reconstruir justo la cuenta que el usuario desasignó.
            sources = list(patch[list_field] or [])
            primary = next(
                (
                    src for src in sources
                    if not (src.get("campaign_ids") or [])
                    and not (src.get("adset_selections") or [])
                ),
                None,
            )
            merged[id_field] = primary.get(id_key) if primary else None
            if id_key == "customer_id":
                merged["google_ads_login_customer_id"] = (
                    primary.get("login_customer_id") if primary else None
                )
            continue
        if id_field not in patch:
            continue
        new_id = patch[id_field]
        sources = list(merged.get(list_field) or [])
        # La cuenta principal es la primera fuente SIN campañas seleccionadas.
        # Las parciales son asignaciones sobre cuentas de otros y no se tocan.
        idx = next(
            (
                i for i, src in enumerate(sources)
                if not (src.get("campaign_ids") or [])
                and not (src.get("adset_selections") or [])
            ),
            None,
        )
        primary = sources[idx] if idx is not None else None
        rest = [src for i, src in enumerate(sources) if i != idx]
        if not new_id:
            # Se desasigna la cuenta principal; las parciales siguen.
            merged[list_field] = rest
            continue
        # Asignar la cuenta principal significa "toda esta cuenta es mía".
        same = primary is not None and primary.get(id_key) == new_id
        source = {
            id_key: new_id,
            "campaign_ids": [],
            "allocation": primary.get("allocation", "exclusive") if same else "exclusive",
        }
        if id_key == "customer_id":
            source["login_customer_id"] = patch.get(
                "google_ads_login_customer_id",
                primary.get("login_customer_id") if same else None,
            )
        merged[list_field] = [source, *rest]


def _sync_ga4_patch(patch: dict) -> None:
    """Deja coherentes `ga4_property_id` y `ga4_property_ids` en un PATCH.

    La UI nueva manda el array; un cliente antiguo de la API puede seguir
    mandando solo el campo suelto. Se acepta cualquiera de los dos y se
    completa el otro, para que ambos describan siempre la misma selección.
    """
    if "ga4_property_ids" in patch:
        ids = patch["ga4_property_ids"] or []
        patch["ga4_property_ids"] = ids
        patch["ga4_property_id"] = ids[0] if ids else None
    elif "ga4_property_id" in patch:
        pid = patch["ga4_property_id"]
        patch["ga4_property_ids"] = [pid] if pid else []


LEVEL_LABEL = {"account": "la cuenta completa", "campaign": "la campaña", "adset": "el conjunto de anuncios"}


def _ensure_no_exclusive_conflicts(clients: list[Client], client: Client) -> None:
    """Un mismo recurso no puede estar en exclusiva para dos clientes.

    El choque se comprueba al MISMO nivel (cuenta / campaña / ad set): una
    campaña y sus ad sets no chocan entre sí, se recortan jerárquicamente (el
    dueño de la campaña la ve menos los ad sets cedidos). "shared" nunca choca:
    es justo lo que significa.
    """
    others = [c for c in clients if c.id != client.id]
    owners = exclusive_claims(others)
    if not owners:
        return
    names = {c.id: c.name for c in others}
    for key, owner in exclusive_claims([client]).items():
        del owner  # solo interesa la clave: ya sabemos que es de `client`
        rival = owners.get(key)
        if rival is None:
            continue
        level = key_level(key)
        resource = key[3] or key[2] or key[1]
        raise ClientConflict(
            f"Conflicto de nivel {level}: {LEVEL_LABEL[level]} {resource} de la cuenta {key[0]} "
            f"{key[1]} ya está asignada en exclusiva a '{names.get(rival, rival)}'. "
            "Usa 'compartida' o quítasela primero."
        )


class ClientRepository(Protocol):
    def list(self) -> list[Client]: ...
    def get(self, client_id: str) -> Client: ...
    def create(self, data: ClientCreate) -> Client: ...
    def update(self, client_id: str, data: ClientUpdate) -> Client: ...


class ClientRegistry:
    """Registro de clientes sobre CUALQUIER store (archivo, Firestore, memoria).

    Toda la lógica —validación, migración, exclusiones, conflictos— vive aquí;
    el store solo carga y guarda. Así cambiar de persistencia no cambia reglas.
    """

    def __init__(self, store: ClientStore) -> None:
        self.store = store
        self._lock = threading.RLock()

    # ---- lectura ----
    def _load(self) -> list[Client]:
        """Clientes del store, migrados y ya sin los excluidos (ver exclusions.py).

        El filtro se aplica al leer: aunque un registro excluido llegue al
        almacenamiento (seed antiguo, edición manual), la aplicación no lo muestra.
        """
        payload, _origin, _changed = migrate(self.store.load())
        clients = [Client(**c) for c in payload.get("clients", [])]
        return [c for c in clients if not is_excluded(c.name, c.slug, c.id)]

    def _save(self, clients: list[Client]) -> None:
        self.store.save({
            "schema_version": SCHEMA_VERSION,
            "clients": [c.model_dump() for c in clients],
        })

    def list(self) -> list[Client]:
        with self._lock:
            return self._load()

    def get(self, client_id: str) -> Client:
        with self._lock:
            for c in self._load():
                if c.id == client_id:
                    return c
        raise ClientNotFound(f"Cliente no encontrado: {client_id}")

    # ---- escritura ----
    def create(self, data: ClientCreate) -> Client:
        with self._lock:
            _reject_excluded(data.name)
            clients = self._load()
            fields = data.model_dump(exclude_none=True)
            slug = fields.pop("slug", None) or slugify(data.name)
            self._ensure_unique_slug(clients, slug, None)
            client = Client(id=slug, slug=slug, **{k: v for k, v in fields.items() if k != "name"}, name=data.name.strip())
            self._ensure_unique_accounts(clients, client)
            _ensure_no_exclusive_conflicts(clients, client)
            clients.append(client)
            self._save(clients)
            # No devolver el objeto que se acaba de construir en memoria: se
            # relee del store para que un 201 solo pueda significar "esto es
            # lo que quedó realmente persistido", nunca "esto es lo que creí
            # haber guardado" (ver informe de persistencia).
            return self.get(client.id)

    def update(self, client_id: str, data: ClientUpdate) -> Client:
        with self._lock:
            clients = self._load()
            idx = next((i for i, c in enumerate(clients) if c.id == client_id), None)
            if idx is None:
                raise ClientNotFound(f"Cliente no encontrado: {client_id}")
            # exclude_unset distingue "campo ausente" (no tocar) de "null
            # explícito" (desasignar). Es la diferencia entre {} y {"x": null}.
            patch = data.model_dump(exclude_unset=True)
            for required in ("name", "slug", "active", "currency", "timezone"):
                if required in patch and patch[required] is None:
                    patch.pop(required)
            if patch.get("name"):
                _reject_excluded(patch["name"])
            _sync_ga4_patch(patch)
            merged_data = clients[idx].model_dump() | patch
            _sync_ads_patch(merged_data, patch)
            # Client(...) revalida (model_copy no lo haría) y aplica las
            # migraciones campo suelto ↔ lista canónica.
            merged = Client(**merged_data)
            if "slug" in patch:
                self._ensure_unique_slug(clients, merged.slug, client_id)
            if not merged.ga4_property_ids:
                merged.ga4_account_id = None
            self._ensure_unique_accounts(clients, merged)
            _ensure_no_exclusive_conflicts(clients, merged)
            clients[idx] = merged
            self._save(clients)
            # Idem: se relee del store en vez de devolver `merged` directo.
            # El frontend se hidrata con lo que el repository dice que hay,
            # no con lo que el proceso acaba de calcular en memoria.
            return self.get(merged.id)

    @staticmethod
    def _ensure_unique_slug(clients: list[Client], slug: str, own_id: str | None) -> None:
        for c in clients:
            if c.id != own_id and (c.slug == slug or c.id == slug):
                raise ClientConflict(f"Ya existe un cliente con el slug '{slug}'.")

    @staticmethod
    def _whole_account_ids(client: Client, platform: str) -> set[str]:
        """Cuentas que el cliente consume ENTERAS.

        Entera = sin campañas NI conjuntos de anuncios seleccionados. Una fuente
        que solo toma ad sets es una asignación parcial, no la cuenta.
        """
        return {
            s.account_id for s in ad_sources(client, platform)
            if getattr(s, "is_whole_account", not s.campaign_ids)
        }

    @classmethod
    def _ensure_unique_accounts(cls, clients: list[Client], client: Client) -> None:
        """Dos clientes no pueden consumir la MISMA cuenta entera.

        "Entera" = una fuente sin campañas seleccionadas, que significa "todas
        las campañas elegibles". Eso sí es excluyente: sería el mismo universo
        de datos en dos clientes.

        En cambio, tomar CAMPAÑAS CONCRETAS de la cuenta de otro cliente está
        permitido — es justo el caso de uso: un cliente sin cuenta propia cuyas
        campañas viven dentro de la cuenta de otro. El reparto lo gobierna
        `allocation` y el choque de exclusivas lo valida
        `_ensure_no_exclusive_conflicts`.

        En GA4 la exclusividad sigue siendo total: una propiedad es de un cliente.
        """
        for other in clients:
            if other.id == client.id:
                continue
            for platform in ACCOUNT_FIELDS:
                if platform in AD_PLATFORMS:
                    mine = cls._whole_account_ids(client, platform)
                    theirs = cls._whole_account_ids(other, platform)
                    noun = "La cuenta completa"
                else:
                    mine, theirs = set(account_ids(client, platform)), set(account_ids(other, platform))
                    noun = "La cuenta"
                shared = mine & theirs
                if shared:
                    ids = ", ".join(sorted(shared))
                    raise ClientConflict(
                        f"{noun} {platform} {ids} ya está asignada a '{other.name}'."
                        + (" Selecciona campañas concretas para compartirla." if platform in AD_PLATFORMS else "")
                    )


class JsonClientRepository(ClientRegistry):
    """Registro sobre un archivo JSON. Nombre histórico, conservado para no
    romper llamadas existentes; internamente es un `ClientRegistry` con un
    `FileClientStore`."""

    def __init__(self, path: Path) -> None:
        super().__init__(FileClientStore(Path(path)))

    @property
    def path(self) -> Path:
        return self.store.path

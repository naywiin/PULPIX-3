"""Persistencia y validación de grupos de reporting por campaña.

Los grupos son una capa de presentación sobre campañas que ya están asignadas
a un cliente. No reemplazan las asignaciones ni consultan proveedores.
"""
from __future__ import annotations

import re
import threading
from datetime import datetime, timezone
from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .models import Client, ad_sources

ReportingPlatform = Literal["google_ads", "meta_ads", "spotify_ads", "pinterest_ads"]
REPORTING_PLATFORMS: tuple[ReportingPlatform, ...] = ("google_ads", "meta_ads", "spotify_ads", "pinterest_ads")
_SLUG = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")


def enabled_platforms_for_group(group: Any) -> dict[str, bool]:
    """Resolve explicit switches or infer a legacy group's state from assignments."""
    configured = getattr(group, "enabled_platforms", None)
    assignments = getattr(group, "assignments", []) or []
    if configured is None:
        assigned = {item.platform for item in assignments}
        return {platform: platform in assigned for platform in REPORTING_PLATFORMS}
    return {platform: bool(configured.get(platform, False)) for platform in REPORTING_PLATFORMS}


class CampaignGroupAssignment(BaseModel):
    """Referencia inequívoca a una campaña ya asignada al cliente."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    platform: ReportingPlatform
    account_id: str = Field(min_length=1, max_length=120)
    campaign_id: str = Field(min_length=1, max_length=160)

    @field_validator("account_id", "campaign_id")
    @classmethod
    def normalize_id(cls, value: str) -> str:
        value = str(value).strip()
        if not value or "/" in value:
            raise ValueError("La referencia de campaña no es válida.")
        return value

    @property
    def key(self) -> tuple[str, str, str]:
        return (self.platform, self.account_id, self.campaign_id)


class ReportingGroupDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=100)
    type: str = Field(min_length=1, max_length=64)
    active: bool = True
    enabled_platforms: dict[ReportingPlatform, bool] | None = None
    assignments: list[CampaignGroupAssignment] = Field(default_factory=list)

    @field_validator("id", "type")
    @classmethod
    def canonical_slug(cls, value: str) -> str:
        value = value.strip().lower()
        if not _SLUG.fullmatch(value):
            raise ValueError("id y type deben ser identificadores cortos en minúsculas.")
        if value == "general":
            raise ValueError("'general' está reservado para campañas sin grupo.")
        return value

    @field_validator("name")
    @classmethod
    def clean_name(cls, value: str) -> str:
        return " ".join(value.split())


class ReportingGroup(ReportingGroupDraft):
    client_id: str = Field(min_length=1, max_length=120)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class ReportingGroupsPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    groups: list[ReportingGroupDraft] = Field(default_factory=list)


class ReportingGroupSnapshotGroup(BaseModel):
    """Metadata de grupo congelada para un solo mes."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    id: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=100)
    type: str = Field(min_length=1, max_length=64)
    active: bool = True
    enabled_platforms: dict[ReportingPlatform, bool] | None = None

    @field_validator("id", "type")
    @classmethod
    def canonical_slug(cls, value: str) -> str:
        value = value.strip().lower()
        if not _SLUG.fullmatch(value) or value == "general":
            raise ValueError("id y type deben ser identificadores válidos; general está reservado.")
        return value

    @field_validator("name")
    @classmethod
    def clean_name(cls, value: str) -> str:
        return " ".join(value.split())


class MonthlyCampaignAssignment(BaseModel):
    """Asignación efectiva solo para el año/mes del snapshot."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    platform: ReportingPlatform
    account_id: str = Field(min_length=1, max_length=120)
    campaign_id: str = Field(min_length=1, max_length=160)
    reporting_group_id: str | None = None

    @field_validator("account_id", "campaign_id")
    @classmethod
    def normalize_id(cls, value: str) -> str:
        value = str(value).strip()
        if not value or "/" in value:
            raise ValueError("La referencia de campaña no es válida.")
        return value


class ReportingGroupSnapshotDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")
    groups: list[ReportingGroupSnapshotGroup] = Field(default_factory=list)
    assignments: list[MonthlyCampaignAssignment] = Field(default_factory=list)


class ReportingGroupSnapshot(ReportingGroupSnapshotDraft):
    client_id: str = Field(min_length=1, max_length=120)
    year: int = Field(ge=2000, le=2100)
    month: int = Field(ge=1, le=12)
    schema_version: int = 2
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def key(self) -> tuple[str, int, int]:
        return self.client_id, self.year, self.month

    @property
    def firestore_id(self) -> str:
        return f"{self.client_id}__{self.year}__{self.month:02d}"

    def as_groups(self) -> list[ReportingGroup]:
        group_assignments: dict[str, list[CampaignGroupAssignment]] = {group.id: [] for group in self.groups}
        for assignment in self.assignments:
            if assignment.reporting_group_id:
                group_assignments[assignment.reporting_group_id].append(CampaignGroupAssignment(
                    platform=assignment.platform,
                    account_id=assignment.account_id,
                    campaign_id=assignment.campaign_id,
                ))
        return [ReportingGroup(
            client_id=self.client_id,
            id=group.id,
            name=group.name,
            type=group.type,
            active=group.active,
            enabled_platforms=group.enabled_platforms,
            assignments=group_assignments[group.id],
            created_at=self.created_at,
            updated_at=self.updated_at,
        ) for group in self.groups]


class ReportingGroupSnapshotStoreError(Exception):
    """Fallo de persistencia para snapshots mensuales."""


class ReportingGroupSnapshotConflict(ReportingGroupSnapshotStoreError):
    """Un snapshot existente no puede ser reemplazado por una copia."""


class ReportingGroupSnapshotRepository(Protocol):
    name: str

    def get(self, client_id: str, year: int, month: int) -> ReportingGroupSnapshot | None: ...
    def list(self, client_id: str) -> list[ReportingGroupSnapshot]: ...
    def save(self, snapshot: ReportingGroupSnapshot) -> ReportingGroupSnapshot: ...
    def create_if_absent(self, snapshot: ReportingGroupSnapshot) -> ReportingGroupSnapshot: ...


class MemoryReportingGroupSnapshotRepository:
    name = "memory"

    def __init__(self, initial: list[ReportingGroupSnapshot] | None = None) -> None:
        self._items = {item.key: item for item in initial or []}
        self._lock = threading.RLock()

    def get(self, client_id: str, year: int, month: int) -> ReportingGroupSnapshot | None:
        with self._lock:
            return self._items.get((client_id, year, month))

    def list(self, client_id: str) -> list[ReportingGroupSnapshot]:
        with self._lock:
            return sorted((item for item in self._items.values() if item.client_id == client_id), key=lambda item: (item.year, item.month))

    def save(self, snapshot: ReportingGroupSnapshot) -> ReportingGroupSnapshot:
        now = datetime.now(timezone.utc)
        with self._lock:
            previous = self._items.get(snapshot.key)
            saved = snapshot.model_copy(update={"created_at": previous.created_at if previous else now, "updated_at": now})
            self._items[snapshot.key] = saved
            return saved

    def create_if_absent(self, snapshot: ReportingGroupSnapshot) -> ReportingGroupSnapshot:
        with self._lock:
            if snapshot.key in self._items:
                raise ReportingGroupSnapshotConflict("Ya existe una configuración para ese mes.")
            return self.save(snapshot)


class FirestoreReportingGroupSnapshotRepository:
    """Un documento determinista e independiente por cliente/año/mes."""

    name = "firestore"

    def __init__(self, collection: str, project: str | None = None, database: str | None = None) -> None:
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
                except ImportError as exc:  # pragma: no cover
                    raise ReportingGroupSnapshotStoreError("Falta google-cloud-firestore para persistir snapshots.") from exc
                kwargs: dict[str, Any] = {}
                if self.project:
                    kwargs["project"] = self.project
                if self.database:
                    kwargs["database"] = self.database
                try:
                    self._client = firestore.Client(**kwargs)
                except Exception as exc:  # pragma: no cover
                    raise ReportingGroupSnapshotStoreError(f"No se pudo conectar con Firestore: {type(exc).__name__}") from exc
            return self._client

    def _reference(self, client_id: str, year: int, month: int):
        snapshot_id = ReportingGroupSnapshot(
            client_id=client_id, year=year, month=month,
        ).firestore_id
        return self._db().collection(self.collection).document(snapshot_id)

    def get(self, client_id: str, year: int, month: int) -> ReportingGroupSnapshot | None:
        try:
            doc = self._reference(client_id, year, month).get()
            return ReportingGroupSnapshot(**doc.to_dict()) if doc.exists else None
        except ReportingGroupSnapshotStoreError:
            raise
        except Exception as exc:  # pragma: no cover
            raise ReportingGroupSnapshotStoreError(f"No se pudo leer snapshot: {type(exc).__name__}") from exc

    def list(self, client_id: str) -> list[ReportingGroupSnapshot]:
        try:
            docs = self._db().collection(self.collection).where("client_id", "==", client_id).stream()
            result = [ReportingGroupSnapshot(**(doc.to_dict() or {})) for doc in docs]
        except ReportingGroupSnapshotStoreError:
            raise
        except Exception as exc:  # pragma: no cover
            raise ReportingGroupSnapshotStoreError(f"No se pudieron listar snapshots: {type(exc).__name__}") from exc
        return sorted(result, key=lambda item: (item.year, item.month))

    def save(self, snapshot: ReportingGroupSnapshot) -> ReportingGroupSnapshot:
        now = datetime.now(timezone.utc)
        try:
            reference = self._reference(snapshot.client_id, snapshot.year, snapshot.month)
            previous = reference.get()
            value = snapshot.model_copy(update={
                "created_at": ReportingGroupSnapshot(**previous.to_dict()).created_at if previous.exists else now,
                "updated_at": now,
            })
            reference.set(value.model_dump(mode="python"))
            return value
        except ReportingGroupSnapshotStoreError:
            raise
        except Exception as exc:  # pragma: no cover
            raise ReportingGroupSnapshotStoreError(f"No se pudo guardar snapshot: {type(exc).__name__}") from exc

    def create_if_absent(self, snapshot: ReportingGroupSnapshot) -> ReportingGroupSnapshot:
        try:
            reference = self._reference(snapshot.client_id, snapshot.year, snapshot.month)
            value = snapshot.model_copy(update={"created_at": datetime.now(timezone.utc), "updated_at": datetime.now(timezone.utc)})
            reference.create(value.model_dump(mode="python"))
            return value
        except ReportingGroupSnapshotConflict:
            raise
        except Exception as exc:  # pragma: no cover
            # Firestore's create precondition makes copies race-safe. Avoid returning raw provider details.
            if "already exists" in str(exc).lower() or "exists" in type(exc).__name__.lower():
                raise ReportingGroupSnapshotConflict("Ya existe una configuración para ese mes.") from None
            raise ReportingGroupSnapshotStoreError(f"No se pudo crear snapshot: {type(exc).__name__}") from exc


class ReportingGroupStoreError(Exception):
    """Fallo seguro de lectura/escritura de grupos de reporting."""


class ReportingGroupRepository(Protocol):
    name: str

    def list(self, client_id: str) -> list[ReportingGroup]: ...
    def save_client_groups(self, client_id: str, groups: list[ReportingGroupDraft]) -> list[ReportingGroup]: ...


def validate_reporting_groups(client: Client, groups: list[ReportingGroupDraft]) -> None:
    """Rechaza fuentes ajenas y campañas duplicadas entre grupos activos."""
    ids: set[str] = set()
    assigned: dict[tuple[str, str, str], str] = {}
    for group in groups:
        if group.id in ids:
            raise ValueError(f"El grupo '{group.id}' aparece más de una vez.")
        ids.add(group.id)
        if not group.active:
            continue
        local: set[tuple[str, str, str]] = set()
        for assignment in group.assignments:
            key = assignment.key
            if key in local:
                raise ValueError(f"La campaña {assignment.campaign_id} está repetida en {group.name}.")
            local.add(key)
            owner = assigned.get(key)
            if owner:
                raise ValueError(
                    f"La campaña {assignment.campaign_id} ya pertenece al grupo '{owner}'."
                )
            sources = ad_sources(client, assignment.platform)
            source = next((item for item in sources if item.account_id == assignment.account_id), None)
            if source is None:
                raise ValueError("No se puede agrupar una campaña de una cuenta no asignada al cliente.")
            allowed_campaigns = set(source.campaign_ids)
            allowed_campaigns.update(
                selection.campaign_id for selection in (getattr(source, "adset_selections", []) or [])
            )
            if allowed_campaigns and assignment.campaign_id not in allowed_campaigns:
                raise ValueError("La campaña no está incluida en la asignación vigente del cliente.")
            assigned[key] = group.name


def validate_monthly_snapshot(client: Client, snapshot: ReportingGroupSnapshotDraft) -> None:
    """Valida también campañas sin grupo; cada campaña aparece como máximo una vez."""
    group_ids = [group.id for group in snapshot.groups]
    if len(group_ids) != len(set(group_ids)):
        raise ValueError("Los IDs de grupo deben ser únicos dentro del mes.")
    known_groups = set(group_ids)
    seen: set[tuple[str, str, str]] = set()
    grouped: dict[str, list[CampaignGroupAssignment]] = {group_id: [] for group_id in group_ids}
    for assignment in snapshot.assignments:
        key = (assignment.platform, assignment.account_id, assignment.campaign_id)
        if key in seen:
            raise ValueError(f"La campaña {assignment.campaign_id} aparece más de una vez en el snapshot mensual.")
        seen.add(key)
        if assignment.reporting_group_id and assignment.reporting_group_id not in known_groups:
            raise ValueError(f"El grupo {assignment.reporting_group_id} no existe en el snapshot del mes.")
        sources = ad_sources(client, assignment.platform)
        source = next((item for item in sources if item.account_id == assignment.account_id), None)
        if source is None:
            raise ValueError("No se puede asignar una campaña de una cuenta no asignada al cliente.")
        allowed_campaigns = set(source.campaign_ids)
        allowed_campaigns.update(
            selection.campaign_id for selection in (getattr(source, "adset_selections", []) or [])
        )
        if allowed_campaigns and assignment.campaign_id not in allowed_campaigns:
            raise ValueError("La campaña no está incluida en la asignación vigente del cliente.")
        if assignment.reporting_group_id:
            grouped[assignment.reporting_group_id].append(CampaignGroupAssignment(
                platform=assignment.platform,
                account_id=assignment.account_id,
                campaign_id=assignment.campaign_id,
            ))
    # Reutiliza las validaciones legacy para mantener los límites de fuente y cuenta.
    validate_reporting_groups(client, [ReportingGroupDraft(
        id=group.id, name=group.name, type=group.type, active=group.active,
        enabled_platforms=group.enabled_platforms,
        assignments=grouped[group.id],
    ) for group in snapshot.groups])


class MemoryReportingGroupRepository:
    name = "memory"

    def __init__(self, initial: list[ReportingGroup] | None = None) -> None:
        self._items = {(item.client_id, item.id): item for item in initial or []}
        self._lock = threading.RLock()

    def list(self, client_id: str) -> list[ReportingGroup]:
        with self._lock:
            items = [item for (cid, _), item in self._items.items() if cid == client_id]
        return sorted(items, key=lambda item: (item.name.casefold(), item.id))

    def save_client_groups(self, client_id: str, groups: list[ReportingGroupDraft]) -> list[ReportingGroup]:
        now = datetime.now(timezone.utc)
        with self._lock:
            previous = {gid: item for (cid, gid), item in self._items.items() if cid == client_id}
            saved: dict[str, ReportingGroup] = {}
            for draft in groups:
                old = previous.get(draft.id)
                saved[draft.id] = ReportingGroup(
                    **draft.model_dump(), client_id=client_id,
                    created_at=old.created_at if old else now, updated_at=now,
                )
            for gid, old in previous.items():
                if gid not in saved:
                    saved[gid] = old.model_copy(update={"active": False, "assignments": [], "updated_at": now})
            self._items = {
                key: item for key, item in self._items.items() if key[0] != client_id
            } | {(client_id, gid): item for gid, item in saved.items()}
        return self.list(client_id)


class FirestoreReportingGroupRepository:
    """Un documento determinista por cliente/grupo; los retiros son inactivaciones."""

    name = "firestore"

    def __init__(self, collection: str, project: str | None = None, database: str | None = None) -> None:
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
                except ImportError as exc:  # pragma: no cover - entorno productivo
                    raise ReportingGroupStoreError("Falta google-cloud-firestore para persistir grupos.") from exc
                kwargs: dict[str, Any] = {}
                if self.project:
                    kwargs["project"] = self.project
                if self.database:
                    kwargs["database"] = self.database
                try:
                    self._client = firestore.Client(**kwargs)
                except Exception as exc:
                    raise ReportingGroupStoreError(f"No se pudo conectar con Firestore: {type(exc).__name__}") from exc
            return self._client

    @staticmethod
    def _document_id(client_id: str, group_id: str) -> str:
        return f"{client_id}__{group_id}"

    def list(self, client_id: str) -> list[ReportingGroup]:
        try:
            docs = self._db().collection(self.collection).where("client_id", "==", client_id).stream()
            groups = [ReportingGroup(**(doc.to_dict() or {})) for doc in docs]
        except ReportingGroupStoreError:
            raise
        except Exception as exc:
            raise ReportingGroupStoreError(f"No se pudieron leer grupos: {type(exc).__name__}") from exc
        return sorted(groups, key=lambda item: (item.name.casefold(), item.id))

    def save_client_groups(self, client_id: str, groups: list[ReportingGroupDraft]) -> list[ReportingGroup]:
        db = self._db()
        collection = db.collection(self.collection)
        now = datetime.now(timezone.utc)
        try:
            existing = {item.id: item for item in self.list(client_id)}
            desired: dict[str, ReportingGroup] = {}
            for draft in groups:
                old = existing.get(draft.id)
                desired[draft.id] = ReportingGroup(
                    **draft.model_dump(), client_id=client_id,
                    created_at=old.created_at if old else now, updated_at=now,
                )
            for group_id, old in existing.items():
                if group_id not in desired:
                    desired[group_id] = old.model_copy(update={"active": False, "assignments": [], "updated_at": now})
            batch = db.batch()
            for group_id, group in desired.items():
                doc_id = self._document_id(client_id, group_id)
                batch.set(collection.document(doc_id), group.model_dump(mode="python"))
            batch.commit()
        except ReportingGroupStoreError:
            raise
        except Exception as exc:
            raise ReportingGroupStoreError(f"No se pudieron guardar grupos: {type(exc).__name__}") from exc
        return self.list(client_id)


class UnavailableReportingGroupRepository:
    name = "unavailable"
    message = "Los grupos de reporting requieren MP_STORE=firestore; no se guardan en almacenamiento efímero."

    def list(self, client_id: str) -> list[ReportingGroup]:
        del client_id
        raise ReportingGroupStoreError(self.message)

    def save_client_groups(self, client_id: str, groups: list[ReportingGroupDraft]) -> list[ReportingGroup]:
        del client_id, groups
        raise ReportingGroupStoreError(self.message)


class UnavailableReportingGroupSnapshotRepository:
    name = "unavailable"
    message = "Los snapshots mensuales requieren MP_STORE=firestore; no se guardan en almacenamiento efímero."

    def get(self, client_id: str, year: int, month: int) -> ReportingGroupSnapshot | None:
        del client_id, year, month
        raise ReportingGroupSnapshotStoreError(self.message)

    def list(self, client_id: str) -> list[ReportingGroupSnapshot]:
        del client_id
        raise ReportingGroupSnapshotStoreError(self.message)

    def save(self, snapshot: ReportingGroupSnapshot) -> ReportingGroupSnapshot:
        del snapshot
        raise ReportingGroupSnapshotStoreError(self.message)

    def create_if_absent(self, snapshot: ReportingGroupSnapshot) -> ReportingGroupSnapshot:
        del snapshot
        raise ReportingGroupSnapshotStoreError(self.message)


def create_reporting_group_repository(settings) -> ReportingGroupRepository:
    backend = (settings.store_backend or "file").lower()
    if backend == "memory":
        return MemoryReportingGroupRepository()
    if backend == "firestore":
        return FirestoreReportingGroupRepository(
            settings.reporting_groups_firestore_collection,
            project=settings.firestore_project,
            database=settings.firestore_database,
        )
    if backend == "file":
        return UnavailableReportingGroupRepository()
    raise ReportingGroupStoreError(f"MP_STORE desconocido: {backend!r}.")


def create_reporting_group_snapshot_repository(settings) -> ReportingGroupSnapshotRepository:
    backend = (settings.store_backend or "file").lower()
    if backend == "memory":
        return MemoryReportingGroupSnapshotRepository()
    if backend == "firestore":
        return FirestoreReportingGroupSnapshotRepository(
            settings.reporting_group_snapshots_firestore_collection,
            project=settings.firestore_project,
            database=settings.firestore_database,
        )
    if backend == "file":
        return UnavailableReportingGroupSnapshotRepository()
    raise ReportingGroupSnapshotStoreError(f"MP_STORE desconocido: {backend!r}.")

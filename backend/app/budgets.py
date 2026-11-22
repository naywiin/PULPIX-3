"""Presupuestos mensuales manuales, persistidos sin mezclar datos de performance."""
from __future__ import annotations

import threading
from hashlib import sha256
from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

BudgetPlatform = Literal["google_ads", "meta_ads", "spotify_ads", "pinterest_ads"]
BUDGET_PLATFORMS: tuple[BudgetPlatform, ...] = ("google_ads", "meta_ads", "spotify_ads", "pinterest_ads")
# La hoja histórica conserva sus bloques actuales; Pinterest se puede presupuestar
# y administrar, pero no se inyecta aún en los bloques mensuales existentes.
REPORT_PLATFORMS: tuple[BudgetPlatform, ...] = ("google_ads", "meta_ads", "spotify_ads")


class BudgetValue(BaseModel):
    """El único valor editable por el usuario."""

    model_config = ConfigDict(extra="forbid")
    budget: float = Field(ge=0, allow_inf_nan=False)


class MonthlyBudget(BaseModel):
    client_id: str = Field(min_length=1, max_length=120)
    platform: BudgetPlatform
    year: int = Field(ge=2000, le=2100)
    month: int = Field(ge=1, le=12)
    # None es la clave canónica para "general / sin grupo" y conserva la
    # compatibilidad con documentos legacy client+platform+año+mes.
    reporting_group_id: str | None = Field(default=None, min_length=1, max_length=64)
    account_id: str | None = Field(default=None, min_length=1, max_length=160)
    campaign_id: str | None = Field(default=None, min_length=1, max_length=160)
    budget: float = Field(ge=0, allow_inf_nan=False)
    currency: str = Field(min_length=3, max_length=3)

    @field_validator("currency")
    @classmethod
    def normalize_currency(cls, value: str) -> str:
        return value.upper()

    @field_validator("account_id", "campaign_id")
    @classmethod
    def normalize_identity(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            raise ValueError("La cuenta y la campaña deben tener identificador.")
        return normalized

    @model_validator(mode="after")
    def validate_scope(self):
        if self.reporting_group_id is not None and (self.account_id is not None or self.campaign_id is not None):
            raise ValueError("Un presupuesto debe corresponder a un grupo o a una campaña.")
        if (self.campaign_id is None) != (self.account_id is None):
            raise ValueError("account_id y campaign_id deben enviarse juntos.")
        return self

    @property
    def key(self) -> tuple[str, str, int, int, str | None, str | None, str | None]:
        return (self.client_id, self.platform, self.year, self.month, self.reporting_group_id, self.account_id, self.campaign_id)


class BudgetStoreError(Exception):
    """Error seguro de persistencia de presupuestos."""


class BudgetRepository(Protocol):
    name: str

    def list(self, client_id: str | None = None) -> list[MonthlyBudget]: ...
    def upsert(self, budget: MonthlyBudget) -> MonthlyBudget: ...
    def delete(self, budget: MonthlyBudget) -> None: ...


class MemoryBudgetRepository:
    name = "memory"

    def __init__(self, initial: list[MonthlyBudget] | None = None) -> None:
        self._items = {item.key: item for item in initial or []}
        self._lock = threading.RLock()

    def list(self, client_id: str | None = None) -> list[MonthlyBudget]:
        with self._lock:
            items = list(self._items.values())
        if client_id:
            items = [item for item in items if item.client_id == client_id]
        return sorted(items, key=lambda item: (*item.key[:4], item.reporting_group_id or "", item.account_id or "", item.campaign_id or ""))

    def upsert(self, budget: MonthlyBudget) -> MonthlyBudget:
        with self._lock:
            self._items[budget.key] = budget
        return budget

    def delete(self, budget: MonthlyBudget) -> None:
        with self._lock:
            self._items.pop(budget.key, None)


class FirestoreBudgetRepository:
    """Un documento por clave lógica; `set` hace upsert sin duplicados."""

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
                except ImportError as exc:  # pragma: no cover - depende del entorno
                    raise BudgetStoreError("Falta google-cloud-firestore para persistir presupuestos.") from exc
                kwargs: dict[str, Any] = {}
                if self.project:
                    kwargs["project"] = self.project
                if self.database:
                    kwargs["database"] = self.database
                try:
                    self._client = firestore.Client(**kwargs)
                except Exception as exc:
                    raise BudgetStoreError(f"No se pudo conectar con Firestore: {type(exc).__name__}") from exc
            return self._client

    @staticmethod
    def _document_id(budget: MonthlyBudget) -> str:
        if budget.campaign_id is not None:
            identity = f"{budget.account_id}:{budget.campaign_id}".encode()
            digest = sha256(identity).hexdigest()[:24]
            return f"{budget.client_id}__{budget.platform}__{budget.year:04d}-{budget.month:02d}__campaign__{digest}"
        if budget.reporting_group_id is None:
            # No cambiar IDs existentes: Firestore leerá los documentos legacy.
            return f"{budget.client_id}__{budget.platform}__{budget.year:04d}-{budget.month:02d}"
        return f"{budget.client_id}__{budget.reporting_group_id}__{budget.platform}__{budget.year:04d}-{budget.month:02d}"

    def list(self, client_id: str | None = None) -> list[MonthlyBudget]:
        try:
            query = self._db().collection(self.collection)
            docs = query.where("client_id", "==", client_id).stream() if client_id else query.stream()
            items = [MonthlyBudget(**(doc.to_dict() or {})) for doc in docs]
        except BudgetStoreError:
            raise
        except Exception as exc:
            raise BudgetStoreError(f"No se pudieron leer los presupuestos: {type(exc).__name__}") from exc
        return sorted(items, key=lambda item: (*item.key[:4], item.reporting_group_id or "", item.account_id or "", item.campaign_id or ""))

    def upsert(self, budget: MonthlyBudget) -> MonthlyBudget:
        try:
            self._db().collection(self.collection).document(self._document_id(budget)).set(budget.model_dump())
        except BudgetStoreError:
            raise
        except Exception as exc:
            raise BudgetStoreError(f"No se pudo guardar el presupuesto: {type(exc).__name__}") from exc
        return budget

    def delete(self, budget: MonthlyBudget) -> None:
        try:
            self._db().collection(self.collection).document(self._document_id(budget)).delete()
        except BudgetStoreError:
            raise
        except Exception as exc:
            raise BudgetStoreError(f"No se pudo eliminar el presupuesto: {type(exc).__name__}") from exc


class UnavailableBudgetRepository:
    """Evita degradar silenciosamente a disco efímero o localStorage."""

    name = "unavailable"
    message = (
        "Los presupuestos requieren MP_STORE=firestore; no se guardan en el "
        "filesystem local del contenedor."
    )

    def list(self, client_id: str | None = None) -> list[MonthlyBudget]:
        del client_id
        raise BudgetStoreError(self.message)

    def upsert(self, budget: MonthlyBudget) -> MonthlyBudget:
        del budget
        raise BudgetStoreError(self.message)

    def delete(self, budget: MonthlyBudget) -> None:
        del budget
        raise BudgetStoreError(self.message)


def create_budget_repository(settings) -> BudgetRepository:
    backend = (settings.store_backend or "file").lower()
    if backend == "memory":
        return MemoryBudgetRepository()
    if backend == "firestore":
        return FirestoreBudgetRepository(
            collection=settings.budgets_firestore_collection,
            project=settings.firestore_project,
            database=settings.firestore_database,
        )
    if backend == "file":
        return UnavailableBudgetRepository()
    raise BudgetStoreError(f"MP_STORE desconocido: {backend!r}.")

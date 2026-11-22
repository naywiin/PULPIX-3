"""API del dashboard Marketing Performance.

Local:      cd backend && ../.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000
Cloud Run:  ver Dockerfile (escucha en 0.0.0.0:$PORT).
"""
from __future__ import annotations

import json
import logging
import os
from datetime import date, timedelta
from typing import Any, Literal

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response

from .budgets import (
    BUDGET_PLATFORMS,
    BudgetPlatform,
    BudgetRepository,
    BudgetStoreError,
    BudgetValue,
    MonthlyBudget,
    REPORT_PLATFORMS,
    create_budget_repository,
)
from .cache import TTLCache
from .config import Settings
from .dates import COMPARISONS, compare_range, parse_date
from .models import AD_PLATFORMS, Client, ClientCreate, ClientUpdate, account_ids, ad_sources, blocked_resources, exclusive_claims
from .migrations import SCHEMA_VERSION, backup_name, migrate
from .registry import ClientRegistry, ClientRepository, RegistryError
from .stores import StoreError, create_store
from .service import LABELS, DashboardService
from .spa import SPAStaticFiles
from .woosync import (
    DEFAULT_TOP_LIMIT,
    MAX_TOP_LIMIT,
    WOOSYNC_COMPARE_VALUES,
    WooSyncService,
    parse_branches,
    parse_date_filter,
)
from .providers.base import ProviderError, ProviderNotConfigured
from .reporting import ReportValidationError, build_performance_summary, generate_performance_report, month_slices
from .report_profiles import report_profile
from .spotify_security import SPOTIFY_SANTUARIO_AD_ACCOUNT_ID, SPOTIFY_SANTUARIO_CLIENT_SLUG
from .reporting_groups import (
    MonthlyCampaignAssignment,
    ReportingGroupsPayload,
    ReportingGroupRepository,
    ReportingGroupSnapshot,
    ReportingGroupSnapshotConflict,
    ReportingGroupSnapshotDraft,
    ReportingGroupSnapshotRepository,
    ReportingGroupSnapshotStoreError,
    ReportingGroupSnapshotGroup,
    ReportingGroupStoreError,
    create_reporting_group_snapshot_repository,
    create_reporting_group_repository,
    validate_monthly_snapshot,
    validate_reporting_groups,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("mp.api")

MAX_RANGE_DAYS = 800


def _default_providers(settings: Settings) -> dict[str, Any]:
    from .providers.ga4 import GA4Provider
    from .providers.google_ads import GoogleAdsProvider
    from .providers.meta import MetaProvider
    from .providers.pinterest import PinterestAdsProvider
    from .providers.spotify import SpotifyAdsProvider
    from .providers.woosync import WooSyncProvider

    return {
        "google_ads": GoogleAdsProvider(settings),
        "meta_ads": MetaProvider(settings),
        "pinterest_ads": PinterestAdsProvider(settings),
        "spotify_ads": SpotifyAdsProvider(settings),
        "ga4": GA4Provider(settings),
        "woosync": WooSyncProvider(settings),
    }


def _seed_once(store, settings: Settings) -> None:
    """Inicializa el almacenamiento SOLO si está vacío.

    Nunca sobrescribe datos existentes: un despliegue actualiza código, no
    resetea la configuración del usuario. Si el store ya tiene clientes, el
    seed de la imagen se ignora por completo.
    """
    try:
        if not store.is_empty():
            return
    except StoreError as e:
        log.error("No se pudo consultar el almacenamiento del registro: %s", e)
        return
    seed = settings.clients_seed_file
    if not seed.exists():
        log.info("Almacenamiento (%s) vacío y sin seed en %s: se arranca sin clientes.", store.name, seed)
        return
    try:
        payload = json.loads(seed.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        log.warning("No se pudo leer el seed %s: %s", seed, e)
        return
    payload, _origin, _changed = migrate(payload)
    try:
        store.save(payload)
    except StoreError as e:
        log.error("No se pudo inicializar el registro: %s", e)
        return
    log.info("Registro inicializado en %s desde el seed %s (%d clientes). "
             "Esto ocurre UNA sola vez: en adelante mandan los datos guardados.",
             store.name, seed, len(payload.get("clients") or []))


def _migrate_store(store, settings: Settings) -> None:
    """Deja el documento persistido en el esquema actual, con respaldo previo.

    Idempotente: si ya está al día no escribe nada. Antes de reescribir en otro
    formato guarda una copia lógica del documento anterior, para que ninguna
    migración pueda dejar al usuario sin su configuración.
    """
    try:
        raw = store.load()
    except StoreError as e:
        log.error("No se pudo leer el registro para migrarlo: %s", e)
        return
    payload, origin, changed = migrate(raw)
    if not changed:
        return
    if origin != SCHEMA_VERSION and raw.get("clients"):
        payload[backup_name(origin)] = raw.get("clients")
        log.info("Respaldo lógico del registro v%d guardado antes de migrar.", origin)
    try:
        store.save(payload)
    except StoreError as e:
        log.error("No se pudo guardar el registro migrado: %s", e)


def _ephemeral_storage_risk(store, settings: Settings) -> bool:
    """True si el registro escribe en un archivo que NO sobrevive a una
    revisión nueva de Cloud Run (mismo path que el seed horneado en la
    imagen: cada revisión arranca de una imagen nueva, así que ese archivo
    vuelve al contenido del seed y pierde lo configurado desde Settings)."""
    return store.name == "file" and settings.clients_file.resolve() == settings.clients_seed_file.resolve()


def _build_repo(settings: Settings) -> ClientRepository:
    """Registro sobre el almacenamiento configurado, ya sembrado y migrado."""
    store = create_store(settings)
    if _ephemeral_storage_risk(store, settings):
        # El registro apunta al mismo archivo que el seed: en Cloud Run eso es
        # el archivo de la imagen, que NO sobrevive a una revisión nueva, a un
        # reciclado de instancia ni a scale-to-zero.
        in_cloud_run = bool(os.environ.get("K_SERVICE"))
        message = (
            "MP_STORE=file y MP_CLIENTS_FILE apunta al seed de la imagen (%s). "
            "Los cambios guardados desde Settings se perderán al desplegar, al reciclarse la "
            "instancia o al escalar a cero. En producción usa MP_STORE=firestore (ver README) "
            "o monta un volumen y apunta MP_CLIENTS_FILE ahí."
        )
        if in_cloud_run:
            # K_SERVICE solo existe dentro de un servicio de Cloud Run: si esto
            # se ve en los logs de producción, NO es una advertencia de
            # desarrollo, es la causa exacta de la pérdida de configuración.
            log.error("[Cloud Run, servicio %s] " + message, os.environ["K_SERVICE"], settings.clients_file)
        else:
            log.warning(message, settings.clients_file)
    elif settings.seed_clients:
        _seed_once(store, settings)
    else:
        log.info("Seed de clientes desactivado por MP_SEED_CLIENTS.")
    _migrate_store(store, settings)
    log.info("Registro de clientes sobre almacenamiento '%s' (esquema v%d)", store.name, SCHEMA_VERSION)
    return ClientRegistry(store)


def create_app(
    settings: Settings | None = None,
    repo: ClientRepository | None = None,
    providers: dict[str, Any] | None = None,
    budget_repository: BudgetRepository | None = None,
    reporting_group_repository: ReportingGroupRepository | None = None,
    reporting_group_snapshot_repository: ReportingGroupSnapshotRepository | None = None,
) -> FastAPI:
    settings = settings or Settings()
    if repo is None:
        repo = _build_repo(settings)
    configured_providers = providers or _default_providers(settings)
    service = DashboardService(
        configured_providers,
        TTLCache(settings.cache_ttl_seconds),
        TTLCache(settings.discovery_ttl_seconds),
    )
    woosync_service = WooSyncService(configured_providers.get("woosync"), TTLCache(settings.cache_ttl_seconds))
    budget_repo = budget_repository or create_budget_repository(settings)
    reporting_group_repo = reporting_group_repository or create_reporting_group_repository(settings)
    reporting_group_snapshot_repo = reporting_group_snapshot_repository or create_reporting_group_snapshot_repository(settings)

    app = FastAPI(title="Marketing Performance API", version="1.0.0")
    app.state.settings = settings
    app.state.repo = repo
    app.state.service = service
    app.state.budget_repository = budget_repo
    app.state.reporting_group_repository = reporting_group_repo
    app.state.reporting_group_snapshot_repository = reporting_group_snapshot_repo
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_methods=["GET", "POST", "PUT", "DELETE"],
        allow_headers=["Content-Type"],
        expose_headers=["Content-Disposition"],
    )

    @app.exception_handler(RegistryError)
    async def _registry_error(_: Request, exc: RegistryError):
        return JSONResponse(status_code=exc.status_code, content={"detail": str(exc)})

    # ------------------------------------------------------------ helpers
    def get_client(client_id: str) -> Client:
        return repo.get(client_id)

    def previous_month(year: int, month: int) -> tuple[int, int]:
        first = date(year, month, 1) - timedelta(days=1)
        return first.year, first.month

    def legacy_groups(client_id: str):
        try:
            return reporting_group_repo.list(client_id)
        except ReportingGroupStoreError:
            if getattr(reporting_group_repo, "name", "") == "unavailable":
                return []
            raise

    def client_snapshots(client_id: str):
        try:
            return reporting_group_snapshot_repo.list(client_id)
        except ReportingGroupSnapshotStoreError:
            if getattr(reporting_group_snapshot_repo, "name", "") == "unavailable":
                return []
            raise

    def snapshot_payload(client_id: str, year: int, month: int) -> dict[str, Any]:
        snapshots = client_snapshots(client_id)
        selected = next((item for item in snapshots if item.year == year and item.month == month), None)
        old_groups = legacy_groups(client_id)
        previous_year, previous_month_value = previous_month(year, month)
        has_previous = any(item.year == previous_year and item.month == previous_month_value for item in snapshots)
        current_period = (date.today().year, date.today().month)
        if selected:
            state = "configured"
            groups = selected.as_groups()
            assignments = selected.assignments
        elif snapshots or old_groups:
            state = "missing_group_configuration"
            groups = []
            assignments = []
        else:
            state = "not_configured"
            groups = []
            assignments = []
        return {
            "client_id": client_id,
            "year": year,
            "month": month,
            "schema_version": selected.schema_version if selected else 1,
            "state": state,
            "groups": [group.model_dump(mode="json") for group in groups],
            "assignments": [item.model_dump(mode="json") for item in assignments],
            "has_previous_snapshot": has_previous,
            "can_initialize_from_current": bool(old_groups) and (year, month) >= current_period,
        }

    def resolve_monthly_group_config(client_ids: list[str], start: date, end: date):
        month_periods = month_slices(start, end)
        resolved: dict[tuple[str, int, int], list] = {}
        missing: dict[str, list[str]] = {}
        for client_id in client_ids:
            snapshots = client_snapshots(client_id)
            old_groups = legacy_groups(client_id)
            snapshot_by_month = {(item.year, item.month): item for item in snapshots}
            has_group_history = bool(snapshots or old_groups)
            for period in month_periods:
                snapshot = snapshot_by_month.get((period.year, period.month))
                key = (client_id, period.year, period.month)
                if snapshot:
                    resolved[key] = snapshot.as_groups()
                elif has_group_history:
                    missing.setdefault(client_id, []).append(period.label)
                else:
                    # No snapshot is required for clients that have never enabled groups.
                    resolved[key] = []
        return resolved, missing

    def missing_group_configuration_response(missing: dict[str, list[str]]) -> dict[str, Any]:
        return {
            "code": "missing_group_configuration",
            "message": "Configuración de grupos no disponible para este periodo.",
            "months_by_client": missing,
        }

    def parse_ranges(start_date: str, end_date: str, comparison: str, compare_start_date: str | None, compare_end_date: str | None):
        try:
            start, end = parse_date(start_date), parse_date(end_date)
            if start > end:
                raise ValueError("start_date debe ser anterior o igual a end_date")
            if (end - start).days > MAX_RANGE_DAYS:
                raise ValueError(f"El rango máximo es de {MAX_RANGE_DAYS} días")
            if comparison not in COMPARISONS:
                raise ValueError(f"comparison debe ser uno de: {', '.join(COMPARISONS)}")
            cs = parse_date(compare_start_date) if compare_start_date else None
            ce = parse_date(compare_end_date) if compare_end_date else None
            cmp = compare_range(comparison, start, end, cs, ce)
        except ValueError as e:
            raise HTTPException(status_code=422, detail=str(e)) from None
        return (start, end), cmp

    RangeParams = dict(
        start_date=Query(..., description="YYYY-MM-DD"),
        end_date=Query(..., description="YYYY-MM-DD"),
    )

    # ------------------------------------------------------------- health
    @app.get("/health", include_in_schema=False)
    def liveness() -> dict[str, str]:
        # Chequeo ligero para Cloud Run (startup/liveness probe): no llama a APIs externas.
        return {"status": "ok"}

    @app.get("/api/health")
    def health() -> dict[str, Any]:
        # Solo indica SI hay credenciales, nunca su valor.
        # `persistence.durable=false` es la señal de que el registro NO
        # sobrevivirá a la próxima revisión/instancia (ver _ephemeral_storage_risk):
        # verificable sin desplegar nada nuevo, solo llamando este endpoint.
        store = getattr(repo, "store", None)
        at_risk = store is not None and _ephemeral_storage_risk(store, settings)
        return {
            "status": "ok",
            "today": date.today().isoformat(),
            "credentials": {
                "google_ads": "adc",
                "ga4": "adc",
                "meta_ads": "configured" if settings.meta_access_token else "missing",
                "spotify_ads": "configured" if all((
                    settings.spotify_client_id,
                    settings.spotify_client_secret,
                    settings.spotify_refresh_token,
                )) else "missing",
                "pinterest_ads": "configured" if settings.pinterest_access_token else "missing",
            },
            "cache_ttl_seconds": settings.cache_ttl_seconds,
            "persistence": {
                "backend": store.name if store is not None else "unknown",
                "budgets_backend": budget_repo.name,
                "durable": not at_risk,
                "warning": None if not at_risk else (
                    "MP_STORE=file apunta al seed de la imagen: la configuración de Settings "
                    "no sobrevive a una revisión nueva. Usa MP_STORE=firestore en producción."
                ),
            },
        }

    # ----------------------------------------------------------- budgets
    @app.get("/api/budgets")
    def list_budgets(client_id: str | None = None) -> dict[str, Any]:
        if client_id:
            get_client(client_id)
        try:
            items = budget_repo.list(client_id)
        except BudgetStoreError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from None
        return {"budgets": [item.model_dump() for item in items]}

    @app.put("/api/budgets/{client_id}/{platform}/{year}/{month}")
    def save_budget(
        client_id: str,
        platform: BudgetPlatform,
        year: int,
        month: int,
        value: BudgetValue,
        reporting_group_id: str | None = Query(None),
        account_id: str | None = Query(None),
        campaign_id: str | None = Query(None),
    ) -> dict[str, Any]:
        client = get_client(client_id)
        if platform not in BUDGET_PLATFORMS or not account_ids(client, platform):
            raise HTTPException(
                status_code=422,
                detail=f"{client.name} no tiene {LABELS.get(platform, platform)} asignado.",
            )
        try:
            if (account_id is None) != (campaign_id is None):
                raise ValueError("account_id y campaign_id deben enviarse juntos para presupuesto por campaña.")
            if campaign_id is not None:
                if reporting_group_id is not None:
                    raise ValueError("El presupuesto de campaña es independiente del presupuesto del grupo.")
                source = next((item for item in ad_sources(client, platform) if item.account_id == account_id), None)
                if source is None:
                    raise ValueError("La cuenta no pertenece a las fuentes asignadas a este cliente.")
                allowed = set(source.campaign_ids)
                allowed.update(selection.campaign_id for selection in (getattr(source, "adset_selections", []) or []))
                if allowed and campaign_id not in allowed:
                    raise ValueError("La campaña no pertenece al alcance asignado a este cliente.")
            if reporting_group_id is not None:
                snapshot = reporting_group_snapshot_repo.get(client.id, year, month)
                if snapshot is None:
                    raise HTTPException(status_code=409, detail="Configuración de grupos no disponible para este periodo.")
                if not any(group.id == reporting_group_id and group.active for group in snapshot.groups):
                    raise HTTPException(status_code=422, detail="El grupo no está activo en la configuración de ese mes.")
            item = MonthlyBudget(
                client_id=client.id,
                platform=platform,
                year=year,
                month=month,
                reporting_group_id=reporting_group_id,
                account_id=account_id,
                campaign_id=campaign_id,
                budget=value.budget,
                currency=client.currency,
            )
            saved = budget_repo.upsert(item).model_dump()
            if campaign_id is None:
                saved.pop("account_id", None)
                saved.pop("campaign_id", None)
            return saved
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from None
        except ReportingGroupStoreError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from None
        except ReportingGroupSnapshotStoreError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from None
        except BudgetStoreError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from None

    @app.delete("/api/budgets/{client_id}/{platform}/{year}/{month}")
    def delete_budget(
        client_id: str,
        platform: BudgetPlatform,
        year: int,
        month: int,
        reporting_group_id: str | None = Query(None),
        account_id: str | None = Query(None),
        campaign_id: str | None = Query(None),
    ) -> dict[str, bool]:
        client = get_client(client_id)
        if not account_ids(client, platform):
            raise HTTPException(status_code=422, detail=f"{client.name} no tiene {LABELS.get(platform, platform)} asignado.")
        try:
            if (account_id is None) != (campaign_id is None):
                raise ValueError("account_id y campaign_id deben enviarse juntos para presupuesto por campaña.")
            if campaign_id is not None:
                source = next((item for item in ad_sources(client, platform) if item.account_id == account_id), None)
                if source is None:
                    raise ValueError("La cuenta no pertenece a las fuentes asignadas a este cliente.")
                allowed = set(source.campaign_ids)
                allowed.update(selection.campaign_id for selection in (getattr(source, "adset_selections", []) or []))
                if allowed and campaign_id not in allowed:
                    raise ValueError("La campaña no pertenece al alcance asignado a este cliente.")
            item = MonthlyBudget(
                client_id=client.id, platform=platform, year=year, month=month,
                reporting_group_id=reporting_group_id, account_id=account_id, campaign_id=campaign_id,
                budget=0, currency=client.currency,
            )
            budget_repo.delete(item)
            return {"deleted": True}
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from None
        except BudgetStoreError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from None

    @app.get("/api/reporting-groups/{client_id}")
    def list_reporting_groups(client_id: str) -> dict[str, Any]:
        client = get_client(client_id)
        try:
            groups = reporting_group_repo.list(client.id)
        except ReportingGroupStoreError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from None
        return {"client_id": client.id, "groups": [group.model_dump(mode="json") for group in groups]}

    @app.put("/api/reporting-groups/{client_id}")
    def save_reporting_groups(client_id: str, payload: ReportingGroupsPayload) -> dict[str, Any]:
        client = get_client(client_id)
        try:
            validate_reporting_groups(client, payload.groups)
            owners = exclusive_claims(repo.list())
            for group in payload.groups:
                if not group.active:
                    continue
                for assignment in group.assignments:
                    blocked, _blocked_adsets = blocked_resources(client, assignment.platform, assignment.account_id, owners)
                    if assignment.campaign_id in blocked:
                        raise ValueError("La campaña está excluida por una asignación exclusiva de otro cliente.")
            groups = reporting_group_repo.save_client_groups(client.id, payload.groups)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from None
        except ReportingGroupStoreError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from None
        return {"client_id": client.id, "groups": [group.model_dump(mode="json") for group in groups]}

    @app.get("/api/reporting-group-snapshots/{client_id}/{year}/{month}")
    def get_reporting_group_snapshot(client_id: str, year: int, month: int) -> dict[str, Any]:
        client = get_client(client_id)
        if not 2000 <= year <= 2100 or not 1 <= month <= 12:
            raise HTTPException(status_code=422, detail="Año o mes inválido.")
        try:
            return snapshot_payload(client.id, year, month)
        except (ReportingGroupSnapshotStoreError, ReportingGroupStoreError) as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from None

    @app.put("/api/reporting-group-snapshots/{client_id}/{year}/{month}")
    def save_reporting_group_snapshot(
        client_id: str, year: int, month: int, payload: ReportingGroupSnapshotDraft,
    ) -> dict[str, Any]:
        client = get_client(client_id)
        if not 2000 <= year <= 2100 or not 1 <= month <= 12:
            raise HTTPException(status_code=422, detail="Año o mes inválido.")
        try:
            validate_monthly_snapshot(client, payload)
            owners = exclusive_claims(repo.list())
            for assignment in payload.assignments:
                blocked, _blocked_adsets = blocked_resources(client, assignment.platform, assignment.account_id, owners)
                if assignment.campaign_id in blocked:
                    raise ValueError("La campaña está excluida por una asignación exclusiva de otro cliente.")
            snapshot = ReportingGroupSnapshot(
                client_id=client.id, year=year, month=month,
                groups=payload.groups, assignments=payload.assignments,
            )
            reporting_group_snapshot_repo.save(snapshot)
            return snapshot_payload(client.id, year, month)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from None
        except (ReportingGroupSnapshotStoreError, ReportingGroupStoreError) as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from None

    @app.post("/api/reporting-group-snapshots/{client_id}/{year}/{month}/copy-previous")
    def copy_previous_reporting_group_snapshot(client_id: str, year: int, month: int) -> dict[str, Any]:
        client = get_client(client_id)
        if not 2000 <= year <= 2100 or not 1 <= month <= 12:
            raise HTTPException(status_code=422, detail="Año o mes inválido.")
        previous_year, previous_month_value = previous_month(year, month)
        try:
            if reporting_group_snapshot_repo.get(client.id, year, month):
                raise HTTPException(status_code=409, detail="Ya existe una configuración para ese mes.")
            source = reporting_group_snapshot_repo.get(client.id, previous_year, previous_month_value)
            if source is None:
                raise HTTPException(status_code=409, detail="No existe un snapshot del mes anterior para copiar.")
            copied = ReportingGroupSnapshot(
                client_id=client.id, year=year, month=month,
                groups=source.groups, assignments=source.assignments,
            )
            validate_monthly_snapshot(client, ReportingGroupSnapshotDraft(groups=copied.groups, assignments=copied.assignments))
            reporting_group_snapshot_repo.create_if_absent(copied)
            return snapshot_payload(client.id, year, month)
        except HTTPException:
            raise
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from None
        except ReportingGroupSnapshotConflict as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from None
        except (ReportingGroupSnapshotStoreError, ReportingGroupStoreError) as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from None

    @app.post("/api/reporting-group-snapshots/{client_id}/{year}/{month}/initialize-from-current")
    def initialize_reporting_group_snapshot_from_current(client_id: str, year: int, month: int) -> dict[str, Any]:
        """Importación explícita de la config legacy a UN mes, nunca fallback de reporting."""
        client = get_client(client_id)
        if not 2000 <= year <= 2100 or not 1 <= month <= 12:
            raise HTTPException(status_code=422, detail="Año o mes inválido.")
        if (year, month) < (date.today().year, date.today().month):
            raise HTTPException(status_code=409, detail="La configuración actual no se puede importar a un mes histórico.")
        try:
            legacy = reporting_group_repo.list(client.id)
            if not legacy:
                raise HTTPException(status_code=409, detail="No hay configuración actual que importar.")
            copied = ReportingGroupSnapshot(
                client_id=client.id, year=year, month=month,
                groups=[ReportingGroupSnapshotGroup(
                    id=item.id, name=item.name, type=item.type, active=item.active,
                    enabled_platforms=item.enabled_platforms,
                ) for item in legacy],
                assignments=[MonthlyCampaignAssignment(
                    platform=assignment.platform,
                    account_id=assignment.account_id,
                    campaign_id=assignment.campaign_id,
                    reporting_group_id=item.id,
                ) for item in legacy for assignment in item.assignments],
            )
            validate_monthly_snapshot(client, ReportingGroupSnapshotDraft(groups=copied.groups, assignments=copied.assignments))
            reporting_group_snapshot_repo.create_if_absent(copied)
            return snapshot_payload(client.id, year, month)
        except HTTPException:
            raise
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from None
        except ReportingGroupSnapshotConflict as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from None
        except (ReportingGroupSnapshotStoreError, ReportingGroupStoreError) as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from None

    @app.get("/api/reporting/summary")
    def performance_summary(
        client_id: str = Query(...),
        start_date: str = RangeParams["start_date"],
        end_date: str = RangeParams["end_date"],
        refresh: bool = False,
    ) -> dict[str, Any]:
        client = get_client(client_id)
        rng, _ = parse_ranges(start_date, end_date, "none", None, None)
        clients = repo.list()
        try:
            groups_by_month, missing = resolve_monthly_group_config([client.id], rng[0], rng[1])
            if missing:
                raise HTTPException(status_code=409, detail=missing_group_configuration_response(missing))
            return build_performance_summary(
                service=service,
                client=client,
                clients=clients,
                budget_repository=budget_repo,
                start=rng[0],
                end=rng[1],
                refresh=refresh,
                reporting_groups_by_client_month=groups_by_month,
            )
        except HTTPException:
            raise
        except BudgetStoreError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from None
        except (ReportingGroupSnapshotStoreError, ReportingGroupStoreError) as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from None
        except ReportValidationError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from None

    # ------------------------------------------------------ Excel report
    @app.get("/api/reports/performance.xlsx")
    def export_performance_report(
        start_date: str = RangeParams["start_date"],
        end_date: str = RangeParams["end_date"],
    ) -> Response:
        rng, _ = parse_ranges(start_date, end_date, "none", None, None)
        clients = repo.list()
        try:
            groups_by_month, missing = resolve_monthly_group_config([item.id for item in clients if item.active], rng[0], rng[1])
            if missing:
                raise HTTPException(status_code=409, detail=missing_group_configuration_response(missing))
            woosync_data = {}
            uses_woosync = any(
                item.active and report_profile(item.id) is not None and report_profile(item.id).uses_woosync
                for item in clients
            )
            if uses_woosync and woosync_service.provider is not None:
                for period in month_slices(rng[0], rng[1]):
                    try:
                        woosync_data[period.label] = woosync_service.overview(
                            from_date=period.start.isoformat(), to_date=period.end.isoformat(),
                            branches=[], top_limit=DEFAULT_TOP_LIMIT, compare_to=None,
                        )
                    except (ProviderNotConfigured, ProviderError):
                        # WooSync es un bloque adicional; su indisponibilidad no
                        # debe impedir exportar las métricas publicitarias.
                        continue
            generated = generate_performance_report(
                service=service,
                clients=clients,
                budget_repository=budget_repo,
                start=rng[0],
                end=rng[1],
                owners=exclusive_claims(clients),
                reporting_groups_by_client_month=groups_by_month,
                woosync_data=woosync_data,
            )
        except HTTPException:
            raise
        except BudgetStoreError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from None
        except (ReportingGroupSnapshotStoreError, ReportingGroupStoreError) as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from None
        except ReportValidationError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from None
        return Response(
            content=generated.content,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": f'attachment; filename="{generated.filename}"'},
        )

    # ------------------------------------------------------------- WooSync
    @app.get("/api/woosync/overview")
    def woosync_overview(
        from_date: str | None = Query(None, alias="from"),
        to_date: str | None = Query(None, alias="to"),
        branches: str | None = None,
        top_limit: int = Query(DEFAULT_TOP_LIMIT, ge=1, le=MAX_TOP_LIMIT),
        compare_to: str | None = None,
    ) -> dict[str, Any]:
        try:
            clean_from = parse_date_filter(from_date, "from")
            clean_to = parse_date_filter(to_date, "to")
            if clean_from and clean_to and clean_from > clean_to:
                raise ValueError("from debe ser anterior o igual a to")
            clean_branches = parse_branches(branches)
            if compare_to is not None and compare_to not in WOOSYNC_COMPARE_VALUES:
                raise ValueError(f"compare_to debe ser uno de: {', '.join(sorted(WOOSYNC_COMPARE_VALUES))}")
            if compare_to == "previous_year" and not (clean_from and clean_to):
                raise ValueError("compare_to=previous_year requiere from y to")
        except ValueError as e:
            raise HTTPException(status_code=422, detail=str(e)) from None
        try:
            return woosync_service.overview(
                from_date=clean_from,
                to_date=clean_to,
                branches=clean_branches,
                top_limit=top_limit,
                compare_to=compare_to,
            )
        except ProviderNotConfigured as e:
            raise HTTPException(status_code=503, detail=str(e)) from None
        except ProviderError as e:
            raise HTTPException(status_code=502, detail=str(e)) from None

    # ------------------------------------------------------------ clients
    @app.get("/api/clients")
    def list_clients(active_only: bool = False) -> dict[str, Any]:
        clients = repo.list()
        if active_only:
            clients = [c for c in clients if c.active]
        return {"clients": [client_payload(c) for c in clients]}

    def client_payload(client: Client) -> dict[str, Any]:
        payload = client.model_dump()
        source = client.spotify_ads_sources[0] if client.spotify_ads_sources else None
        pinterest_source = client.pinterest_ads_sources[0] if client.pinterest_ads_sources else None
        payload.update({
            "spotify_source_id": source.ad_account_id if source else None,
            "spotify_linked": source is not None,
            "spotify_status": "linked" if source else "not_configured",
            "pinterest_source_id": pinterest_source.ad_account_id if pinterest_source else None,
            "pinterest_linked": pinterest_source is not None,
            "pinterest_status": "linked" if pinterest_source else "not_configured",
        })
        return payload

    @app.get("/api/clients/{client_id}")
    def read_client(client_id: str) -> dict[str, Any]:
        return client_payload(get_client(client_id))

    @app.post("/api/clients", status_code=201)
    def create_client(data: ClientCreate) -> dict[str, Any]:
        return client_payload(repo.create(data))

    @app.put("/api/clients/{client_id}")
    def update_client(client_id: str, data: ClientUpdate) -> dict[str, Any]:
        return client_payload(repo.update(client_id, data))

    # ------------------------------------------------------------ accounts
    def discovery(platform: str, refresh: bool) -> dict[str, Any]:
        result = service.discover(platform, refresh)
        # `assigned_to` = quién es DUEÑO de la cuenta (la consume entera). Una
        # asignación parcial (campañas sueltas) no convierte la cuenta en suya,
        # así que va aparte en `campaign_clients`: si no, el selector de cuenta
        # principal diría que la cuenta de un cliente pertenece a otro.
        owners: dict[str, dict[str, str]] = {}
        partial: dict[str, list[dict[str, Any]]] = {}
        for c in repo.list():
            if platform in AD_PLATFORMS:
                for src in ad_sources(c, platform):
                    if src.campaign_ids:
                        partial.setdefault(src.account_id, []).append(
                            {"id": c.id, "name": c.name, "campaigns": len(src.campaign_ids),
                             "allocation": src.allocation}
                        )
                    else:
                        owners[src.account_id] = {"id": c.id, "name": c.name}
            else:
                for account in account_ids(c, platform):
                    owners[account] = {"id": c.id, "name": c.name}
        result["accounts"] = [
            {**a, "assigned_to": owners.get(a["id"]),
             "reserved_for": ({"id": SPOTIFY_SANTUARIO_CLIENT_SLUG, "name": "Santuario Residencial"}
                              if platform == "spotify_ads" and a["id"] == SPOTIFY_SANTUARIO_AD_ACCOUNT_ID else None),
             "campaign_clients": partial.get(a["id"], [])}
            for a in result["accounts"]
        ]
        return result

    def campaigns(platform: str, account_id: str, refresh: bool) -> dict[str, Any]:
        """Campañas reales de una cuenta, para asignarlas a un cliente.

        Marca cada campaña con el cliente que ya la tiene asignada (y si es en
        exclusiva), para que la UI pueda avisar antes de provocar un conflicto.
        """
        result = service.campaigns(platform, account_id, refresh)
        if result["status"] != "ok":
            return result
        owners: dict[str, dict[str, Any]] = {}
        for c in repo.list():
            for src in ad_sources(c, platform):
                if src.account_id != str(account_id):
                    continue
                for cid in src.campaign_ids:
                    owners[cid] = {"id": c.id, "name": c.name, "allocation": src.allocation}
        result["campaigns"] = [{**c, "assigned_to": owners.get(str(c["campaign_id"]))} for c in result["campaigns"]]
        return result

    @app.get("/api/accounts/google-ads/{customer_id}/campaigns")
    def campaigns_google_ads(customer_id: str, refresh: bool = False):
        """Campañas de una cuenta de Google Ads (para "Fuentes de campañas")."""
        return campaigns("google_ads", customer_id, refresh)

    @app.get("/api/accounts/meta/{ad_account_id}/campaigns")
    def campaigns_meta(ad_account_id: str, refresh: bool = False):
        """Campañas de una cuenta publicitaria de Meta."""
        return campaigns("meta_ads", ad_account_id, refresh)

    @app.get("/api/accounts/spotify-ads/{ad_account_id}/campaigns")
    def campaigns_spotify_ads(ad_account_id: str, refresh: bool = False):
        """Catálogo de campañas de una fuente Spotify ya aprobada."""
        return campaigns("spotify_ads", ad_account_id, refresh)

    @app.get("/api/accounts/pinterest-ads/{ad_account_id}/campaigns")
    def campaigns_pinterest_ads(ad_account_id: str, refresh: bool = False):
        return campaigns("pinterest_ads", ad_account_id, refresh)

    @app.get("/api/accounts/meta/{ad_account_id}/campaigns/{campaign_id}/adsets")
    def adsets_meta(ad_account_id: str, campaign_id: str, refresh: bool = False):
        """Conjuntos de anuncios de una campaña, con quién los tiene asignados."""
        result = service.adsets_catalog("meta_ads", ad_account_id, campaign_id, refresh)
        if result["status"] != "ok":
            return result
        owners: dict[str, dict[str, Any]] = {}
        for c in repo.list():
            for src in ad_sources(c, "meta_ads"):
                if src.account_id != str(ad_account_id):
                    continue
                for sel in getattr(src, "adset_selections", []) or []:
                    if sel.campaign_id != str(campaign_id):
                        continue
                    for aid in sel.adset_ids:
                        owners[aid] = {"id": c.id, "name": c.name, "allocation": sel.allocation}
        result["adsets"] = [{**a, "assigned_to": owners.get(str(a["adset_id"]))} for a in result["adsets"]]
        return result

    @app.get("/api/accounts/google-ads")
    def accounts_google_ads(refresh: bool = False):
        """Jerarquía del MCC raíz (customer_client): clientes directos e
        indirectos, sin el propio MCC ni las cuentas ocultas."""
        return discovery("google_ads", refresh)

    @app.get("/api/accounts/google-ads/diagnostics")
    def accounts_google_ads_diagnostics():
        """Solo diagnóstico de accesos: compara la jerarquía del MCC con lo que
        ve list_accessible_customers() para la identidad del servidor."""
        return service.diagnostics("google_ads")

    @app.get("/api/accounts/meta")
    def accounts_meta(refresh: bool = False):
        return discovery("meta_ads", refresh)

    @app.get("/api/accounts/spotify-ads")
    def accounts_spotify_ads(refresh: bool = False):
        """Catálogo Spotify restringido a las fuentes reales aprobadas por provider."""
        return discovery("spotify_ads", refresh)

    @app.get("/api/accounts/pinterest-ads")
    def accounts_pinterest_ads(refresh: bool = False):
        return discovery("pinterest_ads", refresh)

    @app.get("/api/accounts/ga4")
    def accounts_ga4(refresh: bool = False):
        return discovery("ga4", refresh)

    # ----------------------------------------------------------- dashboard
    @app.get("/api/dashboard/{client_id}")
    def dashboard(
        client_id: str,
        start_date: str = RangeParams["start_date"],
        end_date: str = RangeParams["end_date"],
        comparison: str = "none",
        compare_start_date: str | None = None,
        compare_end_date: str | None = None,
        refresh: bool = False,
    ):
        client = get_client(client_id)
        rng, cmp = parse_ranges(start_date, end_date, comparison, compare_start_date, compare_end_date)
        # Las campañas cedidas en exclusiva a otro cliente no deben contarse aquí.
        out = service.dashboard(client, rng, cmp, refresh, exclusive_claims(repo.list()))
        out["comparison"] = comparison
        return out

    @app.get("/api/performance/detail")
    def performance_detail(
        client_id: str = Query(...),
        platform: Literal["google_ads", "meta_ads", "spotify_ads", "pinterest_ads"] = Query(...),
        start_date: str = RangeParams["start_date"],
        end_date: str = RangeParams["end_date"],
        level: Literal["campaign", "ad"] = "campaign",
        status: Literal["all", "active", "inactive"] = "all",
        spend: Literal["all", "with_spend", "without_spend"] = "all",
        refresh: bool = False,
    ) -> dict[str, Any]:
        client = get_client(client_id)
        rng, _ = parse_ranges(start_date, end_date, "none", None, None)
        if not account_ids(client, platform):
            raise HTTPException(status_code=409, detail=f"{client.name} no tiene {LABELS[platform]} asignado.")
        try:
            groups_by_month, missing = resolve_monthly_group_config([client.id], rng[0], rng[1])
            if missing:
                raise HTTPException(status_code=409, detail=missing_group_configuration_response(missing))
            result = service.performance_detail(
                client=client,
                platform=platform,
                start=rng[0],
                end=rng[1],
                level=level,
                status=status,
                spend=spend,
                owners=exclusive_claims(repo.list()),
                refresh=refresh,
            )
            slices = month_slices(rng[0], rng[1])
            for row in result["rows"]:
                reporting_groups = []
                for month_period in slices:
                    configured = groups_by_month.get((client.id, month_period.year, month_period.month), [])
                    active = [group for group in configured if group.active]
                    if not active:
                        continue
                    assigned = next((
                        group for group in active
                        if any(
                            item.platform == platform
                            and item.account_id == str(row.get("source_account_id") or row.get("account_id") or "")
                            and item.campaign_id == str(row.get("campaign_id") or "")
                            for item in group.assignments
                        )
                    ), None)
                    reporting_groups.append({
                        "year": month_period.year,
                        "month": month_period.month,
                        "id": assigned.id if assigned else None,
                        "name": assigned.name if assigned else "General / Sin grupo",
                        "type": assigned.type if assigned else "general",
                    })
                row["reporting_groups"] = reporting_groups
            result["grouping_active"] = any(bool(value) for value in groups_by_month.values())
            return result
        except HTTPException:
            raise
        except ProviderNotConfigured as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from None
        except ProviderError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from None

    def single(platform: str, client_id: str, start_date: str, end_date: str, comparison: str,
               compare_start_date: str | None, compare_end_date: str | None, refresh: bool) -> dict[str, Any]:
        client = get_client(client_id)
        rng, cmp = parse_ranges(start_date, end_date, comparison, compare_start_date, compare_end_date)
        return service.platform_block(client, platform, rng, cmp, refresh, exclusive_claims(repo.list()))

    def platform_route(path: str, platform: str, transform=None):
        def handler(
            client_id: str = Query(...),
            start_date: str = RangeParams["start_date"],
            end_date: str = RangeParams["end_date"],
            comparison: str = "none",
            compare_start_date: str | None = None,
            compare_end_date: str | None = None,
            refresh: bool = False,
        ):
            block = single(platform, client_id, start_date, end_date, comparison, compare_start_date, compare_end_date, refresh)
            return transform(block) if transform else block

        handler.__name__ = path.strip("/").replace("/", "_").replace("-", "_")
        app.get(path, summary=f"{LABELS[platform]} — {path.rsplit('/', 1)[-1]}")(handler)

    def meta_campaigns_only(block: dict[str, Any]) -> dict[str, Any]:
        for k in ("current", "previous"):
            if block.get(k):
                block[k] = {kk: vv for kk, vv in block[k].items() if kk != "adsets"}
        return block

    def meta_adsets_only(block: dict[str, Any]) -> dict[str, Any]:
        for k in ("current", "previous"):
            if block.get(k):
                block[k] = {"adsets": block[k].get("adsets", [])}
        return block

    platform_route("/api/google-ads/performance", "google_ads")
    platform_route("/api/meta/performance", "meta_ads", meta_campaigns_only)
    platform_route("/api/meta/adsets", "meta_ads", meta_adsets_only)
    platform_route("/api/spotify-ads/performance", "spotify_ads")
    platform_route("/api/pinterest-ads/performance", "pinterest_ads")
    platform_route("/api/ga4/performance", "ga4")

    # ------------------------------------------- frontend compilado (prod)
    # Se monta al final: /health y /api/* tienen prioridad; el resto es la SPA.
    if settings.static_dir.is_dir() and (settings.static_dir / "index.html").exists():
        app.mount("/", SPAStaticFiles(directory=settings.static_dir, html=True), name="dashboard")
    else:
        log.info("Frontend compilado no encontrado en %s; solo se sirve la API.", settings.static_dir)

    return app


app = create_app()

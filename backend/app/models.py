"""Modelos del registro de clientes."""
from __future__ import annotations

import re
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field, field_validator, model_validator

from .metrics import normalize_metric_family
from .spotify_security import (
    SPOTIFY_SANTUARIO_AD_ACCOUNT_ID,
    SPOTIFY_SANTUARIO_BUSINESS_ID,
    SPOTIFY_SANTUARIO_CLIENT_SLUG,
    require_santuario_binding,
)

SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")

# Tope de propiedades GA4 por cliente: evita que una edición accidental dispare
# cientos de llamadas a la Data API en cada carga del dashboard.
MAX_GA4_PROPERTIES = 25
# Topes de la asignación de campañas: acotan cuántas consultas dispara un
# dashboard y cuán larga puede ser una cláusula IN de GAQL / filtering de Meta.
MAX_AD_SOURCES = 10
MAX_CAMPAIGNS_PER_SOURCE = 200
MAX_ADSET_SELECTIONS = 200
MAX_ADSETS_PER_CAMPAIGN = 500
REPORT_CATEGORIES = ("ENTRETENIMIENTO", "B2B / CORPORATIVO", "ECOMMERCE", "GENERAL")

#: Cómo se atribuye una campaña asignada desde la cuenta de otro cliente.
#:  - exclusive: solo la contabiliza el cliente destino (evita doble conteo);
#:    el dueño de la cuenta deja de verla.
#:  - shared: la contabilizan ambos.
Allocation = Literal["exclusive", "shared"]


def _digits(v: str | None, prefix: str = "") -> str | None:
    if v is None:
        return None
    v = str(v).strip()
    if prefix and v.startswith(prefix):
        v = v[len(prefix):]
    v = v.replace("-", "")
    if not v:
        return None
    if not v.isdigit():
        raise ValueError("Debe ser un ID numérico")
    return v


def _property_ids(values: list[str] | None) -> list[str] | None:
    """Normaliza una lista de property IDs: quita 'properties/', vacíos y duplicados."""
    if values is None:
        return None
    if isinstance(values, str):  # tolerancia: un solo ID enviado como string
        values = [values]
    out: list[str] = []
    for v in values:
        pid = _digits(v, "properties/")
        if pid and pid not in out:
            out.append(pid)
    if len(out) > MAX_GA4_PROPERTIES:
        raise ValueError(f"Máximo {MAX_GA4_PROPERTIES} propiedades GA4 por cliente")
    return out


def _id_list(values, limit: int, label: str) -> list[str]:
    """IDs numéricos normalizados, sin vacíos ni duplicados, en orden estable."""
    if values is None:
        return []
    if isinstance(values, str):
        values = [values]
    out: list[str] = []
    for v in values:
        cid = _digits(v)
        if cid and cid not in out:
            out.append(cid)
    if len(out) > limit:
        raise ValueError(f"Máximo {limit} {label}")
    return out


def _hidden_metrics(values: list[str] | str | None) -> list[str] | None:
    """Normaliza `hidden_metrics`: colapsa variantes a su familia canónica,
    sin vacíos ni duplicados. `None` se conserva (campo ausente en un PATCH,
    distinto de "lista vacía" explícita)."""
    if values is None:
        return None
    if isinstance(values, str):
        values = [values]
    out: list[str] = []
    for v in values:
        fam = normalize_metric_family(v)
        if fam and fam not in out:
            out.append(fam)
    return out


def _campaign_ids(values: list[str] | None) -> list[str]:
    """IDs de campaña normalizados, sin vacíos ni duplicados, en orden estable.

    Lista vacía significa "todas las campañas elegibles de esta cuenta"
    (ver AdSource.campaign_ids), nunca "ninguna".
    """
    return _id_list(values, MAX_CAMPAIGNS_PER_SOURCE, "campañas por fuente")


class AdSource(BaseModel):
    """Campañas que un cliente consume de UNA cuenta publicitaria.

    `campaign_ids` vacío = todas las campañas elegibles de la cuenta (menos las
    que otro cliente tenga como exclusive). Con IDs = solo esas campañas.
    La selección es siempre por ID real; los nombres son solo para la UI.
    """

    campaign_ids: list[str] = Field(default_factory=list)
    allocation: Allocation = "exclusive"

    @field_validator("campaign_ids", mode="before")
    @classmethod
    def _campaigns(cls, v) -> list[str]:
        return _campaign_ids(v)


class GoogleAdsSource(AdSource):
    customer_id: str
    # MCC por el que se accede (header login-customer-id). Si falta, el
    # proveedor cae al MCC raíz de la agencia.
    login_customer_id: str | None = None

    @field_validator("customer_id", "login_customer_id")
    @classmethod
    def _ids(cls, v: str | None) -> str | None:
        return _digits(v)

    @property
    def account_id(self) -> str:
        return self.customer_id


class AdSetSelection(BaseModel):
    """Conjuntos de anuncios concretos DENTRO de una campaña.

    Es el nivel más específico: seleccionar ad sets NO equivale a seleccionar la
    campaña entera. La campaña aparecerá como "parcial" y sus métricas serán
    exclusivamente la suma de estos ad sets.
    """

    campaign_id: str
    adset_ids: list[str] = Field(default_factory=list)
    allocation: Allocation = "exclusive"

    @field_validator("campaign_id")
    @classmethod
    def _campaign(cls, v: str) -> str:
        cid = _digits(v)
        if not cid:
            raise ValueError("adset_selections.campaign_id es obligatorio")
        return cid

    @field_validator("adset_ids", mode="before")
    @classmethod
    def _adsets(cls, v) -> list[str]:
        ids = _id_list(v, MAX_ADSETS_PER_CAMPAIGN, "conjuntos de anuncios por campaña")
        if not ids:
            raise ValueError("Una selección de conjuntos de anuncios no puede estar vacía")
        return ids


class MetaAdsSource(AdSource):
    """Lo que un cliente consume de UNA cuenta de Meta, en tres niveles.

    - cuenta completa  : `campaign_ids` y `adset_selections` vacíos.
    - campañas enteras : `campaign_ids` con IDs.
    - ad sets sueltos  : `adset_selections`, una entrada por campaña.

    Los tres pueden combinarse: campañas enteras de una parte de la cuenta y
    solo algunos ad sets de otra campaña.
    """

    ad_account_id: str
    adset_selections: list[AdSetSelection] = Field(default_factory=list)

    @field_validator("ad_account_id")
    @classmethod
    def _ids(cls, v: str | None) -> str | None:
        return _digits(v, "act_")

    @field_validator("adset_selections")
    @classmethod
    def _selections(cls, v: list[AdSetSelection]) -> list[AdSetSelection]:
        seen: set[str] = set()
        for sel in v or []:
            if sel.campaign_id in seen:
                raise ValueError(f"La campaña {sel.campaign_id} está repetida en adset_selections")
            seen.add(sel.campaign_id)
        if len(v or []) > MAX_ADSET_SELECTIONS:
            raise ValueError(f"Máximo {MAX_ADSET_SELECTIONS} campañas con selección de ad sets por fuente")
        return v or []

    @model_validator(mode="after")
    def _no_overlap(self) -> "MetaAdsSource":
        """Una campaña entera y una selección de sus ad sets se contradicen."""
        both = set(self.campaign_ids) & {s.campaign_id for s in self.adset_selections}
        if both:
            raise ValueError(
                f"La campaña {', '.join(sorted(both))} está a la vez como campaña completa y como "
                "selección de conjuntos de anuncios: elige un nivel."
            )
        return self

    @property
    def account_id(self) -> str:
        return self.ad_account_id

    @property
    def is_whole_account(self) -> bool:
        return not self.campaign_ids and not self.adset_selections


class SpotifyAdsSource(BaseModel):
    """Vínculo explícito y no descubrible de Spotify Ads para Santuario."""

    business_id: str
    ad_account_id: str

    @field_validator("business_id", "ad_account_id")
    @classmethod
    def _uuid(cls, value: str) -> str:
        try:
            return str(UUID(str(value).strip()))
        except (ValueError, AttributeError, TypeError):
            raise ValueError("Debe ser un UUID válido") from None

    @model_validator(mode="after")
    def _approved_ids(self) -> "SpotifyAdsSource":
        if self.business_id != SPOTIFY_SANTUARIO_BUSINESS_ID:
            raise ValueError("Business de Spotify no autorizado")
        if self.ad_account_id != SPOTIFY_SANTUARIO_AD_ACCOUNT_ID:
            raise ValueError("Ad account de Spotify no autorizado")
        return self

    @property
    def account_id(self) -> str:
        return self.ad_account_id


class PinterestAdsSource(AdSource):
    """Cuenta Pinterest asignada al cliente; solo se guardan metadatos públicos."""

    source_id: str
    ad_account_id: str
    display_name: str | None = None
    currency: str | None = None
    status: str | None = None

    @field_validator("ad_account_id")
    @classmethod
    def _pinterest_account_id(cls, value: str) -> str:
        normalized = _digits(value)
        if not normalized:
            raise ValueError("Pinterest ad_account_id es obligatorio")
        return normalized

    @field_validator("source_id")
    @classmethod
    def _pinterest_source_id(cls, value: str) -> str:
        normalized = str(value).strip()
        if not normalized:
            raise ValueError("Pinterest source_id es obligatorio")
        return normalized

    @property
    def account_id(self) -> str:
        return self.ad_account_id


def primary_source(sources: list) -> Any:
    """La "cuenta principal": la primera fuente que toma la cuenta ENTERA.

    Una fuente con campañas seleccionadas es una asignación parcial sobre la
    cuenta de otro cliente, no una cuenta propia, así que nunca es principal.
    Devuelve None si el cliente solo tiene asignaciones parciales.
    """
    return next((s for s in sources or [] if not s.campaign_ids and not getattr(s, "adset_selections", None)), None)


def _dedupe_sources(sources: list, id_attr: str, label: str) -> list:
    """Una cuenta no puede aparecer dos veces como fuente del mismo cliente."""
    seen: set[str] = set()
    out = []
    for s in sources or []:
        key = getattr(s, id_attr)
        if not key:
            continue
        if key in seen:
            raise ValueError(f"La cuenta {label} {key} está repetida en las fuentes del cliente")
        seen.add(key)
        out.append(s)
    if len(out) > MAX_AD_SOURCES:
        raise ValueError(f"Máximo {MAX_AD_SOURCES} fuentes de {label} por cliente")
    return out


class ClientFields(BaseModel):
    """Campos editables. Solo IDs de cuentas: nunca tokens ni secretos."""

    name: str | None = Field(default=None, min_length=1, max_length=120)
    slug: str | None = Field(default=None, max_length=80)
    logo: str | None = Field(default=None, max_length=500)
    active: bool | None = None
    report_category: str | None = None
    report_position: int | None = Field(default=None, ge=1)
    # --- Google Ads / Meta Ads -------------------------------------------
    # Campos históricos de "una cuenta por cliente". Se conservan por
    # compatibilidad: son el espejo de la PRIMERA fuente (la cuenta principal).
    # La fuente de verdad es google_ads_sources / meta_ads_sources.
    google_ads_customer_id: str | None = None
    # MCC por el que se accede a la cuenta (header login-customer-id). Se
    # completa automáticamente desde "Descubrir cuentas".
    google_ads_login_customer_id: str | None = None
    meta_ad_account_id: str | None = None
    # Cuentas (propias o de otros clientes) de las que este cliente consume
    # campañas. Ver AdSource.
    google_ads_sources: list[GoogleAdsSource] | None = None
    meta_ads_sources: list[MetaAdsSource] | None = None
    spotify_ads_sources: list[SpotifyAdsSource] | None = None
    pinterest_ads_sources: list[PinterestAdsSource] | None = None
    # Campo histórico de una sola propiedad. Se mantiene por compatibilidad con
    # clientes/API antiguos: al guardar se sincroniza con la primera de
    # ga4_property_ids (ver Client._migrate_ga4).
    ga4_property_id: str | None = None
    # Varias propiedades GA4 por cliente: el dashboard las consolida y también
    # permite revisarlas una por una.
    ga4_property_ids: list[str] | None = None
    # Cuenta GA4 contenedora de la propiedad. Informativa (la consulta usa
    # property_id); se completa desde "Descubrir cuentas".
    ga4_account_id: str | None = None
    currency: str | None = Field(default=None, min_length=3, max_length=3)
    timezone: str | None = Field(default=None, max_length=64)
    brand_color: str | None = Field(default=None, max_length=16)
    notes: str | None = Field(default=None, max_length=1000)
    # Métricas que este cliente no debe ver en el dashboard, por familia
    # canónica (ver metrics.normalize_metric_family). None = campo ausente en
    # el PATCH (no tocar); [] = sin métricas ocultas.
    hidden_metrics: list[str] | None = None

    @field_validator("slug")
    @classmethod
    def _slug(cls, v: str | None) -> str | None:
        if v is not None and not SLUG_RE.match(v):
            raise ValueError("slug solo admite minúsculas, números y guiones")
        return v

    @field_validator("google_ads_customer_id", "google_ads_login_customer_id")
    @classmethod
    def _gads(cls, v: str | None) -> str | None:
        return _digits(v)

    @field_validator("meta_ad_account_id")
    @classmethod
    def _meta(cls, v: str | None) -> str | None:
        return _digits(v, "act_")

    @field_validator("ga4_property_id")
    @classmethod
    def _ga4(cls, v: str | None) -> str | None:
        return _digits(v, "properties/")

    @field_validator("ga4_property_ids")
    @classmethod
    def _ga4_list(cls, v: list[str] | None) -> list[str] | None:
        return _property_ids(v)

    @field_validator("google_ads_sources")
    @classmethod
    def _gads_sources(cls, v):
        return None if v is None else _dedupe_sources(v, "customer_id", "Google Ads")

    @field_validator("meta_ads_sources")
    @classmethod
    def _meta_sources(cls, v):
        return None if v is None else _dedupe_sources(v, "ad_account_id", "Meta Ads")

    @field_validator("spotify_ads_sources")
    @classmethod
    def _spotify_sources(cls, v):
        return None if v is None else _dedupe_sources(v, "ad_account_id", "Spotify Ads")

    @field_validator("pinterest_ads_sources")
    @classmethod
    def _pinterest_sources(cls, v):
        return None if v is None else _dedupe_sources(v, "ad_account_id", "Pinterest Ads")

    @field_validator("ga4_account_id")
    @classmethod
    def _ga4_account(cls, v: str | None) -> str | None:
        return _digits(v, "accounts/")

    @field_validator("currency")
    @classmethod
    def _currency(cls, v: str | None) -> str | None:
        return v.upper() if v else v

    @field_validator("report_category")
    @classmethod
    def _report_category(cls, value: str | None) -> str | None:
        if value is not None and value not in REPORT_CATEGORIES:
            raise ValueError(f"Categoría de Excel debe ser una de: {', '.join(REPORT_CATEGORIES)}")
        return value

    @field_validator("logo")
    @classmethod
    def _logo(cls, v: str | None) -> str | None:
        if v and not (v.startswith("https://") or v.startswith("/")):
            raise ValueError("El logo debe ser una URL https:// o una ruta local /…")
        return v or None

    @field_validator("hidden_metrics", mode="before")
    @classmethod
    def _hidden_metrics_fields(cls, v):
        return _hidden_metrics(v)


class ClientCreate(ClientFields):
    name: str = Field(min_length=1, max_length=120)


class ClientUpdate(ClientFields):
    pass


class Client(BaseModel):
    id: str
    name: str
    slug: str
    logo: str | None = None
    active: bool = True
    report_category: str | None = None
    report_position: int | None = Field(default=None, ge=1)
    google_ads_customer_id: str | None = None
    google_ads_login_customer_id: str | None = None
    meta_ad_account_id: str | None = None
    google_ads_sources: list[GoogleAdsSource] = Field(default_factory=list)
    meta_ads_sources: list[MetaAdsSource] = Field(default_factory=list)
    spotify_ads_sources: list[SpotifyAdsSource] = Field(default_factory=list)
    pinterest_ads_sources: list[PinterestAdsSource] = Field(default_factory=list)
    # Espejo de la primera propiedad de ga4_property_ids (compatibilidad).
    ga4_property_id: str | None = None
    ga4_property_ids: list[str] = Field(default_factory=list)
    ga4_account_id: str | None = None
    currency: str = "MXN"
    timezone: str = "America/Mexico_City"
    brand_color: str | None = None
    notes: str | None = None
    # Backward-compatible: los registros antiguos no lo traen y deben
    # comportarse como lista vacía (ninguna métrica oculta).
    hidden_metrics: list[str] = Field(default_factory=list)

    @field_validator("hidden_metrics", mode="before")
    @classmethod
    def _hidden_metrics_client(cls, v):
        return _hidden_metrics(v) or []

    @model_validator(mode="after")
    def _migrate_ad_sources(self) -> "Client":
        """Mantiene sincronizados los campos de una cuenta y las listas de fuentes.

        - Registro antiguo (solo `google_ads_customer_id` / `meta_ad_account_id`)
          → se migra a una fuente con `campaign_ids: []` y `allocation: exclusive`,
          es decir "todas las campañas de mi cuenta", que es el comportamiento
          que ese registro ya tenía.
        - El campo suelto es el espejo de la CUENTA PRINCIPAL, y solo existe
          cuenta principal cuando alguna fuente toma la cuenta ENTERA. Un
          cliente que únicamente consume campañas sueltas de la cuenta de otro
          NO tiene cuenta propia: su campo suelto queda en None, para no
          presentar la cuenta ajena como si fuera suya.
        """
        gads = list(self.google_ads_sources or [])
        if (
            not gads
            and self.google_ads_customer_id
            and "google_ads_sources" not in self.model_fields_set
        ):
            gads = [GoogleAdsSource(
                customer_id=self.google_ads_customer_id,
                login_customer_id=self.google_ads_login_customer_id,
            )]
        object.__setattr__(self, "google_ads_sources", gads)
        primary = primary_source(gads)
        object.__setattr__(self, "google_ads_customer_id", primary.customer_id if primary else None)
        object.__setattr__(self, "google_ads_login_customer_id", primary.login_customer_id if primary else None)

        meta = list(self.meta_ads_sources or [])
        if (
            not meta
            and self.meta_ad_account_id
            and "meta_ads_sources" not in self.model_fields_set
        ):
            meta = [MetaAdsSource(ad_account_id=self.meta_ad_account_id)]
        object.__setattr__(self, "meta_ads_sources", meta)
        primary = primary_source(meta)
        object.__setattr__(self, "meta_ad_account_id", primary.ad_account_id if primary else None)
        return self

    @model_validator(mode="after")
    def _migrate_ga4(self) -> "Client":
        """Mantiene sincronizados el campo antiguo y el nuevo array.

        - Registro antiguo (solo `ga4_property_id`) → se migra al array.
        - Array poblado → `ga4_property_id` queda como su primer elemento, así
          cualquier consumidor que aún lea el campo suelto sigue funcionando.
        """
        ids = _property_ids(self.ga4_property_ids) or []
        if not ids and self.ga4_property_id:
            ids = [self.ga4_property_id]
        object.__setattr__(self, "ga4_property_ids", ids)
        object.__setattr__(self, "ga4_property_id", ids[0] if ids else None)
        return self

    @model_validator(mode="after")
    def _spotify_isolation(self) -> "Client":
        sources = list(self.spotify_ads_sources or [])
        if len(sources) > 1:
            raise ValueError("Santuario solo puede tener una fuente de Spotify Ads")
        for source in sources:
            require_santuario_binding(
                client_slug=self.slug,
                business_id=source.business_id,
                ad_account_id=source.ad_account_id,
            )
        if sources and self.slug != SPOTIFY_SANTUARIO_CLIENT_SLUG:  # mensaje estable para Pydantic
            raise ValueError("Spotify Ads solo puede asociarse al cliente Santuario Residencial")
        return self


ACCOUNT_FIELDS = {
    "google_ads": "google_ads_customer_id",
    "meta_ads": "meta_ad_account_id",
    "ga4": "ga4_property_id",
}

# Plataformas con varias cuentas por cliente → atributo con la lista canónica.
MULTI_ACCOUNT_FIELDS = {
    "ga4": "ga4_property_ids",
    "google_ads": "google_ads_sources",
    "meta_ads": "meta_ads_sources",
    "spotify_ads": "spotify_ads_sources",
    "pinterest_ads": "pinterest_ads_sources",
}

#: Plataformas cuyas cuentas se describen con AdSource (campañas asignables).
AD_PLATFORMS = ("google_ads", "meta_ads", "pinterest_ads")


def ad_sources(client: "Client", platform: str) -> list[AdSource]:
    """Fuentes de campañas del cliente en una plataforma de anuncios."""
    if platform not in AD_PLATFORMS:
        return []
    return list(getattr(client, MULTI_ACCOUNT_FIELDS[platform]) or [])


def account_ids(client: "Client", platform: str) -> list[str]:
    """IDs de cuenta del cliente en una plataforma, sin repetir y en orden."""
    if platform in AD_PLATFORMS:
        return [s.account_id for s in ad_sources(client, platform)]
    if platform == "spotify_ads":
        return [s.ad_account_id for s in client.spotify_ads_sources]
    if platform == "pinterest_ads":
        return [s.ad_account_id for s in client.pinterest_ads_sources]
    return list(getattr(client, MULTI_ACCOUNT_FIELDS[platform]) or [])


#: Niveles de la jerarquía, del más amplio al más específico.
LEVELS = ("account", "campaign", "adset", "ad")

ResourceKey = tuple[str, str, str, str]


def resource_key(platform: str, account_id: str, campaign_id: str | None = None,
                 adset_id: str | None = None) -> ResourceKey:
    """Identidad REAL de un recurso publicitario.

    Nunca se deduplica por un ID de nivel inferior sin su contexto: dos cuentas
    pueden reutilizar el mismo campaign_id, y dos campañas el mismo adset_id.
    La identidad siempre arrastra la cuenta (y la campaña, para un ad set).
    """
    return (platform, str(account_id), str(campaign_id or ""), str(adset_id or ""))


def key_level(key: ResourceKey) -> str:
    """'account', 'campaign' o 'adset', según hasta dónde llega la clave."""
    if key[3]:
        return "adset"
    return "campaign" if key[2] else "account"


# Compatibilidad: la clave de campaña es la de recurso sin ad set.
def campaign_key(platform: str, account_id: str, campaign_id: str) -> ResourceKey:
    return resource_key(platform, account_id, campaign_id)


def exclusive_claims(clients: list["Client"]) -> dict[ResourceKey, str]:
    """Recursos reclamados en exclusiva → id del cliente que los posee.

    Se registran los tres niveles:

    - cuenta  : una fuente que toma la cuenta ENTERA en exclusiva;
    - campaña : cada ID de `campaign_ids`;
    - ad set  : cada ID de cada `adset_selections`, con su propia `allocation`.

    Una fuente de cuenta entera no reclama campañas concretas: reclama "el
    resto", y por eso es la que se recorta cuando otro cliente se lleva algo.
    """
    owners: dict[ResourceKey, str] = {}
    for c in clients:
        for platform in AD_PLATFORMS:
            for src in ad_sources(c, platform):
                if src.allocation == "exclusive":
                    if getattr(src, "is_whole_account", not src.campaign_ids):
                        owners[resource_key(platform, src.account_id)] = c.id
                    for cid in src.campaign_ids:
                        owners[resource_key(platform, src.account_id, cid)] = c.id
                for sel in getattr(src, "adset_selections", []) or []:
                    if sel.allocation != "exclusive":
                        continue
                    for aid in sel.adset_ids:
                        owners[resource_key(platform, src.account_id, sel.campaign_id, aid)] = c.id
    return owners


def blocked_resources(client: "Client", platform: str, account_id: str,
                      owners: dict[ResourceKey, str]) -> tuple[set[str], dict[str, set[str]]]:
    """Lo que OTROS clientes tienen en exclusiva dentro de una cuenta.

    Devuelve (campañas bloqueadas, {campaña: ad sets bloqueados}). Es la base
    del recorte jerárquico: una selección más específica recorta a una más
    amplia, nunca al revés.
    """
    campaigns: set[str] = set()
    adsets: dict[str, set[str]] = {}
    for key, owner in owners.items():
        if key[0] != platform or key[1] != str(account_id) or owner == client.id:
            continue
        level = key_level(key)
        if level == "campaign":
            campaigns.add(key[2])
        elif level == "adset":
            adsets.setdefault(key[2], set()).add(key[3])
    return campaigns, adsets

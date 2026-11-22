# Marketing Performance — Backend (FastAPI)

API que consulta Google Ads, Meta Ads, Spotify Ads y GA4 **desde el servidor** y entrega datos
normalizados al dashboard. El navegador nunca recibe tokens ni credenciales.

## Iniciar

```bash
cd /srv/marketing-performance-cloud/backend
# Meta: definir el token SOLO en el entorno (no en archivos):
#   read -rs META_ACCESS_TOKEN && export META_ACCESS_TOKEN
../.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000
```

- Docs interactivas: http://127.0.0.1:8000/docs
- Si existe `../dashboard/dist`, el backend también sirve el dashboard compilado en `/`
  (un solo proceso en producción: `npm run build` y luego uvicorn).
- Dependencias: `../.venv/bin/pip install -r requirements-dev.txt` (runtime + tests; la imagen solo instala `requirements.txt`)

## Tests

```bash
../.venv/bin/python -m pytest -q
```

Los tests usan proveedores falsos: no llaman a Google, Meta ni Spotify.

## Credenciales

| Plataforma | Fuente | Variable |
|---|---|---|
| Google Ads | ADC (`gcloud auth application-default login`) | `GOOGLE_ADS_DEVELOPER_TOKEN` opcional |
| GA4 | ADC **con scope `analytics.readonly`** (propias, no las de Ads) | — |
| Meta Ads | Token de sistema/usuario con `ads_read` | `META_ACCESS_TOKEN` |
| Spotify Ads | OAuth refresh token, renovado en memoria | `SPOTIFY_CLIENT_ID`, `SPOTIFY_CLIENT_SECRET`, `SPOTIFY_REFRESH_TOKEN` |

El token de Meta se envía en el header `Authorization` (no en la URL), se lee del
entorno en cada petición y los mensajes de error pasan por `sanitize()`.

Cada plataforma pide sus propias credenciales: Google Ads usa la configuración de
la librería `google-ads` (scope `adwords`) y GA4 llama a
`google.auth.default(scopes=["https://www.googleapis.com/auth/analytics.readonly"])`
y pasa esas credenciales a `AnalyticsAdminServiceClient` y
`BetaAnalyticsDataClient`. Sin ese scope el token de la cuenta de servicio
(scope `cloud-platform`) hace que la Admin API responda
**403 ACCESS_TOKEN_SCOPE_INSUFFICIENT**. El scope autoriza la API, no el acceso a
los datos: la cuenta de servicio de Cloud Run también tiene que estar agregada
como usuario con permiso de lectura en la cuenta/propiedades de GA4.

## Google Analytics: cuentas y propiedades

`GET /api/accounts/ga4` recorre **todas** las páginas de
`ListAccountSummaries` (Admin API) y devuelve **todas las propiedades de todas
las cuentas** accesibles por la identidad del servidor. Cada propiedad trae
`account_id`, `account_name`, `property_id`, `property_name`,
`property_resource` y `platform: "ga4"`, y la respuesta incluye además
`groups` (cuenta → propiedades) para pintar el selector "Cuenta → Propiedad — ID".

Cada cliente guarda su propio `ga4_property_id` (y, informativo,
`ga4_account_id`): el dashboard consulta la Data API con la propiedad del
cliente seleccionado, nunca una global, así que dos clientes nunca comparten
datos. Una cuenta puede tener N propiedades y un cliente elige una.

## Exclusión de clientes y cuentas

`backend/app/exclusions.py` tiene la lista central `EXCLUDED_CLIENT_PATTERNS`
(hoy `("vidanta",)`). La comparación es por subcadena y sin distinguir
mayúsculas, así que cubre `Vidanta`, `VIDANTA`, `vidanta` y cualquier nombre que
las contenga. Se aplica en dos capas del backend —el registro de clientes
(`registry.py`: los oculta al leer y rechaza crearlos con HTTP 400) y el
descubrimiento (`service.discover`: filtra las cuentas de las tres
plataformas)— y una tercera en el navegador
(`dashboard/src/config/exclusions.ts`), para que "Descubrir cuentas" no pueda
reintroducirlas. La respuesta solo informa `excluded.count`: ni los nombres ni
los patrones llegan al navegador; el detalle queda en el log del servidor.

Esto es solo visibilidad en esta aplicación: **no** cambia permisos ni borra
nada en Google Ads, Meta Ads o Google Analytics.

Spotify aplica una protección distinta y más estricta: no depende de nombres ni
de la blocklist. `app/spotify_security.py` fija el vínculo
`santuario-residencial` → business `e27cd146-ddc1-4551-9757-25fe6bc04016` →
ad account `79b62668-1427-4b29-8b73-f4cba3525d0d`. El modelo, el proveedor y
cada fila normalizada vuelven a validar esos IDs. El flujo de rendimiento no
descubre ni selecciona automáticamente businesses o ad accounts.

## Google Ads: de dónde salen las cuentas

`GET /api/accounts/google-ads` consulta `customer_client` en el **MCC raíz**
(`GOOGLE_ADS_MANAGER_CUSTOMER_ID`, por defecto `7954113535`) con
`login-customer-id` = ese MCC, así que devuelve los clientes **directos**
(nivel 1) y los **indirectos** (nivel ≥ 2, colgados de un MCC secundario).
Por defecto excluye el propio MCC (nivel 0) y las cuentas ocultas (`hidden`);
`level` y `account_type` (`client` = anunciante, `manager` = MCC secundario)
viajan en cada cuenta y `summary` trae los totales separados.

`list_accessible_customers()` **no** alimenta esta lista: solo ve lo que se
compartió directamente con la identidad que autentica, que en Cloud Run es la
cuenta de servicio y por eso allí devolvía muchas menos cuentas. Queda
disponible en `/api/accounts/google-ads/diagnostics` para comparar accesos.

Toda cuenta descubierta lleva `login_customer_id` = MCC raíz, y
`performance()` usa ese MCC cuando el cliente del registro no tiene uno
guardado: sin `login-customer-id` la API rechaza las cuentas cliente.

## Endpoints

| Método | Ruta | Descripción |
|---|---|---|
| GET | `/health` | Liveness para Cloud Run: `{"status":"ok"}` (no llama APIs externas) |
| GET | `/api/health` | Estado y si hay credenciales (sin valores) |
| GET | `/api/clients` | Registro de clientes (`?active_only=true`) |
| GET | `/api/clients/{id}` | Un cliente |
| POST | `/api/clients` | Crear cliente |
| PUT | `/api/clients/{id}` | Editar / activar / guardar mapeo (parcial; `null` desasigna) |
| GET | `/api/accounts/google-ads` | Jerarquía del MCC raíz vía `customer_client` (clientes directos e indirectos) |
| GET | `/api/accounts/google-ads/diagnostics` | Solo diagnóstico: jerarquía vs. `list_accessible_customers()` |
| GET | `/api/accounts/meta` | Ad accounts accesibles |
| GET | `/api/accounts/ga4` | Todas las propiedades de todas las cuentas GA4 (+ `groups`) |
| GET | `/api/dashboard/{client_id}` | Todas las plataformas, actual + comparado |
| GET | `/api/google-ads/performance` | Campañas + diario |
| GET | `/api/meta/performance` | Campañas + diario + totales de cuenta |
| GET | `/api/meta/adsets` | Rendimiento por Ad Set |
| GET | `/api/spotify-ads/performance` | Totales, diario, campañas, ad sets y anuncios de la cuenta aprobada |
| GET | `/api/ga4/performance` | Totales, diario, source/medium, campaign, landing page |
| GET | `/api/budgets` | Presupuestos persistentes; filtro opcional `client_id` |
| PUT | `/api/budgets/{client_id}/{platform}/{year}/{month}` | Crea o reemplaza únicamente el importe del presupuesto mensual |
| DELETE | `/api/budgets/{client_id}/{platform}/{year}/{month}` | Retira solo ese presupuesto; `reporting_group_id` identifica la variante agrupada |
| GET | `/api/reporting-groups/{client_id}` | Lee grupos y asignaciones por campaña del cliente |
| PUT | `/api/reporting-groups/{client_id}` | Reemplaza la configuración; grupos omitidos se inactivan sin borrar documentos |
| GET | `/api/reporting/summary` | KPI mensuales y del periodo para `client_id`, `start_date`, `end_date`; `refresh=true` omite la cache de datos |
| GET | `/api/reports/performance.xlsx` | Genera el XLSX para `start_date` y `end_date` |

Parámetros de rendimiento: `start_date`, `end_date` (YYYY-MM-DD), `comparison`
(`none` · `previous_period` · `previous_month` · `previous_year` · `custom` con
`compare_start_date`/`compare_end_date`), `refresh=true` para saltar la cache.
Los endpoints por plataforma reciben `client_id` (nunca un account_id libre): la
cuenta sale del registro, así no se mezclan datos entre clientes.

Los grupos son una capa adicional sobre campañas ya incluidas por las
asignaciones actuales. `reporting_group_id: null` es la representación canónica
para campañas y presupuestos generales/sin grupo; `GENERAL` solo se usa como
etiqueta de presentación. Presupuestos sin grupo conservan el ID documental
legacy `{client_id}__{platform}__{YYYY-MM}`; los específicos usan
`{client_id}__{reporting_group_id}__{platform}__{YYYY-MM}`. Las agrupaciones no
se infieren por objetivo ni nombre: el cliente debe asignarlas manualmente.

## Diseño

- `app/registry.py` — registro central (JSON en `data/clients.json`). Interfaz
  `ClientRepository` lista para migrar a base de datos. Una cuenta no puede
  asignarse a dos clientes (HTTP 409).
- `app/cache.py` — cache TTL (5 min) con clave `(client_id, platform, account_id, start, end)`.
- `app/service.py` — cada plataforma se consulta en paralelo y aislada: si una
  falla, responde `status: error` + "X temporalmente no disponible" y el resto sigue.
- `app/budgets.py` — presupuestos mensuales por cliente, plataforma, año y mes;
  usa una colección Firestore separada y una clave de documento determinista.
- `app/reporting.py` — exportador XLSX. Reutiliza `DashboardService.platform_block`
  y las asignaciones del registro; también expone el resumen mensual del dashboard
  desde el mismo dataset normalizado. No consulta directamente ninguna API.
- `app/providers/` — Google Ads (GAQL), Meta (Graph API insights), Spotify Ads v3 y GA4 (Data API).
- ROAS = conversion_value / cost, entregado como número (la UI lo muestra `2.44x`).
- Meta: compras = primer tipo disponible entre `omni_purchase`, `purchase`,
  `offsite_conversion.fb_pixel_purchase`… (sin sumar duplicados).

## Seguridad

Los endpoints de escritura (`POST/PUT /api/clients`) no tienen autenticación
propia. En local el backend escucha solo en `127.0.0.1`; en Cloud Run despliega
con `--no-allow-unauthenticated` y da acceso a tu equipo con IAP (o un load
balancer con IAP). No lo publiques con `--allow-unauthenticated`.

## Cloud Run

Una sola imagen (`/Dockerfile` en la raíz): etapa Node compila `dashboard/dist`,
etapa Python instala `requirements.txt` y arranca uvicorn en `0.0.0.0:$PORT`.
FastAPI sirve `/health`, `/api/*` y cualquier otra ruta devuelve el dashboard
(`app/spa.py`). Una ruta `/api/...` inexistente responde 404 JSON, nunca HTML.

Variables en Cloud Run (nunca en archivos):

| Variable | Origen |
|---|---|
| `META_ACCESS_TOKEN` | Secret Manager → `--set-secrets` |
| `GOOGLE_ADS_DEVELOPER_TOKEN` (opcional) | Secret Manager → `--set-secrets` |
| `SPOTIFY_CLIENT_ID` | Secret Manager → `--set-secrets` |
| `SPOTIFY_CLIENT_SECRET` | Secret Manager → `--set-secrets` |
| `SPOTIFY_REFRESH_TOKEN` | Secret Manager → `--set-secrets` |
| `MP_STORE` | `firestore` en producción; `file` para desarrollo |
| `MP_FIRESTORE_COLLECTION` | colección del registro (por defecto `clients`) |
| `MP_BUDGETS_FIRESTORE_COLLECTION` | colección de presupuestos mensuales (por defecto `performance_budgets`) |
| `MP_REPORTING_GROUPS_FIRESTORE_COLLECTION` | configuración de grupos y asignaciones (por defecto `performance_reporting_groups`) |
| `MP_FIRESTORE_PROJECT` | opcional; por defecto el proyecto de Cloud Run |
| `MP_FIRESTORE_DATABASE` | opcional; por defecto `(default)` |
| `MP_CLIENTS_FILE` | solo modo `file`: ruta del registro mutable |
| `MP_CLIENTS_SEED_FILE` | seed inicial; nunca reemplaza un store con datos |
| `MP_CORS_ORIGINS` | solo si otro origen consume la API |
| `GOOGLE_ADS_MANAGER_CUSTOMER_ID` | opcional: MCC raíz (por defecto `7954113535`) |

Google Ads y GA4 usan la **cuenta de servicio del servicio de Cloud Run** como ADC:
basta con agregar su email como usuario **de solo lectura en el MCC raíz**
(la jerarquía `customer_client` se hereda) y en las propiedades de GA4; las credenciales de usuario de este
servidor no viajan en la imagen.

El disco del contenedor es efímero y cada instancia tiene el suyo. El valor
predeterminado `MP_STORE=file` sirve para desarrollo, pero **no es persistencia
de producción** si `MP_CLIENTS_FILE` queda dentro de la imagen.

Los presupuestos son deliberadamente más estrictos: solo se habilitan con
`MP_STORE=firestore` (o con el repositorio de memoria inyectado por tests). No
se escriben en `clients.json`, otro JSON local ni `localStorage`; si el backend
está en modo `file`, `/api/budgets` y `/api/reports/performance.xlsx` responden
503 hasta que exista persistencia durable.

## Presupuestos y exportación XLSX

La clave lógica del presupuesto es `(client_id, platform, year, month)`. El
`PUT` solo admite `{ "budget": número }`; cliente, plataforma, moneda y cuentas
se obtienen del registro actual. La plataforma se rechaza si el cliente no tiene
una cuenta asignada, y el ID determinista de Firestore convierte cada escritura
en un *upsert* sin duplicados.

El exportador divide el intervalo por meses y consulta cada segmento mediante
la misma capa que alimenta el dashboard. Cada mes conserva su presupuesto
completo, mientras que el gasto esperado usa los días del segmento seleccionado
y la duración real del mes. Las hojas generadas son `REPORTE SEMANAL`,
`PRESUPUESTOS` y `DATOS`; esta última queda oculta. Solo las celdas de importe
en `PRESUPUESTOS` son editables y las fórmulas relacionadas apuntan a ellas.

Los umbrales de pacing están en `app/reporting.py`: verde hasta 10 % de
desviación absoluta, amarillo hasta 20 %, rojo por encima de 20 % y gris sin
presupuesto. El archivo de referencia visual no está incluido en este
repositorio, por lo que la implementación sigue los requisitos funcionales y
de formato documentados, sin afirmar una reproducción exacta de estilos no
inspeccionables.

Producción recomendada: Firestore
----------------------------------

El código ya incluye `FirestoreClientStore`. `clients.json` queda como seed y
solo se importa cuando la colección está vacía; una revisión nueva nunca pisa
datos existentes. Para habilitarlo antes del siguiente despliegue (requiere
autorización del propietario del proyecto):

1. Habilitar la API: `gcloud services enable firestore.googleapis.com --project=PROJECT_ID`.
2. Si el proyecto aún no tiene base, crear una Native database en la misma
   región elegida para los datos: `gcloud firestore databases create --location=REGION --project=PROJECT_ID`.
3. Conceder `roles/datastore.user` a la cuenta de servicio que ejecuta Cloud Run.
4. Añadir `MP_STORE=firestore` al servicio. Opcionalmente fijar
   `MP_FIRESTORE_PROJECT`, `MP_FIRESTORE_DATABASE` y
   `MP_FIRESTORE_COLLECTION`; no se necesitan tokens nuevos.
5. Antes de cambiar el tráfico, importar una sola vez la configuración vigente
   en la colección o arrancar con la colección vacía para que se inicialice
   desde el `clients.json` incluido en esa imagen. Verificar primero que ese
   seed contiene las asignaciones que deben conservarse.

Alternativa: montar un bucket y apuntar `MP_CLIENTS_FILE` ahí. En ese caso usa
`--max-instances=1` para evitar escrituras concurrentes. La cache permanece en
memoria, por instancia.

**Verificar sin desplegar nada nuevo**: `GET /api/health` incluye
`persistence: { backend, durable, warning }`. Si `durable` es `false`, esa
revisión sigue escribiendo en el archivo horneado en la imagen — es la misma
condición que `_ephemeral_storage_risk()` registra como **error** (no
warning) en los logs de Cloud Run, buscando por `K_SERVICE`, para que la
causa quede inequívoca ahí en vez de una advertencia genérica.

# Presupuestos y exportación Excel — validación del 5 de octubre de 2026

Se revisó y corrigió el proyecto existente en `/srv/marketing-performance-cloud`, VM `marketing-dev`. Las dos funcionalidades ya existían en esta versión del código. Se conservaron y completaron mediante correcciones acotadas. No se desplegó, no se hizo commit ni push, no se crearon integraciones ni infraestructura y no se cambiaron credenciales, autenticación, clientes, cuentas o asignaciones.

## Archivos modificados en el servidor

- `backend/app/budgets.py`: los modelos de captura y persistencia rechazan NaN e infinito, además de valores negativos.
- `backend/app/reporting.py`: diferencia contra presupuesto y porcentaje consumido usan el presupuesto mensual completo; pacing conserva el prorrateo. Se corrigió el gasto esperado en subtotales mensuales. Las fórmulas distinguen presupuesto cero del ausente mediante `ISNUMBER`. Las métricas ocultas del cliente se respetan en el reporte. La clave de campaña incluye cuenta para evitar falsas colisiones; los clientes sin grupos conservan sus totales originales cuando otro cliente sí tiene grupos. Se amplió la columna de presupuesto para mostrar completo el estado ausente.
- `backend/tests/test_reporting.py`: actualizadas las expectativas del presupuesto mensual; agregadas regresiones para presupuestos inválidos, métricas ocultas, periodos parciales, cuentas con el mismo ID de campaña y mezcla de clientes agrupados y no agrupados.

Respaldo previo: `.backups/budget-excel-20261005/`. No se añadieron módulos nuevos al proyecto. Se generaron archivos compilados del frontend y cachés de prueba durante la validación.

La comparación SHA-256 contra la copia inicial confirmó que estos son los únicos tres archivos de código modificados. Los proveedores, `DashboardService`, endpoints existentes, modelos de cliente, configuración y fuentes de frontend permanecen iguales. El índice Git ya presentaba numerosas eliminaciones staged y los mismos directorios como untracked antes de empezar; este estado no se corrigió ni se usó para un commit.

## Persistencia existente

Cloud Run tiene `MP_STORE=firestore`, proyecto `marketing-performance-prod`, base `(default)`, colección de clientes `clients` y colección de presupuestos `performance_budgets`. Esta configuración se comprobó con una lectura del servicio. No se crearon bases, permisos IAM ni servicios.

`FirestoreBudgetRepository` guarda un documento por clave lógica con `set` (upsert). Para presupuesto general, el ID es `client_id__platform__YYYY-MM`. Modelo: `client_id`, `platform`, `year`, `month`, `budget` numérico no negativo y finito, `currency` de tres letras y `reporting_group_id` opcional. El esquema existente admite presupuestos por grupos y se preservó; los documentos generales mantienen los IDs legacy. La moneda se obtiene del cliente en el endpoint, no es un nuevo valor manual.

El modo memory es para pruebas. El modo file no degrada a disco efímero: devuelve un error de persistencia. No depende de localStorage. Se validó recreación del repositorio y upsert con Firestore simulado; no se escribieron presupuestos productivos ni se reinició/desplegó producción para demostrar persistencia real.

## Endpoints, servicios y componentes reutilizados

- Presupuestos: `GET /api/budgets`, `PUT /api/budgets/{client_id}/{platform}/{year}/{month}` y eliminación existente. El cuerpo de captura acepta únicamente `budget` y rechaza campos adicionales.
- Exportación: `GET /api/reports/performance.xlsx?start_date=YYYY-MM-DD&end_date=YYYY-MM-DD`. El periodo es el único parámetro de exportación. Incluye clientes activos y sus fuentes asignadas.
- Servicio: `generate_performance_report`, `collect_report_lines`, `_build_workbook` en `backend/app/reporting.py`.
- Frontend existente: `ReportingActions.tsx`, `BudgetManager.tsx`, `ExportReportModal.tsx`, `api/backend.ts`. El botón Excel abre primero el selector Desde/Hasta con Cancelar/Generar.
- Datos: el mismo `DashboardService.platform_block` que utiliza el dashboard, con su caché, normalización, filtros, exclusiones y `exclusive_claims`. No se añadió una capa paralela ni llamadas directas a proveedores desde el exportador. Para un periodo no cacheado, la capa común puede consultar las integraciones existentes de la misma manera que el dashboard.

El archivo contiene `REPORTE SEMANAL`, `PRESUPUESTOS`, `DATOS` y la hoja adicional existente `CONFIGURACION`. DATOS queda oculta. Los presupuestos son números con formato monetario y celdas desbloqueadas; las demás hojas están protegidas. Cambiar el presupuesto en Excel no guarda ese cambio en Firestore ni afecta al dashboard.

## Métricas directas y calculadas

Directas desde la capa común: gasto, conversiones, valor de conversión, clics, impresiones y alcance cuando la plataforma los proporciona. Cuenta, campaña, cliente y plataforma proceden de las asignaciones existentes. Leads se obtienen de conversiones de campañas con objetivo de leads mediante la lógica de reporting ya existente; no es una consulta adicional ni un dato inventado. Si el objetivo necesario falta, el resultado queda vacío.

Calculadas desde bases: diferencia, gasto esperado, pacing, consumo, promedio diario, proyección, CPA, CPL, ROAS, CPC, CTR, ticket promedio y frecuencia. No se suman ni promedian ratios. Las métricas ausentes permanecen vacías; totales incompletos no se presentan como completos. No se suman importes de monedas incompatibles. Alcance/frecuencia agregados quedan vacíos cuando no puede garantizarse deduplicación. El XLSX conserva la base original en DATOS, igual que el payload del dashboard, y deja vacías en REPORTE SEMANAL las familias ocultas del cliente.

El reporte publicitario usa Google Ads, Meta Ads y Spotify Ads asignados. WooSync/WooCommerce y GA4 siguen disponibles en sus secciones existentes y no se mezclan artificialmente con conversiones publicitarias ni se incorporan nuevas consultas para Excel.

## Fórmulas y periodos

Sea B el presupuesto mensual, G el gasto del tramo, D los días del mes y d los días incluidos en ese tramo:

- Diferencia contra presupuesto: `B - G`.
- Presupuesto aplicable y gasto esperado: `B × d / D`.
- Diferencia de pacing: `gasto esperado - G`.
- Consumo del presupuesto mensual: `G / B`.
- Promedio diario: conversiones / días incluidos.
- Proyección mensual: promedio diario × días del mes.
- CPA = gasto / conversiones; CPL = gasto / leads; CPC = gasto / clics.
- CTR = clics / impresiones; ROAS = valor de conversión / gasto; ticket = valor / conversiones; frecuencia = impresiones / alcance.

Las fórmulas enlazan a las celdas numéricas de PRESUPUESTOS. Se generan desde la misma definición matemática que el resumen del dashboard. Usan verificaciones numéricas e `IFERROR(..., "")` para divisiones no disponibles. Un presupuesto ausente muestra “Presupuesto no capturado”; cero es una captura válida, con consumo vacío para evitar división por cero.

Se divide el intervalo en tramos mensuales inclusivos usando `calendar.monthrange`, incluidos años bisiestos. Cada mes utiliza su propio presupuesto completo. Para septiembre 15–30 se usan 16 días de gasto y pacing: 30,000 × 16 / 30 = 16,000. No se usa el día actual ni se compara gasto de un tramo con pacing de días anteriores al tramo. Para varios meses, el resumen suma bases y los esperados prorrateados de cada mes. La proyección a fin de mes se presenta por mes y queda vacía en el total multimes.

Pacing visual: desviación absoluta sobre gasto esperado ≤10% verde, >10% y ≤20% amarillo, >20% rojo; presupuesto ausente gris. Umbrales configurables en `PACING_GREEN_MAX` y `PACING_YELLOW_MAX`.

## Validación

- Backend final: **301 passed**, una advertencia de deprecación existente de Starlette/httpx.
- Frontend: **112 passed**, 9 archivos de pruebas.
- Typecheck: correcto.
- Build: correcto.
- Lint: no se encontró script ni configuración de lint en los archivos inspeccionados; no se instaló uno.
- XLSX real generado por el endpoint con proveedores de prueba existentes, para 2026-08-15 → 2026-09-15. La capa común recibió agosto 15–31 y septiembre 1–15 para cada cuenta correcta.
- Recálculo independiente con Artifact Tool: sin coincidencias de `#REF!`, `#DIV/0!`, `#VALUE!`, `#N/A`, `#NAME?` o `#NUM!`. Verificados presupuesto 30,000 → 60,000, cero y ausente; gasto original inalterado; dependencias y subtotales de pacing correctos. Se revisaron visualmente las áreas de reporte y presupuestos.
- Aislamiento: pruebas de shared/exclusive, asignaciones parciales, presupuestos por clave, grupos por mes, cuentas con ID de campaña coincidente y clientes agrupados/no agrupados. Pruebas de hidden_metrics, exclusiones, Spotify y WooSync incluidas en las suites existentes.
- La verificación fue con fixtures y repositorios simulados; no demuestra disponibilidad/permisos actuales de todas las APIs productivas. No se hizo una exportación con datos reales ni se modificaron presupuestos reales.

## Estado y pendientes antes de despliegue

Producción continúa en `marketing-performance-00032-nap` al 100% según la lectura de Cloud Run. Estos cambios están solo en el código de la VM; no se desplegaron, no se promovió tráfico y no se hizo commit/push.

No se identificó necesidad de infraestructura o IAM. Antes de un eventual despliegue, revisar el estado Git preexistente y guardar los cambios en una versión separada del respaldo de producción. Corresponde entonces validar los datos reales y persistencia en un entorno de prueba existente, conservando fuentes y permisos actuales. El recálculo se verificó con Artifact Tool; no se abrió el archivo en Microsoft Excel para una prueba nativa.

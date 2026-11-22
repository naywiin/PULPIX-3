# syntax=docker/dockerfile:1
# Imagen única para Cloud Run: FastAPI sirve /api/* y el dashboard React compilado.

# ---------- Etapa 1: build del frontend ----------
FROM node:24-slim AS frontend
WORKDIR /app/dashboard
COPY dashboard/package.json dashboard/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY dashboard/ ./
# typecheck + build (tsc -b && vite build). /api es relativo: no hay host fijo.
RUN npm run build

# ---------- Etapa 2: runtime Python ----------
FROM python:3.13-slim AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PORT=8080 \
    MP_STATIC_DIR=/app/dashboard/dist

WORKDIR /app/backend
COPY backend/requirements.txt ./
RUN pip install -r requirements.txt

COPY backend/app ./app
# Registro inicial de clientes (solo IDs de cuentas, sin secretos).
COPY backend/data/clients.json ./data/clients.json
COPY --from=frontend /app/dashboard/dist /app/dashboard/dist

# Usuario sin privilegios; data/ escribible por si el registro se usa en disco local.
RUN useradd --uid 10001 --no-create-home --shell /usr/sbin/nologin app \
    && chown -R app:app /app/backend/data
USER app

EXPOSE 8080
# Cloud Run inyecta PORT. Una sola instancia de uvicorn por contenedor: Cloud Run escala con instancias.
CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8080} --proxy-headers --forwarded-allow-ips='*' --no-server-header"]

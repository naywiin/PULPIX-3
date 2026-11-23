# syntax=docker/dockerfile:1
# Imagen única para Cloud Run: FastAPI sirve /api/* y el dashboard React compilado.

# ---------- Etapa 1: build del frontend ----------
FROM node:24-slim AS frontend
WORKDIR /app/dashboard
COPY dashboard/package.json ./
RUN npm install --no-audit --no-fund
COPY dashboard/ ./

ARG VITE_FIREBASE_API_KEY=""
ARG VITE_FIREBASE_AUTH_DOMAIN=""
ARG VITE_FIREBASE_PROJECT_ID=""
ARG VITE_FIREBASE_APP_ID=""
ENV VITE_FIREBASE_API_KEY=$VITE_FIREBASE_API_KEY \
    VITE_FIREBASE_AUTH_DOMAIN=$VITE_FIREBASE_AUTH_DOMAIN \
    VITE_FIREBASE_PROJECT_ID=$VITE_FIREBASE_PROJECT_ID \
    VITE_FIREBASE_APP_ID=$VITE_FIREBASE_APP_ID

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
COPY backend/data/clients.json ./data/clients.json
COPY --from=frontend /app/dashboard/dist /app/dashboard/dist

RUN useradd --uid 10001 --no-create-home --shell /usr/sbin/nologin app \
    && chown -R app:app /app/backend/data
USER app

EXPOSE 8080
CMD ["sh", "-c", "exec uvicorn app.pulpix_app:app --host 0.0.0.0 --port ${PORT:-8080} --proxy-headers --forwarded-allow-ips='*' --no-server-header"]

"""Sirve el dashboard compilado (dashboard/dist) como SPA.

- Archivos existentes (JS/CSS/imagenes) se sirven tal cual; /assets/* con cache
  larga porque Vite les pone hash en el nombre.
- Cualquier otra ruta devuelve index.html (sin cache) para que React resuelva.
- Las rutas /api/* NUNCA caen al frontend: si no existen responden 404 JSON.
"""
from __future__ import annotations

from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.responses import JSONResponse, Response
from starlette.staticfiles import StaticFiles
from starlette.types import Scope

API_PREFIXES = ("api/", "api")


class SPAStaticFiles(StaticFiles):
    async def get_response(self, path: str, scope: Scope) -> Response:
        if path in API_PREFIXES or path.startswith(API_PREFIXES[0]):
            return JSONResponse({"detail": "Not Found"}, status_code=404)
        try:
            response = await super().get_response(path, scope)
        except StarletteHTTPException as exc:
            if exc.status_code != 404:
                raise
            response = await super().get_response("index.html", scope)
        if path.startswith("assets/"):
            response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        elif response.media_type == "text/html" or path in ("", ".", "index.html"):
            response.headers["Cache-Control"] = "no-cache"
        return response

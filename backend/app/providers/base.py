from __future__ import annotations

import re


class ProviderError(Exception):
    """Fallo al consultar una plataforma. El mensaje es seguro para mostrarse."""


class ProviderNotConfigured(ProviderError):
    """Faltan credenciales en el servidor para la plataforma."""


_SECRET_PATTERNS = [
    re.compile(r"(access_token=)[^&\s]+", re.I),
    re.compile(r"((?:access_token|refresh_token|client_secret|api[_-]?key)\s*[:=]\s*['\"]?)[^'\"&,\s}]+", re.I),
    re.compile(r"(Authorization['\"]?\s*[:=]\s*['\"]?)(?:Bearer\s+)?[^'\"\s,}]+", re.I),
    re.compile(r"(Bearer\s+)[A-Za-z0-9._\-]+", re.I),
    re.compile(r"(developer[-_]token['\"]?\s*[:=]\s*['\"]?)[^'\"\s,}]+", re.I),
    re.compile(r"\bEAA[A-Za-z0-9]{20,}"),  # formato de tokens de Meta
    re.compile(r"\bya29\.[A-Za-z0-9._\-]+"),  # tokens OAuth de Google
]


def sanitize(message: object, limit: int = 300) -> str:
    """Elimina cualquier cosa con forma de token antes de loguear o responder."""
    text = str(message)
    for pat in _SECRET_PATTERNS:
        text = pat.sub(lambda m: (m.group(1) if m.groups() else "") + "[redacted]", text)
    text = " ".join(text.split())
    return text[:limit]

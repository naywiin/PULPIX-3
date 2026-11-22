"""Rangos de fechas y periodos de comparación.

Misma lógica que dashboard/src/lib/dates.ts para que frontend y backend
calculen idénticos rangos (1–24 sep 2026 + año anterior → 1–24 sep 2025).
"""
from __future__ import annotations

import calendar
import re
from datetime import date, timedelta

COMPARISONS = ("none", "previous_period", "previous_month", "previous_year", "custom")
_ISO = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def parse_date(value: str) -> date:
    """Valida estrictamente YYYY-MM-DD (se interpola en GAQL, no aceptar otra cosa)."""
    if not isinstance(value, str) or not _ISO.match(value):
        raise ValueError(f"Fecha inválida: {value!r}. Usa YYYY-MM-DD.")
    return date.fromisoformat(value)


def add_months(d: date, months: int) -> date:
    total = d.year * 12 + (d.month - 1) + months
    y, m = divmod(total, 12)
    m += 1
    return date(y, m, min(d.day, calendar.monthrange(y, m)[1]))


def compare_range(
    comparison: str,
    start: date,
    end: date,
    custom_start: date | None = None,
    custom_end: date | None = None,
) -> tuple[date, date] | None:
    if comparison == "none":
        return None
    if comparison == "custom":
        if not custom_start or not custom_end:
            raise ValueError("La comparación personalizada requiere compare_start_date y compare_end_date.")
        return (custom_start, custom_end) if custom_start <= custom_end else (custom_end, custom_start)
    if comparison == "previous_period":
        length = (end - start).days + 1
        prev_end = start - timedelta(days=1)
        return prev_end - timedelta(days=length - 1), prev_end
    if comparison == "previous_month":
        last_day = calendar.monthrange(end.year, end.month)[1]
        if start.day == 1 and end.day == last_day and (start.year, start.month) == (end.year, end.month):
            prev = add_months(start, -1)
            return prev, date(prev.year, prev.month, calendar.monthrange(prev.year, prev.month)[1])
        return add_months(start, -1), add_months(end, -1)
    if comparison == "previous_year":
        return add_months(start, -12), add_months(end, -12)
    raise ValueError(f"Comparación desconocida: {comparison!r}. Opciones: {', '.join(COMPARISONS)}")

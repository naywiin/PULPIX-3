"""Canonical client ordering for workbook exports."""
from __future__ import annotations

from .models import Client, REPORT_CATEGORIES

CATEGORY_ORDER = {category: index for index, category in enumerate(REPORT_CATEGORIES)}


def client_report_category(client: Client) -> str:
    """Use persisted report category; legacy clients fall back without writes."""
    value = client.report_category
    return value if value in CATEGORY_ORDER else "GENERAL"


def client_report_sort_key(client: Client) -> tuple[int, int, int, int, str]:
    """Active first, then fixed category order, configured position, name.

    Unpositioned clients sort after positioned clients in their category.
    """
    position = client.report_position
    return (
        0 if client.active else 1,
        CATEGORY_ORDER[client_report_category(client)],
        1 if position is None else 0,
        position if position is not None else 0,
        client.name.casefold(),
    )

from __future__ import annotations

from .history_record import HistoryRecord
from .search_service import HistorySearchService
from .service_registry import get_history_search_service

try:  # AstrBot runtime provides these heavy dependencies
    from .plugin import HistoryIndexer  # pragma: no cover
except ModuleNotFoundError:  # pragma: no cover - happens when AstrBot isn't installed for tests
    HistoryIndexer = None  # type: ignore[assignment]

__all__ = [
    "HistoryIndexer",
    "HistoryRecord",
    "HistorySearchService",
    "get_history_search_service",
]

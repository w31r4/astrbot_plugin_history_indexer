from __future__ import annotations

from astrbot.core.star import star_map

from .astrbot_plugin_history_indexer.plugin import HistoryIndexer
from .astrbot_plugin_history_indexer.search_service import (
    HistoryRecord,
    HistorySearchService,
)
from .astrbot_plugin_history_indexer.service_registry import (
    get_history_search_service,
)

# Ensure the plugin metadata uses this module path so the loader can find it.
_metadata = star_map.get(HistoryIndexer.__module__)
if _metadata:
    star_map.pop(HistoryIndexer.__module__, None)
    star_map[__name__] = _metadata
    _metadata.module_path = __name__

__all__ = [
    "HistoryIndexer",
    "HistoryRecord",
    "HistorySearchService",
    "get_history_search_service",
]

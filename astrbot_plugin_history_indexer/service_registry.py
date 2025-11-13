from __future__ import annotations

from .search_service import HistorySearchService

_HISTORY_SERVICE: HistorySearchService | None = None


def get_history_search_service() -> HistorySearchService | None:
    """
    获取全局的历史消息检索服务单例。
    """
    return _HISTORY_SERVICE


def set_history_search_service(service: HistorySearchService | None):
    global _HISTORY_SERVICE
    _HISTORY_SERVICE = service


__all__ = ["get_history_search_service", "set_history_search_service"]

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from typing import Any, Sequence

from .executor import run_blocking
from .history_record import HistoryRecord

try:
    from thefuzz import fuzz
except ModuleNotFoundError:  # pragma: no cover - exercised only when dependency missing
    from difflib import SequenceMatcher

    def _sequence_ratio(a: str, b: str) -> float:
        if not a or not b:
            return 0.0
        return SequenceMatcher(None, a, b).ratio()

    def _simple_partial_ratio(a: str, b: str) -> int:
        """
        Approximate `thefuzz.partial_ratio` using only stdlib tools.
        """
        if not a or not b:
            return 0

        a = a.lower()
        b = b.lower()
        shorter, longer = (a, b) if len(a) <= len(b) else (b, a)
        best = _sequence_ratio(a, b)

        len_diff = len(longer) - len(shorter)
        if len_diff >= 1:
            for idx in range(len_diff + 1):
                window = longer[idx : idx + len(shorter)]
                best = max(best, _sequence_ratio(shorter, window))
                if best >= 0.99:
                    break

        return int(round(best * 100))

    class _FallbackFuzz:
        @staticmethod
        def partial_ratio(a: str, b: str) -> int:
            return _simple_partial_ratio(a, b)

    fuzz = _FallbackFuzz()


class HistorySearchService:
    """
    一个可重用的历史消息检索服务，供其他插件导入和使用。

    这个服务提供了多种维度的消息检索能力，例如按关键词、会话、平台或发送者进行查询。
    """

    def __init__(self, db_path: str):
        self.db_path = db_path

    def _get_conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=10)
        conn.row_factory = sqlite3.Row
        return conn

    @staticmethod
    def _normalize_collection(values: Sequence[str] | None) -> list[str] | None:
        if values is None:
            return None
        if isinstance(values, str):
            return [values]
        return [value for value in values if value]

    def _row_to_record(self, row: sqlite3.Row) -> HistoryRecord:
        ts = datetime.fromtimestamp(row["created_at"], tz=timezone.utc)
        return HistoryRecord(
            session_id=row["session_id"],
            platform_id=row["platform_id"],
            sender_id=row["sender_id"],
            sender_name=row["sender_name"],
            message_text=row["message_text"],
            message_outline=row["message_outline"],
            created_at=ts,
        )

    async def search(
        self,
        keyword: str,
        *,
        sessions: Sequence[str] | None = None,
        platforms: Sequence[str] | None = None,
        senders: Sequence[str] | None = None,
        limit: int = 20,
        include_outline: bool = True,
        fuzzy_threshold: int = 70,
    ) -> list[HistoryRecord]:
        """
        通用的多维度消息检索接口，支持模糊匹配。
        """
        keyword = (keyword or "").strip()
        if not keyword:
            return []

        limit = max(1, min(200, limit))
        norm_sessions = self._normalize_collection(sessions)
        norm_platforms = self._normalize_collection(platforms)
        norm_senders = self._normalize_collection(senders)

        def _query_and_filter():
            clauses: list[str] = []
            params: list[Any] = []

            simple_pattern = f"%{keyword[0]}%" if keyword else "%"
            if include_outline:
                clauses.append("(message_text LIKE ? OR message_outline LIKE ?)")
                params.extend([simple_pattern, simple_pattern])
            else:
                clauses.append("message_text LIKE ?")
                params.append(simple_pattern)

            if norm_sessions:
                placeholders = ",".join("?" for _ in norm_sessions)
                clauses.append(f"session_id IN ({placeholders})")
                params.extend(norm_sessions)
            if norm_platforms:
                placeholders = ",".join("?" for _ in norm_platforms)
                clauses.append(f"platform_id IN ({placeholders})")
                params.extend(norm_platforms)
            if norm_senders:
                placeholders = ",".join("?" for _ in norm_senders)
                clauses.append(f"sender_id IN ({placeholders})")
                params.extend(norm_senders)

            query = (
                "SELECT session_id, platform_id, sender_id, sender_name, "
                "message_text, message_outline, created_at "
                "FROM messages"
            )
            if clauses:
                query += " WHERE " + " AND ".join(clauses)
            query += " ORDER BY created_at DESC LIMIT 200"

            candidate_records: list[HistoryRecord]
            with self._get_conn() as conn:
                cursor = conn.execute(query, params)
                candidate_records = [self._row_to_record(row) for row in cursor.fetchall()]

            scored_records = []
            for record in candidate_records:
                text_to_match = record.message_text
                score = fuzz.partial_ratio(keyword, text_to_match)
                if include_outline and record.message_outline:
                    outline_score = fuzz.partial_ratio(keyword, record.message_outline)
                    score = max(score, outline_score)

                if score >= fuzzy_threshold:
                    scored_records.append({"record": record, "score": score})

            scored_records.sort(key=lambda x: (x["score"], x["record"].created_at), reverse=True)
            return [item["record"] for item in scored_records[:limit]]

        return await run_blocking(_query_and_filter)

    async def search_by_session(
        self,
        session_id: str,
        keyword: str,
        limit: int = 20,
        fuzzy_threshold: int = 70,
    ) -> list[HistoryRecord]:
        """按单个会话 ID 检索消息。"""
        return await self.search(keyword, sessions=[session_id], limit=limit, fuzzy_threshold=fuzzy_threshold)

    async def search_by_platform(
        self,
        platform_ids: Sequence[str],
        keyword: str,
        limit: int = 20,
        fuzzy_threshold: int = 70,
    ) -> list[HistoryRecord]:
        """按一个或多个平台 ID 检索消息。"""
        return await self.search(keyword, platforms=platform_ids, limit=limit, fuzzy_threshold=fuzzy_threshold)

    async def search_across_sessions(
        self,
        session_ids: Sequence[str],
        keyword: str,
        limit: int = 20,
        fuzzy_threshold: int = 70,
    ) -> list[HistoryRecord]:
        """跨多个指定会话 ID 进行检索。"""
        return await self.search(keyword, sessions=session_ids, limit=limit, fuzzy_threshold=fuzzy_threshold)

    async def search_by_sender(
        self,
        sender_id: str,
        keyword: str,
        *,
        platform_id: str | None = None,
        limit: int = 20,
        fuzzy_threshold: int = 70,
    ) -> list[HistoryRecord]:
        """按发送者 ID 检索消息。"""
        platforms = [platform_id] if platform_id else None
        return await self.search(
            keyword,
            senders=[sender_id],
            platforms=platforms,
            limit=limit,
            fuzzy_threshold=fuzzy_threshold,
        )

    async def search_global(self, keyword: str, limit: int = 20, fuzzy_threshold: int = 70) -> list[HistoryRecord]:
        """进行全局检索，不受任何会话、平台或发送者限制。"""
        return await self.search(keyword, limit=limit, fuzzy_threshold=fuzzy_threshold)


__all__ = ["HistorySearchService"]

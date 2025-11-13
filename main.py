from __future__ import annotations

import asyncio
import os
import sqlite3
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, cast

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star, register
from astrbot.core.utils.astrbot_path import get_astrbot_data_path

if TYPE_CHECKING:
    from astrbot.core.star.register.star_handler import RegisteringCommandable


@dataclass(slots=True)
class HistoryRecord:
    """
    代表一条历史消息记录的结构化数据。

    Attributes:
        session_id: 消息所在会话的统一标识符。
        platform_id: 消息来源的平台标识符 (e.g., "qq", "discord")。
        sender_id: 发送者的唯一标识符。
        sender_name: 发送者的显示昵称。
        message_text: 消息的纯文本内容。
        message_outline: 消息的摘要或概览，尤其适用于非纯文本消息。
        created_at: 消息创建的时间戳 (UTC)。
    """

    session_id: str
    platform_id: str
    sender_id: str
    sender_name: str
    message_text: str
    message_outline: str
    created_at: datetime

    def format_line(self) -> str:
        """将记录格式化为单行可读字符串。"""
        local_ts = self.created_at.astimezone().strftime("%m-%d %H:%M")
        return f"{local_ts} {self.sender_name}: {self.message_text}"


class HistorySearchService:
    """
    一个可重用的历史消息检索服务，供其他插件导入和使用。

    这个服务提供了多种维度的消息检索能力，例如按关键词、会话、平台或发送者进行查询。
    其他插件不应直接实例化此类，而应通过 `get_history_search_service()` 函数
    获取由本插件维护的全局单例。

    Example:
        ```python
        from astrbot_plugin_history_indexer.main import get_history_search_service

        history_service = get_history_search_service()
        if history_service:
            # 异步搜索当前会话中包含 "hello" 的最近 10 条消息
            records = await history_service.search_by_session(
                current_session_id, "hello", limit=10
            )
            for record in records:
                print(record.format_line())
        ```
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
    ) -> list[HistoryRecord]:
        """
        通用的多维度消息检索接口。

        Args:
            keyword: 用于在消息文本和摘要中搜索的关键词。如果为空，则不进行关键词过滤。
            sessions: 限定在一个或多个会话 ID 范围内进行搜索。
            platforms: 限定在一个或多个平台 ID 范围内进行搜索。
            senders: 限定在一个或多个发送者 ID 范围内进行搜索。
            limit: 返回结果的最大数量，限制在 1 到 200 之间。
            include_outline: 是否同时搜索消息的摘要字段 (`message_outline`)。

        Returns:
            一个 `HistoryRecord` 对象列表，按时间倒序排列。
        """
        keyword = keyword or ""
        limit = max(1, min(200, limit))
        norm_sessions = self._normalize_collection(sessions)
        norm_platforms = self._normalize_collection(platforms)
        norm_senders = self._normalize_collection(senders)

        def _query():
            clauses: list[str] = []
            params: list[Any] = []
            if keyword:
                pattern = f"%{keyword}%"
                if include_outline:
                    clauses.append("(message_text LIKE ? OR message_outline LIKE ?)")
                    params.extend([pattern, pattern])
                else:
                    clauses.append("message_text LIKE ?")
                    params.append(pattern)
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
            query += " ORDER BY created_at DESC LIMIT ?"
            params.append(limit)

            with self._get_conn() as conn:
                cursor = conn.execute(query, params)
                return [self._row_to_record(row) for row in cursor.fetchall()]

        records = await asyncio.to_thread(_query)
        return records

    async def search_by_session(
        self,
        session_id: str,
        keyword: str,
        limit: int = 20,
    ) -> list[HistoryRecord]:
        """按单个会话 ID 检索消息。"""
        return await self.search(keyword, sessions=[session_id], limit=limit)

    async def search_by_platform(
        self,
        platform_ids: Sequence[str],
        keyword: str,
        limit: int = 20,
    ) -> list[HistoryRecord]:
        """按一个或多个平台 ID 检索消息。"""
        return await self.search(keyword, platforms=platform_ids, limit=limit)

    async def search_across_sessions(
        self,
        session_ids: Sequence[str],
        keyword: str,
        limit: int = 20,
    ) -> list[HistoryRecord]:
        """跨多个指定会话 ID 进行检索。"""
        return await self.search(keyword, sessions=session_ids, limit=limit)

    async def search_by_sender(
        self,
        sender_id: str,
        keyword: str,
        *,
        platform_id: str | None = None,
        limit: int = 20,
    ) -> list[HistoryRecord]:
        """
        按发送者 ID 检索消息。

        Args:
            sender_id: 发送者的唯一标识符。
            keyword: 搜索关键词。
            platform_id: (可选) 限定在特定平台内搜索。
            limit: 返回结果数量。

        Returns:
            匹配的历史记录列表。
        """
        platforms = [platform_id] if platform_id else None
        return await self.search(
            keyword,
            senders=[sender_id],
            platforms=platforms,
            limit=limit,
        )

    async def search_global(self, keyword: str, limit: int = 20) -> list[HistoryRecord]:
        """进行全局检索，不受任何会话、平台或发送者限制。"""
        return await self.search(keyword, limit=limit)


_HISTORY_SERVICE: HistorySearchService | None = None


def get_history_search_service() -> HistorySearchService | None:
    """
    获取全局的历史消息检索服务单例。

    其他插件应通过此函数来获取服务实例，而不是自行创建。

    Returns:
        如果历史索引器插件已成功初始化，则返回 `HistorySearchService` 实例，
        否则返回 `None`。
    """
    return _HISTORY_SERVICE


def _set_history_search_service(service: HistorySearchService | None):
    global _HISTORY_SERVICE
    _HISTORY_SERVICE = service


@register(
    "astrbot_plugin_history_indexer",
    "ZenFun",
    "记录所有历史消息并提供基础检索能力",
    "0.2.0",
)
class HistoryIndexer(Star):
    """
    一个静默的本地历史消息索引器服务。

    核心功能:
    - **静默运行**: 作为一个后台服务，它不提供任何用户可直接交互的聊天命令。
    - **消息持久化**: 监听所有通过 AstrBot 的消息事件，并将其关键信息
      (如发送者、内容、时间等) 持久化到本地的 SQLite 数据库中。
    - **提供检索接口**: 通过 `HistorySearchService` 类，为其他需要历史消息的插件
      提供一个稳定、高效的异步检索接口。

    其他插件可以通过 `get_history_search_service()` 函数获取其实例来检索历史消息。
    数据库文件默认存储于 `data/activity_history_index.db`，以确保数据在插件更新后得以保留。
    """

    def __init__(self, context: Context):
        super().__init__(context)
        data_root = get_astrbot_data_path()
        self.db_path = os.path.join(data_root, "activity_history_index.db")
        self._initialized = False
        self.search_service = HistorySearchService(self.db_path)

    async def initialize(self):
        """插件初始化，创建数据库和表结构。"""
        self.search_service.db_path = self.db_path
        await asyncio.to_thread(self._init_db)
        self._initialized = True
        _set_history_search_service(self.search_service)
        logger.info(
            "HistoryIndexer initialized, database located at %s",
            self.db_path,
        )

    async def terminate(self):
        """插件终止，设置未初始化标志。"""
        self._initialized = False
        _set_history_search_service(None)

    @filter.event_message_type(filter.EventMessageType.ALL)
    async def capture(self, event: AstrMessageEvent):
        """捕获所有消息并异步写入索引。"""
        if not self._initialized:
            return

        message_text = (event.message_str or "").strip()
        outline = event.get_message_outline()
        if not message_text and not outline:
            return

        record = {
            "session_id": event.unified_msg_origin,
            "platform_id": event.get_platform_id(),
            "sender_id": str(event.get_sender_id()),
            "sender_name": event.get_sender_name() or "",
            "message_text": message_text or outline,
            "message_outline": outline or message_text,
            "created_at": (
                event.message_obj.timestamp
                if hasattr(event, "message_obj") and event.message_obj.timestamp
                else int(datetime.now(tz=timezone.utc).timestamp())
            ),
        }
        await asyncio.to_thread(self._insert_record, record)

    # ---------------- SQLite helpers ---------------- #

    def _get_conn(self) -> sqlite3.Connection:
        """创建并返回一个配置好的数据库连接。"""
        conn = sqlite3.connect(self.db_path, timeout=10)  # 设置超时以防锁库
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self):
        """初始化数据库，确保表和索引存在。"""
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        try:
            with self._get_conn() as conn:
                # messages 表: 存储所有消息记录
                # - id: 主键
                # - session_id: 会话的统一标识
                # - platform_id: 平台标识
                # - sender_id: 发送者 ID
                # - sender_name: 发送者昵称
                # - message_text: 消息的纯文本内容
                # - message_outline: 消息的简要概括（用于非纯文本消息）
                # - created_at: 消息创建的 Unix 时间戳 (UTC)
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS messages (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        session_id TEXT NOT NULL,
                        platform_id TEXT NOT NULL,
                        sender_id TEXT NOT NULL,
                        sender_name TEXT,
                        message_text TEXT,
                        message_outline TEXT,
                        created_at INTEGER NOT NULL
                    )
                    """
                )
                # session_id 和 created_at 索引: 加速按会话和时间倒序的查询
                conn.execute(
                    """
                    CREATE INDEX IF NOT EXISTS idx_messages_session_time
                        ON messages (session_id, created_at DESC)
                    """
                )
                # sender_id 和 created_at 索引: 加速按发送者和时间倒序的查询
                conn.execute(
                    """
                    CREATE INDEX IF NOT EXISTS idx_messages_sender_time
                        ON messages (sender_id, created_at DESC)
                    """
                )
        except sqlite3.Error as e:
            logger.error(f"Failed to initialize history database: {e}")
            raise
        else:
            logger.info("History database schema initialized successfully.")

    def _insert_record(self, record: dict):
        """将单条消息记录插入数据库。"""
        sql = """
            INSERT INTO messages (
                session_id, platform_id, sender_id, sender_name,
                message_text, message_outline, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """
        params = (
            record["session_id"],
            record["platform_id"],
            record["sender_id"],
            record["sender_name"],
            record["message_text"],
            record["message_outline"],
            record["created_at"],
        )
        try:
            with self._get_conn() as conn:
                conn.execute(sql, params)
        except sqlite3.Error as e:
            logger.error(
                "HistoryIndexer failed to insert message. Error: %s. Record: %s",
                e,
                record,
            )

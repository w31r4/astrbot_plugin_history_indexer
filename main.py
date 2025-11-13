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
from astrbot.core.star.filter.command import GreedyStr
from astrbot.core.utils.astrbot_path import get_astrbot_data_path

if TYPE_CHECKING:
    from astrbot.core.star.register.star_handler import RegisteringCommandable


@dataclass(slots=True)
class HistoryRecord:
    session_id: str
    platform_id: str
    sender_id: str
    sender_name: str
    message_text: str
    message_outline: str
    created_at: datetime

    def format_line(self) -> str:
        local_ts = self.created_at.astimezone().strftime("%m-%d %H:%M")
        return f"{local_ts} {self.sender_name}: {self.message_text}"


class HistorySearchService:
    """Reusable search helper that other plugins can import."""

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
        return await self.search(keyword, sessions=[session_id], limit=limit)

    async def search_by_platform(
        self,
        platform_ids: Sequence[str],
        keyword: str,
        limit: int = 20,
    ) -> list[HistoryRecord]:
        return await self.search(keyword, platforms=platform_ids, limit=limit)

    async def search_across_sessions(
        self,
        session_ids: Sequence[str],
        keyword: str,
        limit: int = 20,
    ) -> list[HistoryRecord]:
        return await self.search(keyword, sessions=session_ids, limit=limit)

    async def search_by_sender(
        self,
        sender_id: str,
        keyword: str,
        *,
        platform_id: str | None = None,
        limit: int = 20,
    ) -> list[HistoryRecord]:
        platforms = [platform_id] if platform_id else None
        return await self.search(
            keyword,
            senders=[sender_id],
            platforms=platforms,
            limit=limit,
        )

    async def search_global(self, keyword: str, limit: int = 20) -> list[HistoryRecord]:
        return await self.search(keyword, limit=limit)


_HISTORY_SERVICE: HistorySearchService | None = None


def get_history_search_service() -> HistorySearchService | None:
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
    简单的本地历史消息索引器。

    功能:
    - 监听所有消息事件，并将其持久化到本地 SQLite 数据库。
    - 提供基于关键词的简单消息检索功能。
    - 数据库文件存储于 `data/activity_history_index.db`，防止插件更新时数据丢失。
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

    @filter.command_group("hist")
    def hist(self):
        """历史检索命令组。"""

    hist = cast("RegisteringCommandable", hist)

    @hist.command("search")  # type: ignore [attr-defined]
    async def hist_search(
        self,
        event: AstrMessageEvent,
        keyword: GreedyStr = GreedyStr(""),
        limit: int = 5,
    ):
        """在当前会话的索引中搜索关键词。"""
        kw = (keyword or "").strip()
        if not kw:
            yield event.plain_result("请输入要搜索的关键字。")
            return
        limit = max(1, min(50, limit))
        try:
            records = await self.search_service.search_by_session(
                event.unified_msg_origin,
                kw,
                limit=limit,
            )
        except sqlite3.Error as e:
            logger.error(f"History search failed: {e}")
            yield event.plain_result(f"历史记录检索失败: {e}")
            return

        if not records:
            yield event.plain_result("未找到匹配的记录。")
            return

        yield event.plain_result(
            self._render_records(f"最近 {len(records)} 条匹配：", records),
        )

    @hist.command("session")  # type: ignore [attr-defined]
    async def hist_session(
        self,
        event: AstrMessageEvent,
        session: str | None = None,
        keyword: GreedyStr = GreedyStr(""),
        limit: int = 5,
    ):
        """显式指定会话 ID 进行检索，便于跨群定位。"""
        session_id = (session or "").strip()
        if not session_id:
            yield event.plain_result("请提供会话 ID。")
            return
        kw = (keyword or "").strip()
        records = await self.search_service.search_by_session(session_id, kw, limit)
        if not records:
            yield event.plain_result(f"会话 {session_id} 中未找到匹配。")
            return
        yield event.plain_result(
            self._render_records(
                f"会话 {session_id} 最近 {len(records)} 条匹配：",
                records,
            ),
        )

    @hist.command("platform")  # type: ignore [attr-defined]
    async def hist_platform(
        self,
        event: AstrMessageEvent,
        platform: str | None = None,
        keyword: GreedyStr = GreedyStr(""),
        limit: int = 5,
    ):
        """跨平台或指定平台范围内检索。"""
        platform_id = (platform or "").strip()
        if not platform_id:
            yield event.plain_result("请提供平台 ID。")
            return
        kw = (keyword or "").strip()
        records = await self.search_service.search_by_platform(
            [platform_id],
            kw,
            limit,
        )
        if not records:
            yield event.plain_result(f"平台 {platform_id} 中未找到匹配。")
            return
        yield event.plain_result(
            self._render_records(
                f"平台 {platform_id} 最近 {len(records)} 条匹配：",
                records,
            ),
        )

    @hist.command("global")  # type: ignore [attr-defined]
    async def hist_global(
        self,
        event: AstrMessageEvent,
        keyword: GreedyStr = GreedyStr(""),
        limit: int = 5,
    ):
        """跨平台、跨会话的全局检索。"""
        kw = (keyword or "").strip()
        if not kw:
            yield event.plain_result("请输入要搜索的关键字。")
            return
        records = await self.search_service.search_global(kw, limit)
        if not records:
            yield event.plain_result("全局范围内未找到匹配。")
            return
        yield event.plain_result(
            self._render_records(
                f"全局最近 {len(records)} 条匹配：",
                records,
            ),
        )

    @hist.command("sender")  # type: ignore [attr-defined]
    async def hist_sender(
        self,
        event: AstrMessageEvent,
        sender: str | None = None,
        platform: str | None = None,
        keyword: GreedyStr = GreedyStr(""),
        limit: int = 5,
    ):
        """按发送者 ID（可选平台限制）检索最近发言。"""
        sender_id = (sender or "").strip()
        if not sender_id:
            yield event.plain_result("请提供发送者 ID。")
            return
        kw = (keyword or "").strip()
        records = await self.search_service.search_by_sender(
            sender_id,
            kw,
            platform_id=(platform or "").strip() or None,
            limit=limit,
        )
        if not records:
            yield event.plain_result(f"未找到 {sender_id} 的匹配记录。")
            return
        scope = f"{sender_id}"
        if platform:
            scope += f"@{platform}"
        yield event.plain_result(
            self._render_records(
                f"{scope} 最近 {len(records)} 条匹配：",
                records,
            ),
        )

    def _render_records(self, title: str, records: list[HistoryRecord]) -> str:
        lines = [title]
        for record in records:
            lines.append(f"- {record.format_line()}")
        return "\n".join(lines)

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

    def _insert_record(self, record: dict):
        """将单条消息记录插入数据库。"""
        try:
            with self._get_conn() as conn:
                conn.execute(
                    """
                    INSERT INTO messages (
                        session_id,
                        platform_id,
                        sender_id,
                        sender_name,
                        message_text,
                        message_outline,
                        created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        record["session_id"],
                        record["platform_id"],
                        record["sender_id"],
                        record["sender_name"],
                        record["message_text"],
                        record["message_outline"],
                        record["created_at"],
                    ),
                )
        except sqlite3.Error as e:
            logger.error(f"HistoryIndexer failed to insert message: {e}")

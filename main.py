from __future__ import annotations

import asyncio
import os
import sqlite3
from datetime import datetime, timezone

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star, register
from astrbot.core.star.filter.command import GreedyStr
from astrbot.core.utils.astrbot_path import get_astrbot_data_path


@register(
    "astrbot_plugin_history_indexer",
    "ZenFun",
    "记录所有历史消息并提供基础检索能力",
    "0.1.0",
)
class HistoryIndexer(Star):
    """简单的本地历史消息索引器。"""

    def __init__(self, context: Context):
        super().__init__(context)
        data_root = get_astrbot_data_path()
        self.db_path = os.path.join(data_root, "activity_history_index.db")
        self._initialized = False

    async def initialize(self):
        await asyncio.to_thread(self._init_db)
        self._initialized = True
        logger.info(
            "HistoryIndexer initialized, database located at %s",
            self.db_path,
        )

    async def terminate(self):
        self._initialized = False

    @filter.event_message_type(filter.EventMessageType.ALL)
    async def capture(self, event: AstrMessageEvent):
        """捕获所有消息并写入索引。"""
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
            "created_at": event.message_obj.timestamp
            if hasattr(event, "message_obj") and event.message_obj.timestamp
            else int(datetime.now(tz=timezone.utc).timestamp()),
        }
        await asyncio.to_thread(self._insert_record, record)

    @filter.command_group("hist")
    def hist(self):
        """历史检索"""

    @hist.command("search")
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
        rows = await asyncio.to_thread(
            self._search_records,
            event.unified_msg_origin,
            kw,
            limit,
        )
        if not rows:
            yield event.plain_result("未找到匹配的记录。")
            return

        parts = [f"最近 {len(rows)} 条匹配："]
        for row in rows:
            ts = datetime.fromtimestamp(row["created_at"], tz=timezone.utc)
            local_ts = ts.astimezone().strftime("%m-%d %H:%M")
            parts.append(f"- {local_ts} {row['sender_name']}: {row['message_text']}")
        yield event.plain_result("\n".join(parts))

    # ---------------- SQLite helpers ---------------- #

    def _get_conn(self):
        conn = sqlite3.connect(self.db_path, isolation_level=None)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self):
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        conn = self._get_conn()
        try:
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
                """,
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_messages_session_time
                    ON messages (session_id, created_at DESC)
                """,
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_messages_sender_time
                    ON messages (sender_id, created_at DESC)
                """,
            )
        finally:
            conn.close()

    def _insert_record(self, record: dict):
        conn = self._get_conn()
        try:
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
        except Exception as exc:
            logger.error(f"HistoryIndexer failed to insert message: {exc}")
        finally:
            conn.close()

    def _search_records(self, session_id: str, keyword: str, limit: int):
        conn = self._get_conn()
        try:
            cursor = conn.execute(
                """
                SELECT sender_name, message_text, created_at
                FROM messages
                WHERE session_id = ?
                  AND message_text LIKE ?
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (session_id, f"%{keyword}%", limit),
            )
            return cursor.fetchall()
        finally:
            conn.close()

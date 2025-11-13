from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timezone

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.core.star.filter.command import GreedyStr
from astrbot.api.star import Context, Star, register
from astrbot.core.utils.astrbot_path import get_astrbot_data_path

from .executor import run_blocking, shutdown_executor
from .search_service import HistorySearchService
from .service_registry import set_history_search_service


@register(
    "astrbot_plugin_history_indexer",
    "ZenFun",
    "记录所有历史消息并提供基础检索能力",
    "0.2.0",
)
class HistoryIndexer(Star):
    """
    一个静默的本地历史消息索引器服务。
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
        await run_blocking(self._init_db)
        self._initialized = True
        set_history_search_service(self.search_service)
        logger.info(
            "HistoryIndexer initialized, database located at %s",
            self.db_path,
        )

    async def terminate(self):
        """插件终止，设置未初始化标志。"""
        self._initialized = False
        set_history_search_service(None)
        shutdown_executor()

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("history_indexer_probe", alias={"hiprobe"})
    async def history_indexer_probe(
        self,
        event: AstrMessageEvent,
        keyword: GreedyStr = GreedyStr(""),
    ):
        """管理员自检命令：验证索引数据库与基础检索是否可用。"""
        if not self._initialized:
            yield event.plain_result("HistoryIndexer 尚未初始化。")
            return

        stats = await run_blocking(self._collect_index_stats)
        if not stats["exists"]:
            yield event.plain_result("尚未生成 activity_history_index.db，无法执行自检。")
            return

        keyword_text = (keyword or "").strip()
        lines = [
            "【HistoryIndexer 自检】",
            f"- DB: {self.db_path}",
            f"- 总记录数: {stats['total']}",
        ]
        latest = stats.get("latest")
        if latest:
            lines.append(
                f"- 最新记录时间: {latest.astimezone().strftime('%Y-%m-%d %H:%M:%S %Z')}",
            )

        if keyword_text:
            matches = await self.search_service.search_global(
                keyword_text,
                limit=5,
            )
            if not matches:
                lines.append(f"- 关键词 '{keyword_text}' 未命中任何记录。")
            else:
                lines.append(
                    f"- 关键词 '{keyword_text}' 命中 {len(matches)} 条（最多展示 5 条）：",
                )
                for rec in matches:
                    snippet = rec.message_outline or rec.message_text or ""
                    if len(snippet) > 60:
                        snippet = snippet[:60] + "..."
                    lines.append(
                        f"  - [{rec.created_at.astimezone().strftime('%m-%d %H:%M')}] "
                        f"{rec.sender_name or rec.sender_id}: {snippet}",
                    )
        elif stats["samples"]:
            lines.append("- 最近 3 条记录：")
            for rec in stats["samples"]:
                snippet = rec["message"] or ""
                if len(snippet) > 60:
                    snippet = snippet[:60] + "..."
                lines.append(
                    f"  - [{rec['created_at'].astimezone().strftime('%m-%d %H:%M')}] "
                    f"{rec['sender_name']}: {snippet}",
                )
        else:
            lines.append("- 数据库尚无可展示的记录。")

        yield event.plain_result("\n".join(lines))

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
        await run_blocking(self._insert_record, record)

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
                conn.execute(
                    """
                    CREATE INDEX IF NOT EXISTS idx_messages_session_time
                        ON messages (session_id, created_at DESC)
                    """
                )
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

    def _collect_index_stats(self, sample_size: int = 3):
        if not os.path.exists(self.db_path):
            return {"exists": False}

        samples = []
        latest = None
        with self._get_conn() as conn:
            total_row = conn.execute(
                "SELECT COUNT(1) AS cnt FROM messages",
            ).fetchone()
            total = total_row["cnt"] if total_row else 0
            latest_row = conn.execute(
                """
                SELECT created_at FROM messages
                ORDER BY created_at DESC LIMIT 1
                """
            ).fetchone()
            if latest_row:
                latest = datetime.fromtimestamp(
                    latest_row["created_at"],
                    tz=timezone.utc,
                )
            sample_rows = conn.execute(
                """
                SELECT session_id, sender_name, message_outline,
                       message_text, created_at
                FROM messages
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (sample_size,),
            ).fetchall()

        for row in sample_rows:
            sender = row["sender_name"] or "Unknown"
            snippet = row["message_outline"] or row["message_text"] or ""
            samples.append(
                {
                    "session_id": row["session_id"],
                    "sender_name": sender,
                    "message": snippet,
                    "created_at": datetime.fromtimestamp(
                        row["created_at"],
                        tz=timezone.utc,
                    ),
                },
            )

        return {
            "exists": True,
            "total": total,
            "latest": latest,
            "samples": samples,
        }


__all__ = ["HistoryIndexer"]

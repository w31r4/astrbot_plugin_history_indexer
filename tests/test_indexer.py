import asyncio
import os
import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone
from typing import cast
from unittest.mock import MagicMock, patch

from astrbot_plugin_history_indexer.main import (
    HistoryIndexer,
    HistorySearchService,
    get_history_search_service,
)


class TestHistoryIndexer(unittest.TestCase):
    def setUp(self):
        """为每个测试用例设置一个临时的数据库。"""
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.temp_dir.name, "test_history.db")

        # 模拟 AstrBot 上下文和路径函数
        self.mock_context = MagicMock()
        self.patcher = patch(
            "astrbot.core.utils.astrbot_path.get_astrbot_data_path",
            return_value=self.temp_dir.name,
        )
        self.patcher.start()

        self.indexer = HistoryIndexer(self.mock_context)
        # 确保测试时使用临时数据库路径
        self.indexer.db_path = self.db_path
        self.indexer.search_service.db_path = self.db_path

    def tearDown(self):
        """清理临时目录和文件。"""
        self.patcher.stop()
        self.temp_dir.cleanup()

    def test_capture_and_search(self):
        """测试消息的捕获和搜索功能。"""
        asyncio.run(self._test_capture_and_search())

    async def _test_capture_and_search(self):
        # 1. 初始化
        await self.indexer.initialize()
        self.assertTrue(self.indexer._initialized)
        self.assertTrue(os.path.exists(self.db_path))

        # 2. 模拟消息事件并捕获
        mock_event1 = self._create_mock_event("session1", "user1", "Alice", "hello world")
        mock_event2 = self._create_mock_event("session1", "user2", "Bob", "another message")
        mock_event3 = self._create_mock_event("session1", "user1", "Alice", "hello again")

        await self.indexer.capture(mock_event1)
        await self.indexer.capture(mock_event2)
        await self.indexer.capture(mock_event3)

        # 3. 验证数据是否写入
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute("SELECT * FROM messages")
            rows = cursor.fetchall()
            self.assertEqual(len(rows), 3)

        # 4. 测试核心服务功能
        service = get_history_search_service()
        self.assertIsNotNone(service)
        service = cast(HistorySearchService, service)

        # 按会话搜索
        session_records = await service.search_by_session("session1", "hello", limit=5)
        self.assertEqual(len(session_records), 2)
        self.assertEqual(session_records[0].sender_name, "Alice")
        self.assertEqual(session_records[1].sender_name, "Alice")

        # 按平台搜索
        platform_records = await service.search_by_platform(["test_platform"], "another", 5)
        self.assertEqual(len(platform_records), 1)
        self.assertEqual(platform_records[0].sender_name, "Bob")

        # 按发送者搜索
        sender_records = await service.search_by_sender("user1", "again", limit=5)
        self.assertEqual(len(sender_records), 1)
        self.assertEqual(sender_records[0].message_text, "hello again")

        # 全局搜索
        global_records = await service.search_global("message", limit=5)
        self.assertEqual(len(global_records), 1)

        # 搜索无结果
        no_result_records = await service.search_global("nonexistent", limit=5)
        self.assertEqual(len(no_result_records), 0)

        # 5. 终止
        await self.indexer.terminate()
        self.assertFalse(self.indexer._initialized)

    def _create_mock_event(self, session_id: str, sender_id: str, sender_name: str, text: str) -> MagicMock:
        """创建一个模拟的 AstrMessageEvent。"""
        event = MagicMock()
        event.unified_msg_origin = session_id
        event.get_platform_id.return_value = "test_platform"
        event.get_sender_id.return_value = sender_id
        event.get_sender_name.return_value = sender_name
        event.message_str = text
        event.get_message_outline.return_value = text
        event.message_obj.timestamp = int(datetime.now(tz=timezone.utc).timestamp())

        # 模拟 yield 的返回行为
        event.plain_result.side_effect = lambda msg: MagicMock(get_message=lambda: msg)
        return event


if __name__ == "__main__":
    unittest.main()

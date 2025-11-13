import asyncio
import os
import sqlite3
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from typing import cast
from unittest.mock import AsyncMock, MagicMock, patch

# Mock the astrbot module before any other imports
sys.modules["astrbot"] = MagicMock()
sys.modules["astrbot.api"] = MagicMock()
sys.modules["astrbot.api.event"] = MagicMock()
sys.modules["astrbot.api.star"] = MagicMock()
sys.modules["astrbot.core"] = MagicMock()
sys.modules["astrbot.core.utils"] = MagicMock()
sys.modules["astrbot.core.utils.astrbot_path"] = MagicMock()

# Add project root to path to allow importing `main`
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from main import (
    HistoryIndexer,
    HistoryRecord,
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
        self.path_patcher = patch(
            "main.get_astrbot_data_path",
            return_value=self.temp_dir.name,
        )
        self.path_patcher.start()

        self.indexer = HistoryIndexer(self.mock_context)
        # 确保测试时使用临时数据库路径
        self.indexer.db_path = self.db_path
        self.indexer.search_service.db_path = self.db_path

        # 将异步方法模拟为 AsyncMock
        self.indexer.initialize = AsyncMock()
        self.indexer.capture = AsyncMock()
        self.indexer.terminate = AsyncMock()

    def tearDown(self):
        """清理临时目录和文件。"""
        self.path_patcher.stop()
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
        mock_event4 = self._create_mock_event("session2", "user3", "Carol", "fuzzy searching test")

        await self.indexer.capture(mock_event1)
        await self.indexer.capture(mock_event2)
        await self.indexer.capture(mock_event3)
        await self.indexer.capture(mock_event4)

        # 3. 验证数据是否写入
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute("SELECT * FROM messages")
            rows = cursor.fetchall()
            self.assertEqual(len(rows), 4)

        # 4. 测试核心服务功能
        service = get_history_search_service()
        self.assertIsNotNone(service)
        service = cast(HistorySearchService, service)

        # 精确搜索
        exact_records = await service.search_by_session("session1", "hello world", limit=5)
        self.assertEqual(len(exact_records), 1)
        self.assertEqual(exact_records[0].message_text, "hello world")

        # 模糊搜索
        fuzzy_records = await service.search_global("fzy srch tst", limit=5)
        self.assertEqual(len(fuzzy_records), 1)
        self.assertEqual(fuzzy_records[0].sender_name, "Carol")

        # 阈值测试
        high_threshold_records = await service.search_global("fzy srch tst", limit=5, fuzzy_threshold=95)
        self.assertEqual(len(high_threshold_records), 0)

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
        # 创建一个 message_obj 的 mock，并为其 timestamp 属性赋值
        message_obj_mock = MagicMock()
        message_obj_mock.timestamp = int(datetime.now(tz=timezone.utc).timestamp())
        event.message_obj = message_obj_mock

        # 模拟 yield 的返回行为
        event.plain_result.side_effect = lambda msg: MagicMock(get_message=lambda: msg)
        return event


if __name__ == "__main__":
    unittest.main()

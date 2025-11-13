from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


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


__all__ = ["HistoryRecord"]

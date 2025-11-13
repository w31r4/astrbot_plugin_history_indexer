# 历史消息索引器插件

这是一个为 [AstrBot](https://github.com/w31r4/AstrBot) 设计的静默后台服务插件。

## 功能

- **静默运行**: 作为一个后台服务，它不提供任何用户可直接交互的聊天命令。
- **消息持久化**: 监听所有通过 AstrBot 的消息事件，并将其关键信息 (如发送者、内容、时间等) 持久化到本地的 SQLite 数据库中。
- **提供检索接口**: 通过 `HistorySearchService` 类，为其他需要历史消息的插件提供一个稳定、高效的异步检索接口。

数据库文件默认存储于 `data/activity_history_index.db`，以确保数据在插件更新后得以保留。

## 如何为你的插件集成历史检索能力

其他插件可以通过导入并调用 `get_history_search_service` 函数来访问本插件提供的检索服务。

### 步骤

1.  **确保插件加载顺序**:
    请确保你的插件在 `astrbot_plugin_history_indexer` **之后** 加载，以保证服务在被调用时已经初始化。

2.  **导入服务**:
    在你的插件代码中，从本插件导入 `get_history_search_service` 函数。

    ```python
    from astrbot_plugin_history_indexer.main import get_history_search_service
    ```

3.  **获取服务实例并使用**:
    在需要检索历史消息的地方，调用该函数获取服务实例。请注意，该函数可能返回 `None`（如果历史索引器插件未加载或未初始化），因此需要进行判断。

    ```python
    # 示例：在一个事件处理器或命令处理器中使用
    async def handle_some_event(event: AstrMessageEvent):
        history_service = get_history_search_service()

        if not history_service:
            # 处理服务不可用的情况
            print("历史记录服务不可用。")
            return

        try:
            # 异步搜索当前会话中包含 "hello" 的最近 10 条消息
            records = await history_service.search_by_session(
                event.unified_msg_origin, "hello", limit=10
            )

            if not records:
                print("未找到相关历史记录。")
                return

            # 处理查询结果
            response_lines = ["找到最近的相关记录："]
            for record in records:
                # HistoryRecord 对象包含丰富的消息详情
                # record.session_id, record.sender_name, record.message_text, etc.
                response_lines.append(f"- {record.format_line()}")

            # ... 后续处理，例如发送消息 ...
            print("\n".join(response_lines))

        except Exception as e:
            print(f"检索历史记录时出错: {e}")

    ```

### 可用的检索方法

`HistorySearchService` 提供了多种便捷的检索方法：

- `search()`: 通用的多维度检索。
- `search_by_session(session_id, keyword, limit)`: 在指定会话中检索。
- `search_by_platform(platform_ids, keyword, limit)`: 在指定平台中检索。
- `search_by_sender(sender_id, keyword, platform_id, limit)`: 按发送者检索，可选平台限制。
- `search_global(keyword, limit)`: 全局检索。

详细的参数说明请参考 `main.py` 中 `HistorySearchService` 类的文档字符串。

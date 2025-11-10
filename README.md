# 历史消息索引器（astrbot_plugin_history_indexer）

为 AstrBot 提供一个本地的历史消息数据库，所有经由机器人接收的消息都会被写入 SQLite 文件，供其他插件（例如“多智能体行为监听”）快速检索。

## 功能
- 监听所有消息事件（不区分群聊 / 私聊），提取 `unified_msg_origin`、平台 ID、发送者信息、纯文本内容、消息概要、时间戳等字段。
- 将数据实时写入 `data/activity_history_index.db`（SQLite）。如文件不存在会自动创建，包括必要索引。
- 自带 `/hist search <keyword> [limit]` 指令，可在当前会话维度内按关键字扫描最近记录，便于调试索引是否正常工作。

## 与其他插件的协作
- 任何插件都可以直接读取 `data/activity_history_index.db`，按 `session_id`、`sender_id`、`created_at`、`message_text` 查询；推荐只读连接。
- “多智能体行为监听”插件会优先使用该数据库来执行“回到过去”检索——因此**请先启用本索引器，再启用监听插件**。

## 安装
1. 将 `astrbot_plugin_history_indexer` 放入 `data/plugins/`，在 AstrBot WebUI 中启用。
2. 确保插件启动后能看到 `data/activity_history_index.db` 文件（默认 1 分钟内创建）。
3. 可选：在 QQ / 其他协议适配器中打开“消息存档”以获得更完整的历史，再配合本插件持续补充。

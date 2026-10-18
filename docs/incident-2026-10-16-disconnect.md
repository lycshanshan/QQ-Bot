# 2026-10-16 QQ 断线排查

以下时间均为上海时间。用户报告执行 AI 命令后机器人似乎崩溃。

## 已核实的故障层级

- Python 业务进程、8080 监听和其 SDK 子进程仍存活；stderr 为空，没有业务进程退出证据。
- 最近 AI 请求 `aefe9aa77f674cc0b4ae3210a57ec2e3` 在 00:40:41 接收、约 00:40:48 完成；数据库 succeeded，Outbox 42 为 sent，QQ 回执 `868834427`。该次为普通文本问答，无高级任务。
- 00:41:20 OneBot 日志显示 WebSocket 被对端关闭。项目专属 QQ 的 Worker 子进程随后于 00:41:24 重建；主 QQ 进程仍在。Windows Application 日志没有此窗口的崩溃记录。
- NapCat WebUI 返回 `isLogin=false, loginPhase=waiting_qrcode`；指定机器人账号快速登录返回“你的用户身份已失效，为保证账号安全，请你重新登录”。这解释了为什么 30 秒 WebSocket 重试不能恢复：QQ 账号没有登录。
- 00:54:17 业务日志确认 QQ `3653125828` 重新连接。

结论：本次能确定的是 QQ/NapCat 登录会话失效，不能归因于 AI 生成崩溃。NapCat 当时未开启文件日志，旧登录错误已随 Worker 重启丢失；尚不能判断是人工退出、异处登录、服务端会话失效还是其他客户端问题。没有更改 QQ 安全设置、协议版本、个人 QQ 进程或自动重试账号登录。

## 本次改动

- 增加业务侧连接/断线记录，持续 60 秒不可用时输出一次明确提示；恢复后重置提示状态。`artifacts/runtime/connection.json` 保存启动时间、当前连接账号、最后连接/断线和持续离线起点，无消息和凭据。
- AI 日志增加请求 ID、模型、SDK thread/turn ID、执行阶段、异常类型、结束状态、耗时和 token。禁止记录请求正文、异常原文和 traceback 局部变量。
- 修正 AI worker 兜底：子任务若意外逃出内部异常处理，不能只读取并吞掉 exception；现在记录类型、释放仍在执行的预留、将任务标为失败并清空对应私聊 thread，防止请求长期卡住。不会自动重跑模型。
- 未开启 NapCat 原始消息文件日志，避免额外持久化所有群/私聊正文。已保留原故障 stdout/stderr 到 `artifacts/runtime/incident-*.log`，后续重启不会覆盖这份证据。

## 验证

- 完整回归 70 项通过（原 66 项，加连接状态/离线去重、异常任务结算、不泄露输入的错误日志测试）。编译和 diff whitespace 检查通过。
- 重启前无排队/执行 AI 或待发送任务；另有数据库备份 `artifacts/runtime/backups/before-disconnect-fix-2026-10-16.sqlite3`。
- 修复版于 00:56:03 启动，业务进程 PID `9712`，00:56:33 与 QQ 重连；连接状态文件正确、stderr 为空。真实 AI 请求复验通过：00:57:03 收到请求 `7c662ed05ab5434ca04d7bad2ee1e905`，00:57:20 succeeded，耗时 16.4 秒；Outbox 44 sent，用户确认正常收到。

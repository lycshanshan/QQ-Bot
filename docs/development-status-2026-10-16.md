# 开发进度与验收（2026-10-16）

本轮按照需求草案、设计稿与验证结果推进，没有上线部署，也没有提交或覆盖原有未提交工作。当前机器人 QQ `3653125828`，监听/验收群 `1049251004`，superuser `2430023288`。

## 实际交付

| 模块 | 当前能力 |
| --- | --- |
| 基础 | SQLite schema v3、WAL/事务迁移、规范消息段、持久化去重、撤回 tombstone、角色/封禁、群监听、总开关、审计、热更新、单实例锁 |
| 发送 | 事务 Outbox、按会话顺序、限速、明确拒绝重试、断线/超时/退出的不确定发送保留待处理、发送前权限/开关/退订复查 |
| 打卡 | 上海时区严格文本匹配、闭区间、凌晨归前一天、每群每人每时段唯一、文案权重与收集、累计/连续/历史最长统计 |
| AI | 官方 openai-codex 0.162.1、本机订阅登录、私聊默认开启、群开关及原始 @ 触发、/ask、持久化私聊 thread、一次性群历史、额度与管理、取消/超时、排队与并发 |
| 高级任务 | Luna 主 Agent 的项目 Skill；最多两个允许模型的高级任务，由后端顺序执行、独立 thread/规则、不递归委派，主模型整合；任务与 token 用量落库 |
| 媒体 | 当前/同会话引用的图片和文件按需下载；过期 URL 刷新一次；每请求最多 5 个附件、单个 10 MiB；PNG/JPEG/WebP 作为 SDK 图片输入 |
| 输出 | JSON Schema；纯文本、分页 Markdown 图片、生成文件；中文/公式/表格/代码；KaTeX 本地资源；渲染失败退回 Markdown 文件；文件只从任务 output/ 交付 |
| 归档 | 每会话最近 1000 条，额外旧消息满 1000 条导出单行 JSON；固定批次、哈希和可恢复发布；不可变 ZIP、清单、10,000,000 字节/24 小时封包、SFTP 临时上传/回读哈希/发布/重试 |
| 直播 | 实验性公开接口，VIP+ 修改订阅，共享轮询 60—120 秒、首轮基线、会话 ID 去重、短断流去抖、错误退避/暂停、通知去重、订阅列表 |

用户 NAS 自助绑定/保存、B站动态订阅依用户选择暂缓。动态相关逻辑保留停用注释，没有动态轮询任务。未提供任意 `/shell` 或线上服务控制器，`/system restart` 不开放。

## 已确认的当前规则

- 打卡开始/结束都包含：早 `04:30:00—12:00:00`，晚 `20:00:00—次日03:00:00`。结束后一秒无效；不去首尾空格，带 @、引用、图片或额外标点不计；超过 5 分钟延迟不补签。统计按群隔离。
- 示例早/晚文案各两条，权重 90/10，可修改配置；首次有效打卡保存抽取结果，重复不刷次数或收集。
- 普通用户每日 20 次，上海时间 00:00 重置；VIP/admin/superuser 不限业务次数，仍受并发、队列、超时和全局准入预算限制。
- 成功的顶层生成扣 1，高级任务不额外扣；提供方失败释放；生成前取消不扣，生成后用户取消扣 1。生成成功而 QQ 发送失败时保留结果并按生成结算，不重新调用模型。格式处理失败也保留已生成原文。
- `/context user|group N` 只影响下一次请求，最多 20 条、最近 24 小时，限定当前群；普通用户可选择本群历史。撤回不进入新取出的历史；`/new` 不删除聊天记录。
- 每用户同时只运行一个请求，最多 3 个排队/执行请求；全局默认并发 2、总队列 20、任务超时 120 秒、排队 300 秒。主/高级/整合顺序执行，取消覆盖当前高级任务。
- Agent 按用户 2026-10-16 的明确选择，在 Windows 本机使用 `full_access` 执行命令；没有配置 WSL/容器/操作系统沙箱。任务目录只组织文件，不承诺宿主隔离。个人连接器、原有 MCP、SDK 自主多 Agent 和记忆功能不作为机器人能力加载。
- 用户已明确授权：只有 NAS 远端回读哈希校验成功后，才自动清理该包对应的本地 JSON/ZIP；最近消息和归档清单保留。可设置 `NAS_ARCHIVE_KEEP_LOCAL=true` 保留副本。本次开发工具一度拒绝删除默认值改动，用户明确授权后才继续；运行时由脚本按状态处理，不依赖本聊天的审批。
- bot off 继续采集已监听聊天，停止其他用户回复和系统直播通知，保留 superuser 恢复入口。

## 常用命令

| 权限 | 命令 |
| --- | --- |
| user | `/帮助` `/ping` `/状态` `/复读 <文本>` `/提问 <问题>` `/额度` `/取消` `/新会话`（私聊） `/上下文 user|group <1-20>`（群） `/打卡查询` `/订阅列表` |
| vip+ | `/subscribe bili-live <直播间ID>` `/unsubscribe bili-live <直播间ID>` |
| admin+ | `/watch add <群号>`、群内 `/ai on/off`、群内 `/checkin on/off`、`/quota-admin limit default <N>`、`limit user <QQ> <N>`、`grant <N> [QQ]`、`reset [QQ]`、`/system health/reload` |
| superuser | `/admin add/remove <QQ>` `/vip add/remove <QQ>` `/ban <QQ>` `/unban <QQ>` `/watch remove <群号>` `/bot on/off` `/model set <模型>` `/high-model set <模型>` |

模型目前只允许已验证的 `gpt-6-luna`、`gpt-6.1-sol`，模型修改先查询账号模型目录。群内斜杠命令无需 @；未知斜杠命令不会交给 AI。普通命令按用户/会话 1 秒限速，echo 限 1000 字符。额度 reset 不填 QQ 时重置已记录用户的当日已用量，在途预留保留；grant 不填 QQ 时加赠已记录用户。

## 本地配置和文件

- `.env`：OneBot Token、NAS 自身归档凭据和开关，已忽略 Git；草案中的密码未复制进文档。
- `config.example.toml` → 可选 `config.toml`：配置、文案、AI 资源参数、直播开关；不复制时使用相同默认值。
- `config/policy.toml`：普通用户额度策略；命令修改先保存 pending 记录，再原子发布文件，重启按哈希恢复；用量、预留和流水在 SQLite。
- `artifacts/runtime/bot.sqlite3`：正式本地业务库；在线备份在 `artifacts/runtime/backups/`。
- `agent-workspace/<随机ID>/input|output|scratch`：每次请求的附件和产物；任务数据忽略 Git。
- `artifacts/runtime/archives/`：在线窗口之外的 JSON/ZIP；NAS 目标为 qqbot 个人 `/home/archives/{bot}/{kind}/{target}/{year}/{month}/...`，固定已验证主机密钥，不进入同级共享目录。
- `artifacts/runtime/renderer/katex/`：固定 KaTeX 0.16.22，npm sha512 校验后安装；使用本机 Edge/Chrome。渲染禁用原始 HTML、外部图片/资源和页面网络。
- `tools/runtime.py outbox`：查看未决发送；`retry <ID> --confirm-not-delivered`、`suppress <ID>` 人工处理；`backup <新文件>` 用 SQLite 备份接口，不做迁移也不覆盖。

所有代码、探针和测试保留在项目中；原验证产物仍在 artifacts/feasibility/，没有当作正式历史导入。

## 验收与运行证据

1. 本地回归：当前 70 项通过；编译检查和 pip check 通过。包含权限、未监听群、去重、事务回滚、午夜边界、多群、配额并发、取消计次、会话重置、输出路径、Outbox、不确定发送、归档恢复、备份、单实例、高级任务、直播基线/共享/去抖/暂停/退订、NAS 重试与限定清理。
2. 用户已确认真实 QQ：私聊/群基础命令，打卡同一晚仅计一次；私聊持续上下文、/新会话、图片理解、生成 txt 文件；高级任务整合后的公式/表格图片清晰；直播订阅和列表正常。
3. 真实高级模型探针：模型顺序 `gpt-6-luna → gpt-6.1-sol → gpt-6-luna`，状态 completed，生成文件内容有效；该样本合计记录 43,882 tokens，不是性能 SLA。
4. 真实取消探针：在任务目录启动合成命令后调用 /cancel，SDK 最终状态 interrupted；请求 cancelled、扣 1 次、预留 0。没有向 QQ 发送探针内容。
5. NAS 合成完整链路：4 条合成消息，1 条留在线窗口，3 条 JSON 打包成 1,792 字节 ZIP；SFTP 上传、回读 SHA-256 与发布通过。本次远端探针文件和其唯一目录已删除；限定本地清理另有临时数据测试，源清单保留。
6. B站真实直播接口：房间 `1863474053` 返回 code=0、UID `397315493`、当时 offline。用户提供的动态 UID `330430013` 首次请求 HTTP 412，此后没有重试动态接口，也没有引入 Cookie；动态按用户要求暂停。
7. 测试消息仅主动发往此前获准的私聊 `2430023288` 和群 `1049251004`。所有真实 QQ 回复以 API 回执及用户客户端确认为边界，未声称已观察真实主播开播。

8. 最终本地重启：2026-10-16 00:26:02 服务启动，00:26:31 NapCat 重连 QQ `3653125828`，stderr 为空；直播轮询成功、无暂停/错误。业务进程 PID `26384`（venv 启动器 `40268`）；重启前数据库另存 `artifacts/runtime/backups/final-2026-10-16.sqlite3`。最新自动清理逻辑已加载。

## 当日断线修复

00:41 左右 QQ/NapCat 登录会话失效，Python 和 SDK 进程仍在，最近一次 AI 已成功并送达。重新扫码后恢复；本次增加断线状态文件/持续离线提示、AI 阶段元数据日志和异常任务结算兜底，70 项回归通过。证据及原因边界见 [断线排查记录](incident-2026-10-16-disconnect.md)。

## 已知边界

- 尚未等待真实主播开播；状态变化和重启/去抖用模拟 provider 验证，真实接口与群命令已经测试。
- 未进行长时间压测、物理断电、磁盘损坏或 Ubuntu 部署演练；现有业务在单进程架构运行。全局 token 设置是准入/调度预算，不能保证在途生成在某一个 token 上精确停止。
- GIF 动画、语音/视频、复杂转发和卡片仅保留元数据；图片理解先支持静态 PNG/JPEG/WebP。PDF/DOCX 等文件交给 Agent 在任务目录处理，没有另建专用文档解析服务。
- NAS 当前实现固定主机密钥、临时上传、回读校验和可重试；没有承诺大文件断点续传。自动封包需要已经形成 JSON 批次，1000 条旧消息不足时仍暂存在 SQLite。
- 任务工作目录暂不自动删除；聊天保留期限、长期产物清理、NAS 加密与完整灾难恢复可在后续阶段完善。当前用户 NAS 保存、B站动态和上线部署均不在继续执行范围内。
- 第三方 NoneBot 在 Python 3.13 上仍有 ForwardRef 弃用警告；未修改第三方库。

官方依据：[Codex SDK](https://learn.chatgpt.com/docs/codex-sdk)、[配置参考](https://learn.chatgpt.com/docs/config-file/config-reference)、[Playwright 截图](https://playwright.dev/python/docs/api/class-page#page-screenshot)、[Markdown 安全配置](https://markdown-it-py.readthedocs.io/en/latest/security.html)。权限模式以本轮用户最新选择为准，旧 Windows 隔离探针仅作为历史证据。

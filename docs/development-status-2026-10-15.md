# 开发进度与本地验收

更新日期：2026-10-15，Asia/Shanghai。本文记录实际实现状态；原设计稿仍保留方案背景。没有执行上线部署。

## 当前阶段

P1 业务基础、P2 早晚打卡已实现并接入默认入口。机器人已重新启动，NapCat 反向 WebSocket 已重连。

- 配置：`config.example.toml` 提供示例，启动时可加载根目录 `config.toml`；不复制也有同等默认值。唯一 superuser 固定为 `2430023288`，不接受运行时配置替换。
- 存储：本地 SQLite schema v1、WAL、外键、忙等待、事务迁移与在线备份。消息保留有序原始段、昵称/群名片、方向、UTC 时间和会话标识。
- 接收：私聊及已监听群才长期记录；未监听群仅允许 admin+ 明确添加监听。添加时调用 NapCat `get_group_list` 验证机器人已入群。忽略自身消息，以持久化事件键防重；归档后的旧事件仍不能重放。
- 权限：`superuser > admin > vip > user`，身份独立于 QQ 群管理身份。admin/VIP 变更、封禁、群监听、总开关及配置重载有审计记录。
- 总开关：off 后继续记录已监听会话；停止其他用户响应，保留 superuser 控制入口。发送前再次检查封禁、监听和总开关。
- 发送：业务记录和 Outbox 同事务提交；每个会话保持顺序，按块限速。明确 API 拒绝最多尝试 5 次；超时、断线、发送中退出或缺少 message_id 标为 uncertain，不自动重发，也不让同会话后续消息越过它。
- 本地归档：每会话保留最近 1000 条，额外旧消息凑足 1000 条才导出紧凑单行 JSON。因此正常情况下数据库可暂存 1000—1999 条；失败时保留源数据。冻结快照、固定批次 ID、fsync/同盘原子替换、SHA-256 校验和事务删除支持恢复。JSON 目前保留本地，ZIP/NAS 尚未实现。
- 撤回：独立 tombstone，不因消息已经归档而失去撤回标识。引用快照仅从同会话的已记录原消息取得；无法验证来源时只保留引用 ID。
- 打卡：每群/用户/业务日期/早晚时段各一次；固定 ID 文案按权重抽取，结果与回复一起落库；累计、当前连续和历史最长天数/次数及文案收集均可查询。
- 日志：业务入口不输出正文，日志输出端过滤 NoneBot 的事件正文和 OneBot 原始载荷，避免未监听消息在路由前进入业务日志。NapCat 自身日志不由该过滤器管理。
- 运行：同一数据库的业务进程有文件锁，防止重复启动；后台发送与本地归档由生命周期管理。

AI、额度执行、附件下载、任务取消、多模型编排、Markdown 渲染、NAS 保存/上传、B站订阅尚未接入。相关命令明确提示“开发中”，不会偷偷转发给模型或连接 NAS。`/ai on/off` 当前只保存后续 AI 模块的会话开关。

## 本轮确认的业务规则

1. 打卡窗口采用闭区间：早 `[04:30:00,12:00:00]`，晚 `[20:00:00,次日03:00:00]`；午夜至 03:00:00 归前一天。结束时刻的下一秒无效。
2. 文本不去首尾空格；带 @、引用、图片、额外标点不按纯文本打卡。按群独立统计。
3. 超过 300 秒的延迟事件不补签；最多容忍 30 秒的未来时钟偏差，超过则不计。
4. 示例文案每时段各两条，权重 90/10，可在配置修改；已收集 ID 不应随意重命名。重复打卡不抽取新文案、不增加收集。
5. 连续有效业务日期内，一天一次也连续，一天两次计两次；漏掉完整一天后重新开始。查询时会显示已经中断的当前连续数为 0，历史最长保留。
6. 已确认的后续 AI 规则：上海时间每日 00:00 重置；模型成功即扣一次，发送失败保留结果；`/context` 只对下一次请求有效，普通用户可读取本群至多 20 条历史。尚未实现，不能当作当前额度功能。
7. 获准的主动验收目标只有私聊 `2430023288` 与群 `1049251004`。

## 可用命令

普通用户：`/help`/`/帮助`、`/ping`、`/status`/`/状态`、`/echo`/`/复读`、`/checkin-get`/`/打卡查询`。

admin 及以上：`/watch add <群号>`、群内 `/checkin on/off`、群内 `/ai on/off`（仅开关）、`/system health`、`/system reload`。

superuser：`/admin add/remove <QQ>`、`/vip add/remove <QQ>`、`/ban <QQ>`、`/unban <QQ>`、`/watch remove <群号>`、`/bot on/off`。

群内斜杠命令无需 @。未知命令会给帮助提示。命令有每用户/会话 1 秒限速，echo 最多 1000 字符；superuser 的总开关恢复不受该限速影响。

`/system reload` 校验后刷新打卡文案、限制等配置；错误时保留旧配置。数据库/归档路径变更需要本地重启。没有开放任意 shell 或在线重启命令。

## 本地工具

```powershell
# 检查业务、编译与依赖
.\venv\Scripts\python.exe -X utf8 -m pytest -q
.\venv\Scripts\python.exe -X utf8 -m compileall -q bot
.\venv\Scripts\python.exe -m pip check

# 查看未决发送，仅输出元信息
.\venv\Scripts\python.exe -X utf8 tools\runtime.py outbox

# 确认客户端未收到某一条后，才允许重试
.\venv\Scripts\python.exe -X utf8 tools\runtime.py retry 123 --confirm-not-delivered

# 不再投递该条，允许同会话后续队列继续
.\venv\Scripts\python.exe -X utf8 tools\runtime.py suppress 123

# SQLite 在线备份；拒绝覆盖已有文件
.\venv\Scripts\python.exe -X utf8 tools\runtime.py backup artifacts\runtime\backups\manual.sqlite3
```

不确定发送不得直接批量改回 pending；应先核对客户端或协议记录。示例中的 123 是占位 ID。

默认数据库、归档和运行日志都在被 Git 忽略的 `artifacts/runtime/`。`.env`、`config.toml` 和策略运行文件仍被忽略；测试源代码已从原先的 `tests/` 忽略规则中移出。

## 验收证据与边界

- 本地测试：34 项通过，覆盖真实适配器事件归一化和入口、越权/持久化、未监听群、总开关、重复事件、所有时间边界、多群隔离、文案唯一计数、延迟/混排拒绝、连续统计、事务回滚、CQ 字符串作为纯文本发送、明确拒绝与不确定发送、发送前撤销权限、本地归档恢复/校验失败、备份和单实例锁。
- 依赖检查：通过；新增 `tzdata`，解决 Windows 没有 `Asia/Shanghai` 数据的问题。框架本身在 Python 3.13 上仍有 ForwardRef 弃用警告，未修改第三方库。
- 真实链路：服务重启后 Bot `3653125828` 已与 NapCat 重连。两个获准目标的验收提示均取得发送 message_id。用户实际执行私聊/群命令、开启群监听与打卡、发送晚安文本，并确认显示正常、同一晚间只累计一次；数据库也只有对应一条 night 记录。
- 群 `1049251004` 已由用户命令开启监听与打卡；测试打卡作为真实已提交记录保留，没有擅自清零。
- 尚未完成物理断电、磁盘损坏、长时间压力、真实 QQ 断线中发送的不确定性复现，也没有进行跨版本数据库升级演练。归档 SHA-256 通过不等同于已上传 NAS。

## 下一阶段

P3 接入官方 `openai-codex` 的 AsyncCodex，先落实可验证的工具权限、上下文边界、额度预留/结算、取消与超时。官方 SDK 与权限文档已核对：

- [Codex Python SDK](https://learn.chatgpt.com/docs/codex-sdk)
- [配置参考](https://learn.chatgpt.com/docs/config-file/config-reference)
- [权限配置与执行边界](https://learn.chatgpt.com/docs/permissions)

Agent 运行工具的隔离方式仍需确认并实测，不能把 read-only、Skill 或“禁止读取凭据”的文字当作已完成隔离。后续 NAS 普通用户绑定方式、B站 provider 和测试 UID/直播间仍待选择；不属于本轮已经交付的功能。上线部署按本次用户要求暂不执行。

# QQ 机器人可行性测试记录

> 这是 2026-10-15 探索阶段的历史记录。后续功能开发、当前运行状态及用户新增决策见 [2026-10-16 开发进度](development-status-2026-10-16.md)。

日期：2026-10-15，时区 Asia/Shanghai。对应方案：[qq-bot-design.md](qq-bot-design.md)。

## 1. 执行范围

读取《需求草案.md》第一节指定的探索要求、当前项目源码与历史聊天“配置并初始化 NapCatQQ 机器人”。启动现有 NoneBot 与 NapCat，在用户扫码后连接 QQ。没有直接实现全部业务功能，没有安装新依赖到原机器人 venv，没有创建 Git 提交。

本轮测试目标为用户指定的群 `1049251004` 和大号 `2430023288`，机器人账号 `3653125828`。文字、合成蓝色图片、小 txt 文件发送到这两个目标；测试事件单独采集到 `artifacts/feasibility/events.jsonl`，未提交 Git。

捕获文件保存的是 NoneBot 解析、补充引用后的事件及 `original_message`，不是逐字节原始 WebSocket 帧。现有 basic 插件本来就会输出所有收到消息的文本，正式开发时需要在日志前加监听范围与脱敏规则。

## 2. 运行环境与当前软件

| 项目 | 实际观察 |
| --- | --- |
| Python | 3.13，本机原 `venv` 继续用于机器人 |
| NoneBot | `nonebot2 2.5.0` |
| OneBot 适配器 | `nonebot-adapter-onebot 2.4.6` |
| NapCat | API 返回 `NapCat.Onebot 4.18.37`，OneBot v11 |
| WebUI | 本机 `6099`；Token 只交给用户完成登录，不写入设计文档 |
| NoneBot | `127.0.0.1:8080`，反向 WS `/onebot/v11/ws` |
| Codex 桌面配套 CLI | `0.162.0-alpha.17.2`，登录状态为 ChatGPT |
| 隔离 SDK | `openai-codex==0.162.1`，配套 CLI runtime `0.162.1` |
| NAS 正确目标 | `192.168.124.3:22`，使用 qqbot 账号测试 SFTP |

终端默认沙箱与浏览器自动化运行时均在启动时报告 `helper_unknown_error: setup refresh had errors`。终端操作经沙箱外执行完成；页面通过 Codex 打开工具交给用户，工具响应是 queued，未声称已自动控制页面。用户提供扫码登录，日志随后确认 QQ/WS 连接。

Windows 重定向日志出现 GBK 无法编码表情的错误，测试入口改用 `python -X utf8` 后继续运行。现有业务代码未因此改动。

## 3. 已完成真实链路测试

| 编号 | 测试 | 结果 | 边界 |
| --- | --- | --- | --- |
| QQ-01 | NapCat 扫码登录并反向连接 NoneBot | 通过 | WS accepted，Bot connected |
| QQ-02 | 私聊 `/ping` | 已收到并走基础命令处理 | 用户端 pong 如需单独验收可再确认 |
| QQ-03 | 群内 `/ping` 无需 @ | 通过 | 捕获 `to_me=false` 的命令事件并执行 ping |
| QQ-04 | 群内 @机器人 | 通过 | `original_message` 有 at 段，处理后 `to_me=true` |
| QQ-05 | 单条 `文字/image/文字` 接收 | 通过 | 私聊消息 `1375441801`，段序保留 |
| QQ-06 | 引用单图与引用混排消息 | 通过 | `event.reply` 中可取得原消息与附件元信息 |
| QQ-07 | 私聊、群文件接收及引用 | 通过 | 私聊 `332501693`、群 `2025704574` 均在引用中可解析 |
| QQ-08 | 向两个目标发送文字 | API 成功 | 返回 message_id；没有向其他目标主动发送测试消息 |
| QQ-09 | 向两个目标发送 `text/image/text` | API 成功 | 用户已完成接收测试；未单独记录所有客户端的混排视觉差异 |
| QQ-10 | 私聊、群上传 txt 文件 | API 成功，用户确认可打开 | 各一次 58 字节合成文本文件 |
| MEDIA-01 | 收到引用后才下载图片 | 通过 | 下载 1752 字节、有效图片签名；未在接收时自动下载全部图片 |
| MEDIA-02 | 引用群文件后通过 URL 下载 | 通过 | 4 字节，已计算 SHA-256 |
| MEDIA-03 | 无直链的私聊文件通过 `get_file` 获取 | 通过 | 返回 file/url/file_size/file_name，取得本地 4 字节文件 |

关键实际差异：群文件会出现 `group_upload` notice 和普通 file 消息，标识并不相同；不能把 notice 与 message 当作两个独立待保存文件。引用获取成功不代表所有旧消息或所有群文件都永久可用。

MEDIA-01 下载哈希：`00747adbaadfe9f71b440682fcf4209897a9c9ea0e00c31fc2de6105da4d00c3`。

MEDIA-02 下载哈希：`0ffe1abd1a08215353c233d6e009613e95eec4253832a761af28ff37ac5a150c`。

## 4. Codex Python SDK 实测

在 `artifacts/feasibility/sdk-venv` 中安装官方 SDK，没有修改原 `pyproject.toml` 或 bot 依赖。SDK 使用自己固定版本的运行时、现有本地登录与一个只读临时工作目录；测试内容全部为合成文本和合成蓝色图片。

| 编号 | 测试 | 结果 |
| --- | --- | --- |
| AI-01 | `gpt-6-luna` 返回固定文本 | completed，`QQ_BOT_SDK_OK`；记录用时 6.60 秒 |
| AI-02 | `LocalImageInput` 传合成蓝色 PNG | 返回 `blue` |
| AI-03 | JSON Schema 约束输出 | 返回 `{color,memo}` 合法 JSON |
| AI-04 | 同一 SDK thread 的下一轮读取记忆标记 | 返回正确 `QQ_CTX_314159` |
| AI-05 | 下一轮指定 `gpt-6.1-sol` | completed，保持正确图片颜色和上下文标记 |

AI-02—05 两轮合计记录用时 11.77 秒，仅为单次小样本，不是延迟 SLA。模型目录返回七个模型，但只对上述两种做了真实请求。

启动器曾把 `launch_args_override` 误作额外参数、并使用了不存在的枚举值；已按安装源码修正为正常 pinned runtime 启动和 `ApprovalMode.deny_all`。结果序列化也已修正为 `.value`。这些是探针调试记录，不是 SDK 服务失败。探针目前未实现外层强制终止机制，复跑时应由操作者管理执行时间。

**结论**：本机“Python SDK + 已有订阅登录 + Luna”成立。图片、结构化输出、上下文和按轮切模型成立。主 Agent 的自动高级任务委派、独立 Skill 载入、文档解析、生成附件、取消恢复、长稳运行及 Linux 上的认证尚未实测。

官方依据：[Python SDK](https://learn.chatgpt.com/docs/codex-sdk)、[认证](https://learn.chatgpt.com/docs/auth)。

## 5. 数据层合成实验

`storage_probe.py` 仅使用合成数据与独立数据库，没有迁移真实聊天。

| 编号 | 测试 | 结果 |
| --- | --- | --- |
| STORE-01 | 写 JSON 前模拟中断 | 3000 条全部可恢复，无重复 |
| STORE-02 | JSON 重命名后、数据库提交前中断 | 源行仍存在，恢复后无丢失无重复 |
| STORE-03 | 删除旧行的事务提交前中断 | 回滚恢复正确 |
| STORE-04 | 数据库提交后中断 | 清单与 JSON 完整，最终窗口 1000、归档 2000 |
| QUOTA-01 | 32 请求并发争用 10 次额度 | 恰好成功 10 次，剩余 0，无负值 |
| CHECK-01 | 9 个早晚边界案例 | 全部通过；使用开始包含/结束不包含的拟定口径 |

没有验证物理断电、磁盘损坏、多进程导出、ZIP 的 10 MB 边界、大规模存储性能、完整配额退款或正式打卡功能。完整业务仍需按设计实施。

## 6. NAS 事实与纠正

草案最初给出的地址为 `.2`，用户后更正为 `.3`。对 `192.168.124.2` 的 445 可达、SMB2 认证失败和 22 拒绝连接，全部属于错误目标，**不得用来判断真实 NAS 的账号密码、SMB 稳定性或 SSH 配置**。没有向 `.2` 上传文件或执行远程 shell。

更正为 `192.168.124.3` 后：

- qqbot 密码认证成功，SFTP 通道打开。
- 登录时看到的虚拟目录是 `/`，包含 `home` 和若干共享目录；未进入共享目录读取内容。
- `/home` 下创建一个 UUID 命名的测试目录，上传 `probe.part`，改名 `probe.txt`，回读核验，再仅删除本次创建的文件与空目录。
- 实际往返 25 字节，SHA-256 一致：`2b089a0c9eab7cf3d0745323ba5bd225dabe9de79d60b66743d775f75964f04a`。
- 测试目录和文件已清理；没有上传用户的 `1.txt`，没有删除 NAS 原有文件。
- 用户另在 qqbot 个人文件夹根目录创建 `QQBotRootCheck`；SFTP `/home/QQBotRootCheck` 确认存在且为空，并按约定删除。界面个人文件夹与该账号 SFTP `/home` 的对应关系已核实。
- 主机密钥指纹 `SHA256:kj9fo+ZhMhPtETTL6RatEogfIWJ+y639vZCS+locLXU`，采用本项目本地首次信任记录，后续连接固定此 key；尚未通过另一可信渠道独立核验。

**结论**：SFTP 的认证、微型上传、同目录重命名、回读校验与清理可行，qqbot 个人目录映射已确认。大文件、断线续传、NAS 重启、非管理员用户 SFTP 权限、长期凭据存储尚未验证。当前 qqbot 被用户设为管理员，不能据此推出所有普通 NAS 用户都可自助用 SFTP。

官方依据：[UGOS 终端机/SFTP](https://support.ugnas.com/detail/article/zh-CN/84)、[Paramiko 主机密钥](https://docs.paramiko.org/en/stable/api/client.html)、[SFTP 操作](https://docs.paramiko.org/en/stable/api/sftp.html)。

## 7. 尚未验证的功能

Markdown/公式/表格渲染、原生 QQ Markdown、卡片/合并转发/语音/公告的逐项兼容；完整多模型任务编排；持久化角色/封禁/总开关；完整打卡；真实归档封包上传；用户 NAS 凭据绑定与权限隔离；B站动态和开播 provider；Ubuntu 26.04 同版本部署与长稳运行。

B站目前只完成官方能力边界查阅，未选择或调用逆向接口，没有订阅任何 UID。要继续实测，需要选择 provider 并提供测试 UID/直播间。

## 8. 文件与复跑

可审查脚本在 `tools/feasibility/`。本地详细结果在 `artifacts/feasibility/`，已忽略 Git：`codex-probe.json`、`codex-media-probe.json`、`download-probe.json`、`storage-probe.json`、`sftp-probe.json`、`sftp-root-mapping.json` 及指定目标的事件/接口结果。

额外事件捕获已于本轮测试结束后关闭；原始 `python -X utf8 -m bot` 入口已恢复并再次与 NapCat 连通。后台进程信息见本地 `current-processes.json`；NapCat 与基础机器人继续在线。

当前结果来自本机及小样本，不等同于全部功能完成。复跑说明见 [tools/feasibility/README.md](../tools/feasibility/README.md)。

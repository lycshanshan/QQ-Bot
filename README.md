# QQ Bot

这是一个通过 OneBot v11 接入 NapCatQQ 的 NoneBot2 机器人。已接入权限与消息存储、早晚打卡、订阅登录的多模型 AI、Markdown 图片输出、NAS 自身归档和 B站直播订阅。用户 NAS 保存与B站动态按当前决定暂缓。

数据流：`NapCat（WebSocket 客户端） -> 本项目（反向 WebSocket 服务端）`。

## Windows 本地运行

1. 启动 `NapCat/NapCat.52230.Shell/napcat.bat`，在 WebUI 的“QQ 登录”页面完成扫码。
2. 创建虚拟环境并安装项目：

   ```powershell
   python -m venv venv
   .\venv\Scripts\python.exe -m pip install -U pip
   .\venv\Scripts\python.exe -m pip install -e ".[test]"
   Copy-Item .env.example .env
   ```

3. 启动机器人：

   ```powershell
   .\venv\Scripts\python.exe -X utf8 -m bot
   ```

4. 在 NapCat WebUI 进入“网络配置” -> “新建” -> “WebSocket 客户端”：

   - URL：`ws://127.0.0.1:8080/onebot/v11/ws`
   - 消息格式：`array`
   - Token：与 `.env` 中 `ONEBOT_ACCESS_TOKEN` 一致；两端都在本机且仅监听回环地址时可以都留空
   - 勾选“保存时启用”

5. 用另一个 QQ 向机器人发送 `/ping`、`/echo hello`、`/status` 或 `/help`。

## 代码结构

- `bot/__main__.py`：加载环境、注册 OneBot v11 适配器并启动服务。
- `bot/plugins/basic.py`：适配器入口与后台任务生命周期。
- `bot/config/`、`config.example.toml`：业务配置及校验。
- `bot/domain/`、`bot/services/`：消息规范、权限、命令和打卡业务。
- `bot/ai/`、`agent-workspace/`：SDK、上下文、额度、高级任务及本机任务目录。
- `bot/media/`、`bot/integrations/`：附件、Markdown 渲染、SFTP 和直播接口。
- `bot/repositories/`、`bot/workers/`：SQLite、可靠发送及本地 JSON 归档。
- `tests/`：业务回归和适配器入口测试。
- `tools/runtime.py`：本地备份与未决发送恢复。
- `.env.example`：本地监听地址、端口和鉴权配置。
- `docs/linux-deployment.md`：Ubuntu Server 部署步骤。

## 验证

```powershell
.\venv\Scripts\python.exe -m compileall -q bot
.\venv\Scripts\python.exe -m pytest
```

NapCat WebUI 与机器人端口不同：WebUI 默认是 `6099`，本项目默认是 `8080`。

## 功能设计与可行性验证

- [功能设计与分阶段开发方案](docs/qq-bot-design.md)：已确认决策、模块方案、命令和待审核参数。
- [2026-10-15 可行性测试记录](docs/feasibility-report-2026-10-15.md)：实际收发、SDK、数据层和 NAS 测试，以及尚未验证的范围。
- [独立探针说明](tools/feasibility/README.md)：复跑方式；探针不由默认机器人入口加载。

当前实现和完整命令见 [2026-10-16 开发进度](docs/development-status-2026-10-16.md)。此前 [P1/P2 记录](docs/development-status-2026-10-15.md) 保留为历史快照。Windows 使用 `-X utf8` 可避免重定向日志时表情字符触发 GBK 编码错误。

首次监听群：由 superuser 私聊 `/watch add <群号>`；然后在该群执行 `/checkin on`。群命令不需要 @。可复制 `config.example.toml` 为 `config.toml` 修改文案和限制，通过 `/system reload` 生效；数据库与归档路径修改需要重启。


## AI、渲染与直播

私聊默认启用 AI；群内先由管理员 `/ai on`，再 @机器人或 `/ask <问题>`。`/新会话` 清空自己的私聊上下文；`/上下文 group 20` 为下一次群请求携带限量历史；`/额度`、`/取消` 查看额度和取消任务。

主模型默认 `gpt-6-luna`，高级任务默认 `gpt-6.1-sol`。按用户当前选择，Agent 在 Windows 本机使用 `full_access` 执行命令，工作文件位于 `agent-workspace/`。SDK 复用本机已有 ChatGPT 登录。

首次安装公式资源：

```powershell
.\venv\Scripts\python.exe -X utf8 tools\install_renderer_assets.py
```

图片渲染使用已安装的 Edge/Chrome，不额外下载 Chromium。直播订阅需 VIP 及以上：`/subscribe bili-live <直播间ID>`、`/unsubscribe bili-live <直播间ID>`；`/订阅列表` 查看状态。首次建立基线，不发送旧开播消息。

NAS 自身归档配置见 `.env.example`；凭据只存本地 `.env`。默认达到 10,000,000 字节或等待 24 小时后上传已导出的 JSON 封包，远端回读校验通过后才可清理本地已封包 JSON/ZIP；最近消息与清单保留。用户保存和动态订阅尚未启用。本轮不涉及上线部署。

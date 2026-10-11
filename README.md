# QQ Bot

这是一个通过 OneBot v11 接入 NapCatQQ 的最小可扩展机器人。NapCat 负责登录 QQ 和收发协议事件；本项目使用 NoneBot2 处理命令。

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
   .\venv\Scripts\python.exe -m bot
   ```

4. 在 NapCat WebUI 进入“网络配置” -> “新建” -> “WebSocket 客户端”：

   - URL：`ws://127.0.0.1:8080/onebot/v11/ws`
   - 消息格式：`array`
   - Token：与 `.env` 中 `ONEBOT_ACCESS_TOKEN` 一致；两端都在本机且仅监听回环地址时可以都留空
   - 勾选“保存时启用”

5. 用另一个 QQ 向机器人发送 `/ping`、`/echo hello`、`/status` 或 `/help`。

## 代码结构

- `bot/__main__.py`：加载环境、注册 OneBot v11 适配器并启动服务。
- `bot/plugins/basic.py`：消息日志和基础命令。
- `.env.example`：本地监听地址、端口和鉴权配置。
- `docs/linux-deployment.md`：Ubuntu Server 部署步骤。

## 验证

```powershell
.\venv\Scripts\python.exe -m compileall -q bot
.\venv\Scripts\python.exe -m pytest
```

NapCat WebUI 与机器人端口不同：WebUI 默认是 `6099`，本项目默认是 `8080`。


# Ubuntu 26.04 Server 部署

本文以 NapCat 官方当前推荐的 Linux 一键安装和 systemd 为主。官方文档声明安装器支持使用 `apt` 的 amd64/arm64 系统，但没有单独点名 Ubuntu 26.04；正式迁移前应先在同版本干净服务器做一次前台启动和登录验证。

## 1. 创建专用用户并安装系统环境

```bash
sudo adduser --disabled-password --gecos '' qqbot
sudo apt update
sudo apt install -y curl git python3 python3-venv xvfb xauth jq
sudo -iu qqbot
```

不要用 root 长期运行 QQ、NapCat 或 Python 机器人。以下命令均以 `qqbot` 用户执行。

## 2. 安装 NapCat 和 QQ

```bash
cd ~
curl -fL --connect-timeout 20 --max-time 1800 \
  -o napcat.sh https://raw.githubusercontent.com/NapNeko/NapCat-Installer/main/script/install.sh
bash napcat.sh --docker n --cli y --github-proxy 0
```

安装器会把内容放在当前用户的 `~/Napcat`。安装后先运行 `napcat`，按菜单启动服务并查看 WebUI 地址。若安装器在 Ubuntu 26.04 上报告 Xvfb 或 QQ 依赖不可用，优先改用官方 Docker 方案，而不是强行安装旧发行版软件包。

无公网桌面时，建议从自己的电脑建立 SSH 隧道访问 WebUI：

```bash
ssh -L 6099:127.0.0.1:6099 qqbot@服务器地址
```

然后在本机打开 `http://127.0.0.1:6099/webui/`，使用服务日志或下列文件中的 token 登录：

```text
/home/qqbot/Napcat/opt/QQ/resources/app/app_launcher/napcat/config/webui.json
```

进入“QQ 登录” -> “QRCode”扫码。首次登录后 WebUI 会刷新 token，并要求修改 WebUI 密码。不要把 WebUI 的 `6099` 端口直接暴露到公网。

## 3. 部署 Python 项目

把仓库克隆或复制到 `/home/qqbot/QQ_Bot` 后：

```bash
cd /home/qqbot/QQ_Bot
python3 -m venv venv
./venv/bin/python -m pip install -U pip
./venv/bin/python -m pip install -e '.[test]'
cp .env.example .env
./venv/bin/python -m pytest
./venv/bin/python -m bot
```

保持默认 `HOST=127.0.0.1` 和 `PORT=8080`。如果配置鉴权，使用 `python3 -c 'import secrets; print(secrets.token_urlsafe(32))'` 生成 token，填入 `.env` 的 `ONEBOT_ACCESS_TOKEN`，并在 NapCat 网络配置中填写同一个值。

## 4. 配置 NapCat 到机器人

在 NapCat WebUI 的“网络配置”中新建“WebSocket 客户端”：

- URL：`ws://127.0.0.1:8080/onebot/v11/ws`
- 消息格式：`array`
- Token：与 `/home/qqbot/QQ_Bot/.env` 一致
- 勾选“保存时启用”

机器人启动后，NapCat 会自动连接。不要在安全组或 UFW 中开放 `8080`，因为两个服务位于同一台机器。

## 5. 配置 systemd

先退出到有 sudo 权限的管理用户，为 Python 机器人创建 `/etc/systemd/system/qq-bot.service`：

```ini
[Unit]
Description=QQ Bot (NoneBot2)
Wants=network-online.target
After=network-online.target napcat.service

[Service]
Type=simple
User=qqbot
WorkingDirectory=/home/qqbot/QQ_Bot
ExecStart=/home/qqbot/QQ_Bot/venv/bin/python -m bot
Restart=on-failure
RestartSec=5
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
```

NapCat 官方的一键安装路径对应 `/etc/systemd/system/napcat.service`：

```ini
[Unit]
Description=NapCat Shell
Wants=network-online.target
After=network-online.target

[Service]
Type=simple
User=qqbot
WorkingDirectory=/home/qqbot
ExecStart=/usr/bin/xvfb-run -a /home/qqbot/Napcat/opt/QQ/qq --no-sandbox
Restart=on-failure
RestartSec=5
TimeoutStopSec=20
LimitCORE=0

[Install]
WantedBy=multi-user.target
```

启用并检查：

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now napcat.service qq-bot.service
systemctl status napcat.service qq-bot.service
journalctl -u napcat.service -u qq-bot.service -b -f
```

## 6. 更新与备份

- Python 代码：先拉取代码，再运行 `./venv/bin/python -m pip install -e '.[test]'`，测试通过后 `sudo systemctl restart qq-bot`。
- NapCat：由安装 NapCat 的同一用户重新运行安装脚本并加 `--force`；官方说明配置和插件会保留。
- 备份：至少备份仓库中的 `.env`（安全保存，不进 Git），以及 `~/Napcat/opt/QQ/resources/app/app_launcher/napcat/config` 和 QQ 登录数据。
- 更新前先记录当前 QQ 和 NapCat 版本；QQ 更新可能要求 NapCat 同步适配。

## 7. Docker 备选方案

如果 Ubuntu 26.04 原生依赖不兼容，可用官方文档给出的 `mlikiowa/napcat-docker:latest` 镜像。生产环境应固定具体发布标签、挂载 QQ/config/plugins 三个数据卷，并只把 `6099`、`3001` 绑定到 `127.0.0.1`。Python 机器人可继续作为 systemd 服务运行；若两者都容器化，则把反向 WS 地址改为容器服务名，并务必设置 token。

## 参考

- [NapCat Shell 安装](https://napneko.github.io/guide/boot/Shell)
- [NapCat WebUI/OneBot 配置](https://napneko.github.io/config/basic)
- [NapCat Linux systemd](https://napneko.github.io/guide/boot/Linux_startup)
- [NoneBot OneBot v11 连接配置](https://github.com/nonebot/adapter-onebot/blob/2f5930d4f6c7fb3a82cf3201639e8f0b0a6c124e/website/docs/guide/setup.md)

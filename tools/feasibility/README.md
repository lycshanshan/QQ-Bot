# QQ Bot 可行性探针

这些脚本是 2026-10-15 设计探索的独立实验，不会被 `python -m bot` 自动加载。不要将 `artifacts/`、密码、登录缓存或聊天捕获提交 Git。

## 已有环境

- `venv/Scripts/python.exe`：原机器人环境，用于 live_bot、下载和数据层实验。
- `artifacts/feasibility/sdk-venv/Scripts/python.exe`：本轮隔离环境，安装 `openai-codex==0.162.1`、`pysmb==1.2.15`、`paramiko==5.0.0`；原项目依赖未修改。
- NAS 正确地址为 `192.168.124.3`，早期 `.2` 为用户已纠正的草案地址。

## 复跑命令

在项目根目录，以下命令以 PowerShell 为例。SDK 测试会使用当前登录并消耗少量账户额度，输入仅为合成数据：

```powershell
.\venv\Scripts\python.exe -X utf8 tools\feasibility\storage_probe.py
.\artifacts\feasibility\sdk-venv\Scripts\python.exe -X utf8 tools\feasibility\codex_probe.py
.\artifacts\feasibility\sdk-venv\Scripts\python.exe -X utf8 tools\feasibility\codex_media_probe.py
```

media probe 依赖先前生成的 `artifacts/feasibility/transport-test.png`；`queue_media.py` 会生成该图片，但也会排队真实 QQ 发送请求。不要为单纯准备图片而在活动测试入口旁无意排队发送。

NAS 默认只认证与目录发现；凭据从用户自己的草案读取，不在代码中硬编码：

```powershell
.\artifacts\feasibility\sdk-venv\Scripts\python.exe -X utf8 tools\feasibility\sftp_probe.py --draft 'D:\SysFolders\Downloads\UGREEN_Drive\需求草案.md' --host 192.168.124.3
```

在已确认的个人目录内做微型往返与清理，增加 `--root /home`。首次连接主机 key 记录在项目忽略目录中，之后固定；正式部署应独立核对指纹。不要把错误地址上的早期 `nas-probe.json` 作为真实 NAS 的结果。

## 真实 QQ 入口

只在已经得到指定目标测试授权时运行：

```powershell
.\venv\Scripts\python.exe -X utf8 tools\feasibility\live_bot.py
```

它与正式 bot 共用 8080 端口，不能同时启动。单独事件采集仅包含固定群 `1049251004` 及大号 `2430023288`；原 basic 插件的普通消息日志仍保留原行为。

本地 `artifacts/feasibility/requests/*.json` 为受信任操作者的文件队列，不提供网络控制端点；结果写入 `responses/`。响应文件存在时同名请求不会重复执行。支持的操作和目标有白名单；这套临时控制器不用于生产，不是面向不可信输入的通用 API。

`queue_media.py` 向两个指定目标排队蓝色图片图文和小文件测试。`download_probe.py` 仅在操作者运行时下载已捕获的引用附件，限制 2 MiB，执行 URL/重定向后缀检查；不是完整生产 SSRF 防护实现，尚未包含 DNS/IP 钉扎。URL 失效后应重新发消息/刷新标识，不能无限重试。

测试结束应退出 live_bot、恢复 `python -X utf8 -m bot`，停止额外捕获；详细结果继续留在本机供核对。

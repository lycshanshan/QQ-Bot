# Agent 工作目录

每个请求在此目录下获得随机 ID 子目录，内含 `input/`、`output/`、`scratch/`。聊天输入、下载附件和生成结果都按请求组织。

根据用户 2026-10-16 的选择，机器人和 Agent 都在 Windows 本机运行，SDK 使用 `full_access`，允许执行本机命令；没有配置 WSL、容器或操作系统沙箱。该目录提供文件组织，不构成宿主访问隔离。

后端仍分别管理用户/群上下文、权限、配额、队列和 QQ 收件人。输出文件只接受当前任务 `output/` 中的真实文件，并拒绝路径穿越、符号链接、junction 和硬链接。

`bot/ai/workspace.py` 中的 permission_overrides 仅供历史隔离探针使用，实际 SDK Provider 不调用它。目录内任务数据被 Git 忽略，只有本说明纳入版本管理。

from __future__ import annotations

from datetime import UTC, datetime

from nonebot import logger, on_command, on_message
from nonebot.adapters.onebot.v11 import Bot, Message, MessageEvent
from nonebot.params import CommandArg


STARTED_AT = datetime.now(UTC)

message_log = on_message(priority=1, block=False)
help_command = on_command("help", aliases={"帮助"}, priority=10, block=True)
ping_command = on_command("ping", priority=10, block=True)
echo_command = on_command("echo", priority=10, block=True)
status_command = on_command("status", aliases={"状态"}, priority=10, block=True)


@message_log.handle()
async def log_message(event: MessageEvent) -> None:
    logger.info(
        "收到消息 session={} user={} text={!r}",
        event.get_session_id(),
        event.get_user_id(),
        event.get_plaintext(),
    )


@help_command.handle()
async def show_help() -> None:
    await help_command.finish(
        "可用命令：\n"
        "/ping - 检查机器人是否在线\n"
        "/echo <内容> - 原样回复文本\n"
        "/status - 查看连接和运行时间\n"
        "/help - 显示本帮助"
    )


@ping_command.handle()
async def ping() -> None:
    await ping_command.finish("pong")


@echo_command.handle()
async def echo(argument: Message = CommandArg()) -> None:
    text = argument.extract_plain_text().strip()
    if not text:
        await echo_command.finish("用法：/echo <内容>")
    await echo_command.finish(text)


@status_command.handle()
async def status(bot: Bot, event: MessageEvent) -> None:
    uptime = datetime.now(UTC) - STARTED_AT
    total_seconds = int(uptime.total_seconds())
    hours, remainder = divmod(total_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    await status_command.finish(
        "机器人运行正常\n"
        f"QQ：{bot.self_id}\n"
        f"会话：{event.get_session_id()}\n"
        f"运行时间：{hours:02d}:{minutes:02d}:{seconds:02d}"
    )


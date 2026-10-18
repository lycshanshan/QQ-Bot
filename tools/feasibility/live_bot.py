"""Temporary test harness. Captures ONLY the explicitly selected test targets.

Run with the existing bot venv. File requests are local, not a network API.
No AI integration, NAS operation, or general command execution is exposed.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

import nonebot
from dotenv import load_dotenv
from nonebot import on_message, on_notice
from nonebot.adapters.onebot.v11 import Adapter, Bot, Event

OUT = ROOT / "artifacts/feasibility"
REQUESTS = OUT / "requests"
RESPONSES = OUT / "responses"
TEST_GROUP = 1049251004
TEST_USER = 2430023288
SEEN_MESSAGES: set[int] = set()
SEEN_FILES: set[str] = set()


def remember(data: dict) -> None:
    if data.get("message_id"):
        SEEN_MESSAGES.add(int(data["message_id"]))
    for segment in data.get("original_message", data.get("message", [])):
        if segment.get("type") in {"image", "file"}:
            for key in ("file_id", "file"):
                value = segment.get("data", {}).get(key)
                if value:
                    SEEN_FILES.add(str(value))


def write_json(path: Path, value: object) -> None:
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    temporary.replace(path)


def in_scope(data: dict) -> bool:
    if data.get("group_id") is not None:
        return data["group_id"] == TEST_GROUP
    return data.get("user_id") == TEST_USER


async def capture(event: Event) -> None:
    data = json.loads(event.json())
    if not in_scope(data):
        return
    remember(data)
    with (OUT / "events.jsonl").open("a", encoding="utf-8") as stream:
        stream.write(json.dumps({"captured_at": time.time(), "event": data}, ensure_ascii=False) + "\n")


def validate(action: str, params: dict) -> None:
    if action in {"get_login_info", "get_version_info", "get_status"}:
        if params:
            raise ValueError("Unexpected parameters")
        return
    if action in {"send_private_msg", "upload_private_file"}:
        if params.get("user_id") != TEST_USER:
            raise ValueError("Private target is not the approved test user")
    elif action in {"send_group_msg", "upload_group_file"}:
        if params.get("group_id") != TEST_GROUP:
            raise ValueError("Group target is not the approved test group")
    elif action == "get_msg":
        if int(params.get("message_id", 0)) not in SEEN_MESSAGES:
            raise ValueError("Message is outside the observed test scope")
    elif action in {"get_file", "get_image", "get_private_file_url"}:
        reference = params.get("file_id", params.get("file"))
        if str(reference) not in SEEN_FILES:
            raise ValueError("File is outside the observed test scope")
    else:
        raise ValueError("Action not allowed by this temporary harness")
    if action.startswith("upload_"):
        path = Path(params["file"]).resolve()
        if not path.is_relative_to(OUT.resolve()) or not path.is_file():
            raise ValueError("Only local feasibility artifacts may be uploaded")
    if action.startswith("send_"):
        for segment in params.get("message", []):
            if segment.get("type") not in {"text", "image", "face", "reply"}:
                raise ValueError("Unsupported test output segment")
            if segment.get("type") == "image":
                uri = str(segment["data"]["file"])
                parsed = urllib.parse.urlparse(uri)
                path = Path(urllib.request.url2pathname(parsed.path)).resolve()
                if parsed.scheme != "file" or parsed.netloc or not path.is_relative_to(OUT.resolve()) or not path.is_file():
                    raise ValueError("Only local feasibility images may be sent")


async def worker() -> None:
    while True:
        for path in sorted(REQUESTS.glob("*.json")):
            response = RESPONSES / path.name
            if response.exists():
                continue
            bots = nonebot.get_bots()
            if not bots:
                break
            try:
                request = json.loads(path.read_text(encoding="utf-8-sig"))
                action, params = request["action"], request.get("params", {})
                validate(action, params)
                bot = next(iter(bots.values()))
                result = await asyncio.wait_for(bot.call_api(action, **params), timeout=30)
                if action.startswith("send_") and isinstance(result, dict) and result.get("message_id"):
                    SEEN_MESSAGES.add(int(result["message_id"]))
                write_json(response, {"ok": True, "action": action, "result": result})
            except Exception as exc:
                write_json(response, {"ok": False, "error": str(exc), "error_type": type(exc).__name__})
        await asyncio.sleep(0.5)


def main() -> None:
    for path in (OUT, REQUESTS, RESPONSES):
        path.mkdir(parents=True, exist_ok=True)
    history = OUT / "events.jsonl"
    if history.exists():
        for line in history.read_text(encoding="utf-8").splitlines():
            data = json.loads(line)["event"]
            if in_scope(data):
                remember(data)
    load_dotenv()
    nonebot.init(driver="~fastapi", host="127.0.0.1", port=8080,
                 onebot_access_token=os.getenv("ONEBOT_ACCESS_TOKEN", "").strip() or None)
    driver = nonebot.get_driver()
    driver.register_adapter(Adapter)
    nonebot.load_plugins("bot/plugins")
    on_message(priority=0, block=False).append_handler(capture)
    on_notice(priority=0, block=False).append_handler(capture)
    task: asyncio.Task | None = None

    @driver.on_startup
    async def start() -> None:
        nonlocal task
        task = asyncio.create_task(worker())

    @driver.on_shutdown
    async def stop() -> None:
        if task:
            task.cancel()

    @driver.on_bot_connect
    async def connected(bot: Bot) -> None:
        write_json(OUT / "connected.json", {"bot_id": bot.self_id, "time": time.time()})

    nonebot.run()


if __name__ == "__main__":
    main()

from __future__ import annotations

import os

import nonebot
from dotenv import load_dotenv
from nonebot.adapters.onebot.v11 import Adapter as OneBotV11Adapter


def main() -> None:
    """Start NoneBot's reverse WebSocket server for NapCat."""
    load_dotenv()

    access_token = os.getenv("ONEBOT_ACCESS_TOKEN", "").strip()
    nonebot.init(
        driver="~fastapi",
        host=os.getenv("HOST", "127.0.0.1"),
        port=int(os.getenv("PORT", "8080")),
        log_level=os.getenv("LOG_LEVEL", "INFO"),
        onebot_access_token=access_token or None,
    )

    driver = nonebot.get_driver()
    driver.register_adapter(OneBotV11Adapter)
    nonebot.load_plugins("bot/plugins")
    nonebot.run()


if __name__ == "__main__":
    main()


from __future__ import annotations

import os
import sys

import nonebot
from dotenv import load_dotenv
from nonebot.adapters.onebot.v11 import Adapter as OneBotV11Adapter
from nonebot.log import default_filter, default_format

from bot.config import ROOT, load_settings
from bot.services.instance import InstanceLock


def privacy_filter(record: dict) -> bool:
    """Filter at the sink: NoneBot replaces its global patcher during init()."""
    source = record['file'].path.replace('\\', '/')
    if source.endswith('/nonebot/message.py'):
        # This happens before our scope check, so omit framework event/handler logs entirely.
        return False
    if '/nonebot/adapters/onebot/' in source:
        record['message'] = 'OneBot protocol event (payload omitted)'
        record['exception'] = None
    if record['function'] == 'init' and 'Config' in record['message']:
        record['message'] = 'NoneBot configuration loaded (values omitted)'
    return default_filter(record)


def main() -> None:
    """Start one local business process connected to NapCat via reverse WebSocket."""
    load_dotenv(ROOT / '.env')
    settings = load_settings()
    instance = InstanceLock(settings.database.with_suffix('.lock'))
    nonebot.logger.remove()
    nonebot.logger.add(sys.stdout, level=0, diagnose=False, filter=privacy_filter, format=default_format)
    try:
        access_token = os.getenv('ONEBOT_ACCESS_TOKEN', '').strip()
        nonebot.init(driver='~fastapi', host=os.getenv('HOST', '127.0.0.1'),
                     port=int(os.getenv('PORT', '8080')), log_level=os.getenv('LOG_LEVEL', 'INFO'),
                     onebot_access_token=access_token or None)
        driver = nonebot.get_driver()
        driver.register_adapter(OneBotV11Adapter)
        if nonebot.load_plugin('bot.plugins.basic') is None:
            raise RuntimeError('Business plugin failed to load')
        nonebot.run()
    finally:
        instance.close()


if __name__ == '__main__':
    main()

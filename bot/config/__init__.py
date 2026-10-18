from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import math
import tomllib
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[2]
SUPERUSER = '2430023288'


@dataclass(frozen=True)
class Copy:
    id: str
    period: str
    text: str
    weight: float


@dataclass(frozen=True)
class Settings:
    database: Path
    archives: Path
    timezone: str = 'Asia/Shanghai'
    message_window: int = 1000
    archive_batch: int = 1000
    checkin_max_delay: int = 300
    echo_max_chars: int = 1000
    command_interval: float = 1.0
    send_interval: float = 1.0
    copies: tuple[Copy, ...] = ()
    ai_enabled: bool = True
    ai_concurrency: int = 2
    ai_queue_limit: int = 20
    ai_timeout: int = 120
    ai_queue_timeout: int = 300
    ai_history_limit: int = 20
    ai_daily_requests: int = 500
    ai_daily_tokens: int = 2000000
    ai_input_chars: int = 24000
    ai_policy: Path = ROOT / 'config/policy.toml'
    agent_workspaces: Path = ROOT / 'agent-workspace'
    bili_enabled: bool = True


def load_settings(path: Path | None = None) -> Settings:
    path = path or ROOT / 'config.toml'
    data = tomllib.loads(path.read_text(encoding='utf-8')) if path.exists() else {}
    unknown = set(data) - {'storage', 'bot', 'checkin', 'ai', 'subscriptions'}
    if unknown:
        raise ValueError(f'Unknown config sections: {sorted(unknown)}')
    storage, bot, checkin = (data.get(k, {}) for k in ('storage', 'bot', 'checkin'))
    ai = data.get('ai', {})
    subscriptions=data.get('subscriptions',{})
    if set(subscriptions)-{'enabled'} or type(subscriptions.get('enabled',True)) is not bool:
        raise ValueError('Invalid subscriptions configuration')
    for section, allowed in ((storage, {'database', 'archives', 'message_window', 'archive_batch'}),
                             (bot, {'timezone', 'echo_max_chars', 'command_interval', 'send_interval'}),
                             (checkin, {'max_delay_seconds', 'copies'}),
                             (ai, {'enabled','concurrency','queue_limit','timeout_seconds','queue_timeout_seconds','history_limit','daily_requests','daily_tokens','input_chars'})):
        if set(section) - allowed:
            raise ValueError(f'Unknown config keys: {sorted(set(section) - allowed)}')
    def positive(section: dict, key: str, default: int) -> int:
        value = section.get(key, default)
        if type(value) is not int or value <= 0:
            raise ValueError(f'{key} must be a positive integer')
        return value
    def seconds(key: str, default: float) -> float:
        value = bot.get(key, default)
        if type(value) not in (float, int) or not math.isfinite(value) or value < 0:
            raise ValueError(f'{key} must be finite and nonnegative')
        return float(value)
    zone = bot.get('timezone', 'Asia/Shanghai')
    if zone != 'Asia/Shanghai':
        raise ValueError('Business timezone must be Asia/Shanghai')
    ZoneInfo(zone)
    raw_copies = checkin.get('copies', [
        {'id': 'morning_common', 'period': 'morning', 'text': '早安！新的一天也要好好生活。', 'weight': 90},
        {'id': 'morning_rare', 'period': 'morning', 'text': '早安喵！今天的幸运已经送达。', 'weight': 10},
        {'id': 'night_common', 'period': 'night', 'text': '晚安，愿你今晚睡个好觉。', 'weight': 90},
        {'id': 'night_rare', 'period': 'night', 'text': '晚安喵！送你一颗好梦星星。', 'weight': 10},
    ])
    copies: list[Copy] = []
    for item in raw_copies:
        copy = Copy(**item)
        if (not isinstance(copy.id, str) or not copy.id or copy.period not in {'morning', 'night'}
                or not isinstance(copy.text, str) or not copy.text or len(copy.text) > 1000
                or type(copy.weight) not in (int, float) or not math.isfinite(copy.weight) or copy.weight <= 0):
            raise ValueError('Invalid check-in copy')
        copies.append(copy)
    if len({c.id for c in copies}) != len(copies) or {c.period for c in copies} != {'morning', 'night'}:
        raise ValueError('Copies require unique IDs and both periods')
    def location(key: str, default: str) -> Path:
        value = storage.get(key, default)
        if not isinstance(value, str) or not value:
            raise ValueError(f'{key} must be a path')
        return (ROOT / value).resolve()
    if type(ai.get('enabled', True)) is not bool:
        raise ValueError('ai.enabled must be boolean')
    if ai.get('history_limit', 20) > 20:
        raise ValueError('AI history is limited to 20 messages')
    return Settings(location('database', 'artifacts/runtime/bot.sqlite3'),
                    location('archives', 'artifacts/runtime/archives'), zone,
                    positive(storage, 'message_window', 1000), positive(storage, 'archive_batch', 1000),
                    positive(checkin, 'max_delay_seconds', 300), positive(bot, 'echo_max_chars', 1000),
                    seconds('command_interval', 1.0), seconds('send_interval', 1.0), tuple(copies),
                    ai.get('enabled', True), positive(ai,'concurrency',2), positive(ai,'queue_limit',20),
                    positive(ai,'timeout_seconds',120), positive(ai,'queue_timeout_seconds',300),
                    positive(ai,'history_limit',20), positive(ai,'daily_requests',500),
                    positive(ai,'daily_tokens',2000000), positive(ai,'input_chars',24000),bili_enabled=subscriptions.get('enabled',True))

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

MORNING = {'早上好', '早安', '早上好喵', '早安喵'}
NIGHT = {'晚安', '晚安喵'}


def classify(text: str | None, timestamp: int, timezone: str = 'Asia/Shanghai') -> tuple[str, str] | None:
    local = datetime.fromtimestamp(timestamp, ZoneInfo(timezone))
    clock = local.time()
    if text in MORNING and time(4, 30) <= clock <= time(12):
        return local.date().isoformat(), 'morning'
    if text in NIGHT and (clock >= time(20) or clock <= time(3)):
        business = local.date() - timedelta(days=clock <= time(3))
        return business.isoformat(), 'night'
    return None


def statistics(rows: list[dict], today: date) -> dict:
    days: dict[date, int] = {}
    for row in rows:
        day = date.fromisoformat(row['business_date'])
        days[day] = days.get(day, 0) + 1
    streak_days = streak_count = best_days = best_count = 0
    previous = None
    for day, count in sorted(days.items()):
        if previous is None or day != previous + timedelta(days=1):
            streak_days = streak_count = 0
        streak_days += 1
        streak_count += count
        best_days, best_count = max(best_days, streak_days), max(best_count, streak_count)
        previous = day
    if previous is None or previous < today - timedelta(days=1):
        streak_days = streak_count = 0
    return dict(total_days=len(days), total_count=len(rows), streak_days=streak_days,
                streak_count=streak_count, best_days=best_days, best_count=best_count,
                last_date=previous.isoformat() if previous else None)

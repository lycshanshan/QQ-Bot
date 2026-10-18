"""Persist transport health without messages, credentials, or automatic login retries."""
from __future__ import annotations

import json
import os
from pathlib import Path
import time


class ConnectionHealth:
    def __init__(self, path: Path, now: float | None = None):
        self.path = path
        self.connected: set[str] = set()
        self.started_at = time.time() if now is None else now
        self.offline_since: float | None = self.started_at
        self.last_connected_at: float | None = None
        self.last_disconnected_at: float | None = None
        self.warned = False
        self.publish()

    def publish(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix('.tmp')
        temporary.write_text(json.dumps({
            'connected_bot_ids': sorted(self.connected),
            'started_at': self.started_at,
            'offline_since': self.offline_since,
            'last_connected_at': self.last_connected_at,
            'last_disconnected_at': self.last_disconnected_at,
        }, indent=2) + '\n', encoding='utf-8')
        os.replace(temporary, self.path)

    def connect(self, bot_id: str, now: float | None = None):
        self.connected.add(bot_id)
        self.last_connected_at = time.time() if now is None else now
        self.offline_since = None
        self.warned = False
        self.publish()

    def disconnect(self, bot_id: str, now: float | None = None):
        self.connected.discard(bot_id)
        self.last_disconnected_at = time.time() if now is None else now
        if not self.connected and self.offline_since is None:
            self.offline_since = self.last_disconnected_at
            self.warned = False
        self.publish()

    def needs_warning(self, now: float | None = None) -> bool:
        now = time.time() if now is None else now
        if self.offline_since is not None and now - self.offline_since >= 60 and not self.warned:
            self.warned = True
            return True
        return False

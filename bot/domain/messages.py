from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any


@dataclass(frozen=True)
class Incoming:
    bot_id: str
    kind: str
    target_id: str
    user_id: str
    message_id: str
    occurred_at: int
    received_at: float
    nickname: str
    card: str
    segments: list[dict[str, Any]]
    command_text: str
    reply: dict[str, Any] | None = None
    message_seq: str | None = None

    @property
    def conversation_id(self) -> str:
        return f'{self.kind}:{self.bot_id}:{self.target_id}'

    @property
    def event_key(self) -> str:
        return f'{self.conversation_id}:{self.message_id}'

    @property
    def pure_text(self) -> str | None:
        if not self.segments or any(s.get('type') != 'text' for s in self.segments):
            return None
        return ''.join(str(s.get('data', {}).get('text', '')) for s in self.segments)

    def payload(self) -> dict[str, Any]:
        return dict(schema_version=1, bot_id=self.bot_id, conversation_id=self.conversation_id,
                    event_key=self.event_key, message_id=self.message_id, message_seq=self.message_seq,
                    direction='incoming', sender_id=self.user_id, nickname=self.nickname, card=self.card,
                    occurred_at=datetime.fromtimestamp(self.occurred_at, UTC).isoformat(),
                    received_at=datetime.fromtimestamp(self.received_at, UTC).isoformat(),
                    segments=self.segments, reply_to=self.reply, revoked=False)

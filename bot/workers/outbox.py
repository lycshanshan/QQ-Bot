from __future__ import annotations

import asyncio
from datetime import UTC, datetime
import json
import time
from typing import Any

from bot.repositories.database import Database
from bot.services.application import can_send, dumps


class Outbox:
    def __init__(self, database: Database, ai_enabled=lambda: True, notifications_enabled=lambda: True):
        self.database = database
        self.ai_enabled = ai_enabled
        self.notifications_enabled = notifications_enabled

    async def deliver_one(self, bots: dict[str, Any]) -> str | None:
        with self.database.transaction() as db:
            rows = db.execute('''SELECT o.*,c.kind,c.target_id FROM outbox o
                JOIN conversations c ON c.id=o.conversation_id
                WHERE o.status IN ('pending','retry') AND o.next_attempt<=?
                AND NOT EXISTS(SELECT 1 FROM outbox earlier
                  WHERE earlier.conversation_id=o.conversation_id AND earlier.id<o.id
                  AND earlier.status IN ('pending','retry','sending','uncertain'))
                ORDER BY o.id LIMIT 50''', (time.time(),)).fetchall()
            row = next((r for r in rows if r['bot_id'] in bots), None)
            if row is None:
                return None
            if not can_send(db, row) or (row['dedup_key'].startswith('ai:') and not self.ai_enabled()) or (row['dedup_key'].startswith(('live:','live-error:')) and not self.notifications_enabled()):
                db.execute("UPDATE outbox SET status='suppressed',error='policy_changed' WHERE id=?", (row['id'],))
                return 'suppressed'
            db.execute("UPDATE outbox SET status='sending',attempts=attempts+1 WHERE id=?", (row['id'],))
        from nonebot.adapters.onebot.v11 import Message, MessageSegment
        from nonebot.adapters.onebot.v11.exception import ActionFailed
        try:
            # No await between policy recheck and submission to the connected adapter.
            with self.database.transaction() as db:
                if not can_send(db, row):
                    db.execute("UPDATE outbox SET status='suppressed',error='policy_changed' WHERE id=?", (row['id'],))
                    return 'suppressed'
            segments=json.loads(row['payload'])
            uploading=bool(segments and segments[0]['type']=='_upload_file')
            if uploading:
                data=segments[0]['data']
                action='upload_group_file' if row['kind']=='group' else 'upload_private_file'
                params={('group_id' if row['kind']=='group' else 'user_id'):int(row['target_id']),
                        'file':data['path'],'name':data['name']}
            else:
                action='send_group_msg' if row['kind']=='group' else 'send_private_msg'
                params={('group_id' if row['kind']=='group' else 'user_id'):int(row['target_id']),
                        'message':Message([MessageSegment(**segment) for segment in segments])}
            result=await asyncio.wait_for(bots[row['bot_id']].call_api(action,**params),timeout=30)
        except asyncio.CancelledError:
            self.finish(row['id'], 'uncertain', 'cancelled_during_send')
            raise
        except ActionFailed:
            attempts = row['attempts'] + 1
            self.finish(row['id'], 'retry' if attempts < 5 else 'failed', 'api_rejected',
                        time.time()+min(60, 2**attempts))
            return 'rejected'
        except Exception as exc:
            # A transport failure may follow a successful send. Never blindly replay it.
            self.finish(row['id'], 'uncertain', type(exc).__name__)
            return 'uncertain'
        if not uploading and (not isinstance(result, dict) or result.get('message_id') is None):
            self.finish(row['id'], 'uncertain', 'missing_message_id')
            return 'uncertain'
        message_id = str(result.get('message_id', 'upload:'+str(row['id']))) if isinstance(result,dict) else 'upload:'+str(row['id'])
        with self.database.transaction() as db:
            db.execute("UPDATE outbox SET status='sent',message_id=?,error=NULL WHERE id=?", (message_id, row['id']))
            timestamp = time.time()
            payload = dict(schema_version=1, bot_id=row['bot_id'], conversation_id=row['conversation_id'],
                           event_key=f'outbox:{row["id"]}', message_id=message_id, message_seq=None,
                           direction='outgoing', sender_id=row['bot_id'], nickname='', card='',
                           occurred_at=datetime.fromtimestamp(timestamp, UTC).isoformat(),
                           received_at=datetime.fromtimestamp(timestamp, UTC).isoformat(),
                           segments=([{'type':'file','data':{'name':segments[0]['data']['name']}}] if uploading else segments), reply_to=None, revoked=False)
            db.execute('''INSERT OR IGNORE INTO messages(event_key,conversation_id,message_id,sender_id,occurred_at,payload)
                          VALUES(?,?,?,?,?,?)''', (f'outbox:{row["id"]}', row['conversation_id'], message_id,
                                                  row['bot_id'], int(timestamp), dumps(payload)))
        return 'sent'

    def finish(self, identifier: int, status: str, error: str, next_attempt: float = 0) -> None:
        with self.database.transaction() as db:
            db.execute('UPDATE outbox SET status=?,error=?,next_attempt=? WHERE id=?',
                       (status, error, next_attempt, identifier))


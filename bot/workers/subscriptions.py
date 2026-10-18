from __future__ import annotations

import asyncio
import json
import random
import time

from bot.integrations.bilibili import BiliError,BiliLive
from bot.config import SUPERUSER


class LiveSubscriptions:
    def __init__(self,app,provider=None):
        self.app=app
        self.provider=provider or BiliLive()

    def command(self,db,event,name,args,role):
        if event.kind!='group':raise ValueError('请在目标监听群内管理或查看直播订阅。')
        if name=='subscriptions':
            rows=db.execute('''SELECT s.source_id,s.active,t.last_success,t.error,t.paused FROM subscriptions s
                LEFT JOIN subscription_state t ON t.kind=s.kind AND t.source_id=s.source_id
                WHERE s.conversation_id=? AND s.active=1''',(event.conversation_id,)).fetchall()
            return '本群直播订阅：\n'+('\n'.join(f'{r["source_id"]}：{"暂停" if r["paused"] else "轮询中"}，最近错误 {r["error"] or "无"}' for r in rows) or '无')+'\n动态订阅暂缓开发。'
        if role not in {'vip','admin','superuser'}:raise ValueError('直播订阅修改需要 vip 或更高权限。')
        if args[:1]==['bili-dynamic']:raise ValueError('动态订阅按要求暂缓开发，当前不启用。')
        if len(args)!=2 or args[0]!='bili-live' or not args[1].isascii() or not args[1].isdigit() or not 1<=len(args[1])<=12:
            raise ValueError(f'用法：/{name} bili-live <直播间ID>')
        room=args[1]
        if name=='unsubscribe':
            db.execute("UPDATE subscriptions SET active=0 WHERE conversation_id=? AND kind='bili-live' AND source_id=?",(event.conversation_id,room))
            self.app.audit(db,event,'unsubscribe',room,'bili-live')
            return f'已退订直播间 {room}。'
        count=db.execute('SELECT count(*) FROM subscriptions WHERE conversation_id=? AND active=1',(event.conversation_id,)).fetchone()[0]
        prior=db.execute("SELECT active FROM subscriptions WHERE conversation_id=? AND kind='bili-live' AND source_id=?",(event.conversation_id,room)).fetchone()
        if count>=10 and not (prior and prior['active']):raise ValueError('本群最多订阅 10 个直播间。')
        # A new subscriber joins the shared stream at its current cursor, never replays history.
        db.execute("INSERT OR IGNORE INTO subscription_state(kind,source_id) VALUES('bili-live',?)",(room,))
        db.execute("UPDATE subscription_state SET paused=0,error=NULL,next_poll=0 WHERE kind='bili-live' AND source_id=?",(room,))
        db.execute('''INSERT INTO subscriptions(conversation_id,kind,source_id,created_by) VALUES(?,'bili-live',?,?)
                      ON CONFLICT(conversation_id,kind,source_id) DO UPDATE SET active=1''',(event.conversation_id,room,event.user_id))
        self.app.audit(db,event,'subscribe',room,'bili-live')
        return f'已订阅直播间 {room}。首次建立基线，不补发历史；每 60—120 秒检查。'

    def ingest(self,room: str,snapshot: dict,now: float):
        with self.app.database.transaction() as db:
            row=db.execute("SELECT * FROM subscription_state WHERE kind='bili-live' AND source_id=?",(room,)).fetchone()
            if row is None:return
            previous=json.loads(row['baseline']) if row['baseline'] else None
            notify=previous is not None and snapshot['online'] and (not previous['online'] or snapshot['session']!=previous['session'])
            baseline=snapshot
            candidate=None
            candidate_since=None
            if previous and previous['online'] and not snapshot['online']:
                candidate=json.dumps(snapshot)
                candidate_since=row['candidate_since'] or now
                if now-candidate_since<120:baseline=previous
            if notify:
                subscribers=db.execute('''SELECT s.conversation_id,c.bot_id,c.target_id FROM subscriptions s
                    JOIN conversations c ON c.id=s.conversation_id JOIN bot_state b ON b.bot_id=c.bot_id
                    WHERE s.kind='bili-live' AND s.source_id=? AND s.active=1 AND c.watched=1 AND b.enabled=1''',(room,)).fetchall()
                text=f'直播开播：{snapshot["title"]}\nhttps://live.bilibili.com/{snapshot["room_id"]}'
                for subscriber in subscribers:
                    # System notifications are sent under the protected controller actor; global off
                    # is checked explicitly here and again via notification-aware outbox policy.
                    key=f'live:{subscriber["conversation_id"]}:{room}:{snapshot["session"]}'
                    db.execute('''INSERT OR IGNORE INTO outbox(dedup_key,bot_id,conversation_id,actor_id,payload,created_at)
                                  VALUES(?,?,?,?,?,?)''',(key,subscriber['bot_id'],subscriber['conversation_id'],SUPERUSER,
                                  json.dumps([{'type':'text','data':{'text':text}}],ensure_ascii=False),now))
            db.execute("UPDATE subscription_state SET baseline=?,last_success=?,next_poll=?,failures=0,error=NULL,candidate=?,candidate_since=? WHERE kind='bili-live' AND source_id=?",
                       (json.dumps(baseline),now,now+random.uniform(60,120),candidate,candidate_since,room))

    async def poll_one(self):
        now=time.time()
        with self.app.database.transaction() as db:
            row=db.execute('''SELECT t.* FROM subscription_state t WHERE t.kind='bili-live' AND t.paused=0 AND t.next_poll<=?
               AND EXISTS(SELECT 1 FROM subscriptions s JOIN conversations c ON c.id=s.conversation_id
                   JOIN bot_state b ON b.bot_id=c.bot_id WHERE s.kind=t.kind AND s.source_id=t.source_id
                   AND s.active=1 AND c.watched=1 AND b.enabled=1) ORDER BY t.next_poll LIMIT 1''',(now,)).fetchone()
        if row is None:return None
        try:
            snapshot=await asyncio.to_thread(self.provider.snapshot,row['source_id'])
            self.ingest(row['source_id'],snapshot,now)
            return 'ok'
        except Exception as exc:
            code=getattr(exc,'code',type(exc).__name__)
            paused=code in {401,403,412,429,-352}
            failures=row['failures']+1
            with self.app.database.transaction() as db:
                db.execute("UPDATE subscription_state SET failures=?,error=?,paused=?,next_poll=? WHERE kind='bili-live' AND source_id=?",
                           (failures,str(code),int(paused),now+min(3600,60*2**min(failures,6)),row['source_id']))
                if paused or failures==3:
                    subscribers=db.execute('''SELECT c.id,c.bot_id FROM subscriptions s JOIN conversations c ON c.id=s.conversation_id
                        JOIN bot_state b ON b.bot_id=c.bot_id WHERE s.kind='bili-live' AND s.source_id=?
                        AND s.active=1 AND c.watched=1 AND b.enabled=1''',(row['source_id'],)).fetchall()
                    for target in subscribers:
                        key=f'live-error:{target["id"]}:{row["source_id"]}:{int(now//3600)}'
                        text=f'直播间 {row["source_id"]} 轮询{"已暂停" if paused else "连续失败"}，错误 {code}。可用 /订阅列表 查看；处理后重新 /subscribe 可恢复。'
                        db.execute('''INSERT OR IGNORE INTO outbox(dedup_key,bot_id,conversation_id,actor_id,payload,created_at)
                            VALUES(?,?,?,?,?,?)''',(key,target['bot_id'],target['id'],SUPERUSER,json.dumps([{'type':'text','data':{'text':text}}],ensure_ascii=False),now))
            return 'failed'

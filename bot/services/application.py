from __future__ import annotations

from datetime import datetime, timedelta
import json
import random
import sqlite3
import time
from zoneinfo import ZoneInfo

from bot.config import Settings, SUPERUSER
from bot.ai.requests import AIRequests
from bot.workers.subscriptions import LiveSubscriptions
from bot.domain.checkin import classify, statistics
from bot.domain.messages import Incoming
from bot.repositories.database import Database

RANK = {'user': 0, 'vip': 1, 'admin': 2, 'superuser': 3}
ALIASES = {'帮助': 'help', '复读': 'echo', '状态': 'status', '打卡查询': 'checkin-get',
           '提问': 'ask', '新会话': 'new', '上下文': 'context', '额度': 'quota',
           '保存': 'save', '网盘': 'nas', '取消': 'cancel', '订阅列表': 'subscriptions'}
HELP = [('help', '帮助'), ('ping', '在线检查'), ('status', '状态'), ('echo <内容>', '复读'),
        ('checkin-get', '打卡查询')]


def dumps(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'))


def role_for(db: sqlite3.Connection, user_id: str) -> str:
    if user_id == SUPERUSER:
        return 'superuser'
    row = db.execute('SELECT role FROM users WHERE user_id=?', (user_id,)).fetchone()
    return row['role'] if row else 'user'


def can_send(db: sqlite3.Connection, row: sqlite3.Row) -> bool:
    actor = row['actor_id']
    key=row['dedup_key'] if 'dedup_key' in row.keys() else ''
    notification=key.startswith(('live:','live-error:'))
    state = db.execute('SELECT enabled FROM bot_state WHERE bot_id=?', (row['bot_id'],)).fetchone()
    if state and not state['enabled'] and (actor != SUPERUSER or notification):
        return False
    user = db.execute('SELECT banned FROM users WHERE user_id=?', (actor,)).fetchone()
    if user and user['banned'] and actor != SUPERUSER:
        return False
    conversation = db.execute('SELECT * FROM conversations WHERE id=?', (row['conversation_id'],)).fetchone()
    if notification and conversation:
        room=key.split(row['conversation_id']+':',1)[-1].split(':',1)[0]
        if not db.execute("SELECT 1 FROM subscriptions WHERE conversation_id=? AND source_id=? AND kind='bili-live' AND active=1",(row['conversation_id'],room)).fetchone():
            return False
    if conversation and 'dedup_key' in row.keys() and row['dedup_key'].startswith('ai:') and not conversation['ai_enabled']:
        return False
    return bool(conversation and (conversation['kind'] == 'private' or conversation['watched']))


class Application:
    def __init__(self, settings: Settings, database: Database | None = None):
        self.settings = settings
        self.database = database or Database(settings.database)
        self.last_commands: dict[tuple[str, str], float] = {}
        self.started_at = time.time()
        self.ai = AIRequests(self)
        self.available_models = None
        self.subscriptions = LiveSubscriptions(self)

    def audit(self, db: sqlite3.Connection, event: Incoming, action: str, target: str, detail: str) -> None:
        db.execute('INSERT INTO audit_log(actor_id,action,target,detail,created_at) VALUES(?,?,?,?,?)',
                   (event.user_id, action, target, detail, event.received_at))

    def enqueue(self, db: sqlite3.Connection, event: Incoming, text: str, suffix: str = 'reply') -> None:
        db.execute('''INSERT INTO outbox(dedup_key,bot_id,conversation_id,actor_id,payload,created_at)
                      VALUES(?,?,?,?,?,?)''', (f'{event.event_key}:{suffix}', event.bot_id, event.conversation_id,
                                              event.user_id, dumps([{'type': 'text', 'data': {'text': text}}]),
                                              event.received_at))

    def process(self, event: Incoming, joined_groups: set[str] | None = None) -> bool:
        """Record and route one event in a single transaction. False means ignored/replayed."""
        if event.user_id == event.bot_id or event.kind not in {'private', 'group'}:
            return False
        command = event.command_text.strip()
        is_command = command.startswith('/')
        words = command[1:].split() if is_command else []
        name = ALIASES.get(words[0], words[0]) if words else ''
        with self.database.transaction() as db:
            role = role_for(db, event.user_id)
            existing = db.execute('SELECT * FROM conversations WHERE id=?', (event.conversation_id,)).fetchone()
            watched = event.kind == 'private' or bool(existing and existing['watched'])
            bootstrap = name == 'watch' and words[1:2] == ['add'] and RANK[role] >= RANK['admin']
            if not watched and not bootstrap:
                return False
            if not db.execute('INSERT OR IGNORE INTO event_receipts VALUES(?,?)',
                              (event.event_key, event.received_at)).rowcount:
                return False
            db.execute('INSERT INTO users(user_id,nickname) VALUES(?,?) ON CONFLICT(user_id) DO UPDATE SET nickname=excluded.nickname',
                       (event.user_id, event.nickname))
            db.execute('INSERT OR IGNORE INTO bot_state(bot_id) VALUES(?)', (event.bot_id,))
            db.execute('''INSERT OR IGNORE INTO conversations(id,bot_id,kind,target_id,watched,ai_enabled)
                          VALUES(?,?,?,?,?,?)''', (event.conversation_id, event.bot_id, event.kind,
                                                  event.target_id, int(event.kind == 'private'), int(event.kind == 'private')))
            if watched:
                revoked = bool(db.execute('SELECT 1 FROM message_tombstones WHERE conversation_id=? AND message_id=?',
                                          (event.conversation_id, event.message_id)).fetchone())
                payload = event.payload()
                payload['revoked'] = revoked
                # Reply content is retained only if its origin is in this conversation.
                if payload['reply_to']:
                    reply_id = str(payload['reply_to']['message_id'])
                    origin = db.execute('SELECT payload,revoked FROM messages WHERE conversation_id=? AND message_id=?',
                                        (event.conversation_id, reply_id)).fetchone()
                    payload['reply_to'] = {'message_id': reply_id, 'scope_verified': bool(origin),
                                           'snapshot': json.loads(origin['payload'])['segments'] if origin and not origin['revoked'] else None}
                db.execute('''INSERT INTO messages(event_key,conversation_id,message_id,sender_id,occurred_at,payload,revoked)
                              VALUES(?,?,?,?,?,?,?)''', (event.event_key, event.conversation_id, event.message_id,
                                                        event.user_id, event.occurred_at, dumps(payload), int(revoked)))
            banned = db.execute('SELECT banned FROM users WHERE user_id=?', (event.user_id,)).fetchone()['banned']
            enabled = db.execute('SELECT enabled FROM bot_state WHERE bot_id=?', (event.bot_id,)).fetchone()['enabled']
            if event.user_id != SUPERUSER and (banned or not enabled):
                return True
            if is_command:
                key = (event.conversation_id, event.user_id)
                previous = self.last_commands.get(key, 0)
                if event.received_at - previous < self.settings.command_interval and name != 'bot':
                    return True
                self.last_commands[key] = event.received_at
                # Bound transient rate state independently of the durable message store.
                if len(self.last_commands) > 10000:
                    cutoff = event.received_at - max(60, self.settings.command_interval)
                    self.last_commands = {k: v for k, v in self.last_commands.items() if v >= cutoff}
                try:
                    response = self.command(db, event, name, words[1:], command, role, joined_groups)
                except ValueError as exc:
                    response = str(exc)
                if response:
                    self.enqueue(db, event, response)
            elif watched:
                conversation = db.execute('SELECT checkin_enabled FROM conversations WHERE id=?', (event.conversation_id,)).fetchone()
                if event.kind == 'group' and conversation['checkin_enabled']:
                    self.checkin(db, event)
                self.ai.natural(db, event, role)
        self.ai.policy.apply_pending(self.database)
        return True

    def command(self, db: sqlite3.Connection, event: Incoming, name: str, args: list[str], raw: str,
                role: str, joined_groups: set[str] | None) -> str | None:
        def require(minimum: str) -> None:
            if RANK[role] < RANK[minimum]:
                self.audit(db, event, 'permission_denied', name, minimum)
                raise ValueError('权限不足。')
        def qq(value: str) -> str:
            if not value.isascii() or not value.isdigit() or not 5 <= len(value) <= 12 or value.startswith('0'):
                raise ValueError('QQ/群号格式不正确。')
            return value
        if name == 'help':
            lines = [f'/{cmd} - {label}' for cmd, label in HELP]
            if RANK[role] >= RANK['admin']:
                lines += ['/watch add <群号>', '/checkin on/off', '/ai on/off', '/system health/reload']
            if role == 'superuser':
                lines += ['/admin add/remove <QQ>', '/vip add/remove <QQ>', '/ban <QQ>、/unban <QQ>',
                          '/watch remove <群号>', '/bot on/off', '/model set <模型>', '/high-model set <模型>']
            lines += ['/ask <问题> /提问', '/quota /额度', '/cancel /取消', '/new /新会话（私聊）', '/context user|group <1-20>（群）','/subscriptions /订阅列表']
            if RANK[role]>=RANK['vip']:
                lines += ['/subscribe bili-live <直播间ID>','/unsubscribe bili-live <直播间ID>']
            if RANK[role] >= RANK['admin']:
                lines += ['/quota-admin limit/grant/reset ...']
            return '可用命令：\n' + '\n'.join(lines) # + '\n用户 NAS 保存与B站动态暂缓。'
        if name == 'ping':
            return 'pong'
        if name == 'echo':
            parts = raw.split(maxsplit=1)
            text = parts[1].strip() if len(parts) > 1 else ''
            if not text:
                raise ValueError('用法：/echo <内容>（或 /复读 <内容>）')
            if len(text) > self.settings.echo_max_chars:
                raise ValueError(f'最多复读 {self.settings.echo_max_chars} 个字符。')
            return text
        if name == 'status':
            row = db.execute('SELECT * FROM conversations WHERE id=?', (event.conversation_id,)).fetchone()
            enabled = db.execute('SELECT enabled FROM bot_state WHERE bot_id=?', (event.bot_id,)).fetchone()['enabled']
            return (f'机器人在线，运行 {int(time.time()-self.started_at)} 秒\n身份：{role}\n'
                    f'总开关：{"开" if enabled else "关"}\n'
                    f'本会话打卡：{"开" if row["checkin_enabled"] else "关"}\n'
                    f'本会话 AI 开关：{"开" if row["ai_enabled"] else "关"}')
        if name in {'admin', 'vip'}:
            require('superuser')
            if len(args) != 2 or args[0] not in {'add', 'remove'}:
                raise ValueError(f'用法：/{name} add/remove <QQ>')
            target = qq(args[1])
            if target == SUPERUSER:
                raise ValueError('唯一 superuser 不可修改。')
            current = role_for(db, target)
            if name == 'vip' and current == 'admin':
                raise ValueError('目标是 admin，请先明确移除 admin 身份。')
            new_role = name if args[0] == 'add' else 'user'
            if args[0] == 'remove' and current != name:
                raise ValueError(f'目标当前不是 {name}。')
            db.execute('INSERT INTO users(user_id,role) VALUES(?,?) ON CONFLICT(user_id) DO UPDATE SET role=excluded.role',
                       (target, new_role))
            self.audit(db, event, name, target, new_role)
            return f'{target} 的身份已设为 {new_role}。'
        if name in {'ban', 'unban'}:
            require('superuser')
            if len(args) != 1:
                raise ValueError(f'用法：/{name} <QQ>')
            target = qq(args[0])
            if target == SUPERUSER:
                raise ValueError('不能封禁唯一 superuser。')
            db.execute('INSERT INTO users(user_id,banned) VALUES(?,?) ON CONFLICT(user_id) DO UPDATE SET banned=excluded.banned',
                       (target, int(name == 'ban')))
            self.audit(db, event, name, target, '')
            return f'{target} 已{"封禁" if name == "ban" else "解封"}。'
        if name == 'bot':
            require('superuser')
            if args not in [['on'], ['off']]:
                raise ValueError('用法：/bot on/off')
            db.execute('UPDATE bot_state SET enabled=? WHERE bot_id=?', (int(args[0] == 'on'), event.bot_id))
            self.audit(db, event, 'bot', event.bot_id, args[0])
            return f'总开关已{args[0]}。关闭期间继续记录已监听会话，保留 superuser 恢复入口。'
        if name == 'watch':
            require('admin')
            if len(args) != 2 or args[0] not in {'add', 'remove'}:
                raise ValueError('用法：/watch add/remove <群号>')
            if args[0] == 'remove':
                require('superuser')
            target = qq(args[1])
            if args[0] == 'add' and (joined_groups is None or target not in joined_groups):
                raise ValueError('无法确认机器人已加入该群，未添加监听。')
            scope = f'group:{event.bot_id}:{target}'
            db.execute('''INSERT INTO conversations(id,bot_id,kind,target_id,watched) VALUES(?,?,?,?,?)
                          ON CONFLICT(id) DO UPDATE SET watched=excluded.watched''',
                       (scope, event.bot_id, 'group', target, int(args[0] == 'add')))
            self.audit(db, event, 'watch', target, args[0])
            return f'群 {target} 监听已{"添加" if args[0] == "add" else "移除"}。'
        if name in {'checkin', 'ai'}:
            require('admin')
            if event.kind != 'group':
                raise ValueError('请在目标监听群内执行此命令。')
            if args not in [['on'], ['off']]:
                raise ValueError(f'用法：/{name} on/off')
            column = 'checkin_enabled' if name == 'checkin' else 'ai_enabled'
            db.execute(f'UPDATE conversations SET {column}=? WHERE id=?', (int(args[0] == 'on'), event.conversation_id))
            self.audit(db, event, name, event.conversation_id, args[0])
            return f'本群{name}开关已{args[0]}。'
        if name == 'checkin-get':
            if event.kind != 'group':
                raise ValueError('请在监听群内查询该群的打卡记录。')
            return self.checkin_status(db, event)
        if name == 'system':
            require('admin')
            if args == ['health']:
                rows = db.execute('SELECT status,count(*) AS n FROM outbox GROUP BY status').fetchall()
                ai=db.execute('SELECT status,count(*) AS n FROM ai_requests GROUP BY status').fetchall()
                nas=db.execute('SELECT status,count(*) AS n FROM archive_packs GROUP BY status').fetchall()
                paused=db.execute('SELECT count(*) FROM subscription_state WHERE paused=1').fetchone()[0]
                counts=lambda values:'、'.join(f'{r["status"]}={r["n"]}' for r in values) or '空'
                return f'数据库正常；业务模块已加载。\n发送队列：{counts(rows)}\nAI：{counts(ai)}\nNAS：{counts(nas)}\n暂停的直播源：{paused}'
            if args == ['reload']:
                # The plugin validates and publishes a new immutable configuration after this transaction.
                return '配置重载请求已处理。'
            raise ValueError('当前支持 /system health、/system reload；服务重启命令尚未开放。')
        if name in {'subscribe','unsubscribe','subscriptions'}:
            if name!='subscriptions':
                require('vip')
            return self.subscriptions.command(db,event,name,args,role)
        if name in {'save','nas'}:
            return '功能开发中，敬请期待' # '用户 NAS 绑定和文件保存按当前开发安排暂缓；机器人自身归档独立运行。'
        if name in {'model','high-model'}:
            require('superuser')
            if len(args)!=2 or args[0]!='set' or args[1] not in {'gpt-6-luna','gpt-6.1-sol'}:
                raise ValueError(f'用法：/{name} set gpt-6-luna|gpt-6.1-sol')
            if self.available_models is None or args[1] not in self.available_models:
                raise ValueError('尚未确认该模型在当前登录账号中可用，未修改。')
            column='default_model' if name=='model' else 'high_model'
            db.execute(f'UPDATE bot_state SET {column}=? WHERE bot_id=?',(args[1],event.bot_id))
            self.audit(db,event,name,event.bot_id,args[1])
            return f'{name} 已设为 {args[1]}，对新请求生效。'
        if name in {'ask', 'new', 'context', 'quota', 'quota-admin', 'cancel'}:
            if name == 'quota-admin':
                require('admin')
            return self.ai.command(db,event,name,args,role)
        if name in {'save', 'nas',
                    'subscriptions', 'subscribe', 'unsubscribe', 'model', 'high-model', 'experimental'}:
            return '此功能仍在开发中，尚未启用。使用 /help 或 /帮助 查看当前命令。'
        return '未知命令。使用 /help 或 /帮助 查看当前命令。'

    def checkin(self, db: sqlite3.Connection, event: Incoming) -> None:
        match = classify(event.pure_text, event.occurred_at, self.settings.timezone)
        if not match or not -30 <= event.received_at-event.occurred_at <= self.settings.checkin_max_delay:
            return
        day, period = match
        exists = db.execute('SELECT 1 FROM checkins WHERE conversation_id=? AND user_id=? AND business_date=? AND period=?',
                            (event.conversation_id, event.user_id, day, period)).fetchone()
        if exists:
            self.enqueue(db, event, '这个时段已经打过卡啦。')
            return
        copies = [c for c in self.settings.copies if c.period == period]
        copy = random.choices(copies, weights=[c.weight for c in copies], k=1)[0]
        db.execute('''INSERT INTO checkins(conversation_id,user_id,business_date,period,copy_id,event_key,occurred_at)
                      VALUES(?,?,?,?,?,?,?)''', (event.conversation_id, event.user_id, day, period,
                                                copy.id, event.event_key, event.occurred_at))
        self.enqueue(db, event, f'{copy.text}\n------------\n{self.checkin_status(db, event)}', 'checkin')

    def checkin_status(self, db: sqlite3.Connection, event: Incoming) -> str:
        rows = [dict(r) for r in db.execute('SELECT business_date,period,copy_id FROM checkins WHERE conversation_id=? AND user_id=?',
                                          (event.conversation_id, event.user_id))]
        now = datetime.fromtimestamp(event.received_at, ZoneInfo(self.settings.timezone))
        today = now.date() - timedelta(days=now.hour < 3 or (now.hour == 3 and now.minute == 0 and now.second == 0))
        stats = statistics(rows, today)
        known_ids = {c.id for c in self.settings.copies}
        collected = {r['copy_id'] for r in rows} & known_ids
        latest = max(rows, key=lambda r: (r['business_date'], r['period'])) if rows else None
        last = f'{latest["business_date"]} {"早" if latest["period"] == "morning" else "晚"}' if latest else '无'
        return (f'累计 {stats["total_days"]} 天 / {stats["total_count"]} 次\n'
                f'当前连续 {stats["streak_days"]} 天 / {stats["streak_count"]} 次\n'
                f'历史最长 {stats["best_days"]} 天 / {stats["best_count"]} 次\n'
                f'最近打卡：{last}\n文案收集 {len(collected)}/{len(known_ids)}')

    def recall(self, bot_id: str, kind: str, target: str, message_id: str, timestamp: int) -> None:
        scope = f'{kind}:{bot_id}:{target}'
        with self.database.transaction() as db:
            conversation = db.execute('SELECT watched FROM conversations WHERE id=?', (scope,)).fetchone()
            if not conversation or (kind == 'group' and not conversation['watched']):
                return
            db.execute('INSERT OR IGNORE INTO message_tombstones VALUES(?,?,?)', (scope, message_id, timestamp))
            db.execute('UPDATE messages SET revoked=1 WHERE conversation_id=? AND message_id=?', (scope, message_id))

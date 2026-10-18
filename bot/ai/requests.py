from __future__ import annotations

from datetime import datetime
import json
import sqlite3
import uuid
from zoneinfo import ZoneInfo

from bot.ai.policy import Policy


class AIRequests:
    def __init__(self, app):
        self.app=app
        self.policy=Policy(app.settings.ai_policy)

    def day(self, timestamp: float) -> str:
        return datetime.fromtimestamp(timestamp,ZoneInfo(self.app.settings.timezone)).date().isoformat()

    def quota(self,db,user: str,day: str,unlimited: bool) -> str:
        row=db.execute('SELECT * FROM quota_daily WHERE user_id=? AND business_date=?',(user,day)).fetchone()
        used,reserved,grants=(row['used'],row['reserved'],row['grants']) if row else (0,0,0)
        limit=self.policy.limit(user)
        return f'今日额度：{"不限业务次数" if unlimited else str(max(0,limit+grants-used-reserved))+" 次剩余"}\n已用 {used}，预留 {reserved}，每日上限 {limit}，今日加赠 {grants}\n上海时间次日 00:00 重置。'

    def command(self,db,event,name,args,role):
        unlimited=role in {'vip','admin','superuser'}
        day=self.day(event.received_at)
        if name=='quota':
            return self.quota(db,event.user_id,day,unlimited)
        if name=='ask':
            if not args and not any(s['type'] in {'image','file'} for s in event.segments) and not event.reply:
                raise ValueError('用法：/ask <问题>，可附带图片或引用。')
            self.admit(db,event,unlimited,' '.join(args))
            return None
        if name=='context':
            if event.kind!='group' or len(args)!=2 or args[0] not in {'user','group'} or not args[1].isdigit():
                raise ValueError('在本群执行 /context user|group <1-20>，只对下一次 AI 请求有效。')
            count=int(args[1])
            if not 1<=count<=self.app.settings.ai_history_limit:
                raise ValueError(f'历史条数应为 1-{self.app.settings.ai_history_limit}。')
            self.ensure_session(db,event)
            db.execute('UPDATE context_sessions SET context_mode=?,context_count=? WHERE conversation_id=? AND user_id=?',
                       (args[0],count,event.conversation_id,event.user_id))
            return f'下一次本群 AI 请求将带入{args[0]}历史，最多 {count} 条。'
        if name=='new':
            if event.kind!='private':
                raise ValueError('群聊每次默认独立上下文；/new 仅重置自己的私聊。')
            self.ensure_session(db,event)
            db.execute('UPDATE context_sessions SET generation=generation+1,thread_id=NULL WHERE conversation_id=? AND user_id=?',
                       (event.conversation_id,event.user_id))
            db.execute("UPDATE ai_requests SET cancel_requested=1 WHERE conversation_id=? AND user_id=? AND status IN ('queued','running')",
                       (event.conversation_id,event.user_id))
            return '已创建新会话；旧任务将取消，聊天记录保留。'
        if name=='cancel':
            changed=db.execute("UPDATE ai_requests SET cancel_requested=1 WHERE conversation_id=? AND user_id=? AND status IN ('queued','running')",
                               (event.conversation_id,event.user_id)).rowcount
            return f'已请求取消 {changed} 个自己的任务。'
        if name=='quota-admin':
            if role not in {'admin','superuser'}:
                raise ValueError('权限不足。')
            if len(args)>=3 and args[:2]==['limit','default']:
                if len(args)!=3:
                    raise ValueError('用法：/quota-admin limit default <N>')
                target,value='default',args[2]
            elif len(args)==4 and args[:2]==['limit','user']:
                target,value=args[2],args[3]
                self.validate_qq(target)
            else:
                target=value=None
            if target is not None:
                if not value.isascii() or not value.isdigit() or int(value)>100000:
                    raise ValueError('额度必须是 0-100000 的整数。')
                self.policy.prepare(db,event.event_key,event.user_id,target,int(value))
                return '正在应用额度策略。'
            if args[:1]==['reset'] and len(args)<=2:
                targets=[self.validate_qq(args[1])] if len(args)==2 else [r[0] for r in db.execute('SELECT user_id FROM users')]
                for user in targets:
                    db.execute('INSERT OR IGNORE INTO quota_daily(user_id,business_date) VALUES(?,?)',(user,day))
                    db.execute('UPDATE quota_daily SET used=0 WHERE user_id=? AND business_date=?',(user,day))
                    self.ledger(db,user,day,None,'admin_reset',0,event.received_at)
                self.app.audit(db,event,'quota_reset',args[1] if len(args)==2 else 'all','')
                return f'已重置 {len(targets)} 个用户的当日用量；在途预留保留。'
            if args[:1]==['grant'] and len(args) in {2,3} and args[1].isdigit() and 0<int(args[1])<=100000:
                targets=[self.validate_qq(args[2])] if len(args)==3 else [r[0] for r in db.execute('SELECT user_id FROM users')]
                for user in targets:
                    db.execute('INSERT OR IGNORE INTO quota_daily(user_id,business_date) VALUES(?,?)',(user,day))
                    db.execute('UPDATE quota_daily SET grants=grants+? WHERE user_id=? AND business_date=?',(int(args[1]),user,day))
                    self.ledger(db,user,day,None,'admin_grant',int(args[1]),event.received_at)
                self.app.audit(db,event,'quota_grant',args[2] if len(args)==3 else 'all',args[1])
                return f'已为 {len(targets)} 个用户各加赠当日 {args[1]} 次。'
            raise ValueError('用法：/quota-admin limit default <N>；limit user <QQ> <N>；grant <N> [QQ]；reset [QQ]')
        raise ValueError('Unsupported AI command')

    @staticmethod
    def validate_qq(value: str) -> str:
        if not value.isascii() or not value.isdigit() or not 5<=len(value)<=12 or value.startswith('0'):
            raise ValueError('QQ 号格式错误。')
        return value

    @staticmethod
    def ensure_session(db,event) -> None:
        db.execute('INSERT OR IGNORE INTO context_sessions(conversation_id,user_id) VALUES(?,?)',(event.conversation_id,event.user_id))

    @staticmethod
    def ledger(db,user,day,request,action,amount,timestamp):
        db.execute('INSERT INTO quota_ledger(user_id,business_date,request_id,action,amount,created_at) VALUES(?,?,?,?,?,?)',
                   (user,day,request,action,amount,timestamp))

    def admit(self,db,event,unlimited: bool,explicit: str | None=None) -> None:
        if not self.app.settings.ai_enabled:
            raise ValueError('AI 服务当前关闭。')
        conversation=db.execute('SELECT ai_enabled FROM conversations WHERE id=?',(event.conversation_id,)).fetchone()
        if not conversation['ai_enabled']:
            raise ValueError('本会话 AI 未开启，管理员可在本群执行 /ai on。')
        active=db.execute("SELECT count(*) FROM ai_requests WHERE status IN ('queued','running')").fetchone()[0]
        own=db.execute("SELECT count(*) FROM ai_requests WHERE user_id=? AND status IN ('queued','running')",(event.user_id,)).fetchone()[0]
        if active>=self.app.settings.ai_queue_limit or own>=3:
            raise ValueError('AI 队列已满，请稍后重试。')
        day=self.day(event.received_at)
        used=db.execute('SELECT count(*),coalesce(sum(tokens),0) FROM ai_requests WHERE business_date=?',(day,)).fetchone()
        if used[0]>=self.app.settings.ai_daily_requests or used[1]>=self.app.settings.ai_daily_tokens:
            raise ValueError('AI 今日全局资源预算已用尽。')
        identifier=uuid.uuid4().hex
        db.execute('INSERT OR IGNORE INTO quota_daily(user_id,business_date) VALUES(?,?)',(event.user_id,day))
        reserved=not unlimited
        if reserved:
            limit=self.policy.limit(event.user_id)
            changed=db.execute('''UPDATE quota_daily SET reserved=reserved+1 WHERE user_id=? AND business_date=?
                                AND used+reserved < ?+grants''',(event.user_id,day,limit)).rowcount
            if not changed:
                raise ValueError('今日 AI 额度已用尽。使用 /额度 查看。')
            self.ledger(db,event.user_id,day,identifier,'reserve',1,event.received_at)
        self.ensure_session(db,event)
        session=db.execute('SELECT * FROM context_sessions WHERE conversation_id=? AND user_id=?',(event.conversation_id,event.user_id)).fetchone()
        history=[]
        if event.kind=='group' and session['context_count']:
            sql='SELECT payload,revoked FROM messages WHERE conversation_id=? AND event_key<>? AND revoked=0'
            params=[event.conversation_id,event.event_key]
            if session['context_mode']=='user':
                sql+=' AND sender_id=?'
                params.append(event.user_id)
            sql+=' AND occurred_at>=? ORDER BY id DESC LIMIT ?'
            params.extend([event.occurred_at-86400,session['context_count']])
            history=[json.loads(r['payload']) for r in db.execute(sql,params)][::-1]
        reply=None
        if event.reply:
            origin=db.execute('SELECT payload FROM messages WHERE conversation_id=? AND message_id=? AND revoked=0',
                              (event.conversation_id,str(event.reply['message_id']))).fetchone()
            reply=json.loads(origin['payload']) if origin else {'message_id':event.reply['message_id'],'unavailable':True}
        high_model=db.execute('SELECT high_model FROM bot_state WHERE bot_id=?',(event.bot_id,)).fetchone()[0]
        envelope={'limits':{'default_high_model':high_model,'advanced_tasks':2},'version':1,'request_id':identifier,'scope':event.conversation_id,'actor_id':event.user_id,
                  'input':{'segments':event.segments,'explicit_question':explicit,'reply':reply},'context':history}
        # Bound the supplied history first; never silently truncate the current input JSON.
        while history and len(json.dumps(envelope,ensure_ascii=False))>self.app.settings.ai_input_chars:
            history.pop(0)
        if len(json.dumps(envelope,ensure_ascii=False))>self.app.settings.ai_input_chars:
            if reserved:
                db.execute('UPDATE quota_daily SET reserved=reserved-1 WHERE user_id=? AND business_date=?',(event.user_id,day))
                self.ledger(db,event.user_id,day,identifier,'release_oversize',1,event.received_at)
            raise ValueError('消息过长，请缩短当前问题或引用。')
        model=db.execute('SELECT default_model FROM bot_state WHERE bot_id=?',(event.bot_id,)).fetchone()[0]
        db.execute('''INSERT INTO ai_requests(id,event_key,conversation_id,bot_id,user_id,business_date,generation,
                      reserved,model,input,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)''',
                   (identifier,event.event_key,event.conversation_id,event.bot_id,event.user_id,day,session['generation'],int(reserved),
                    model,json.dumps(envelope,ensure_ascii=False),event.received_at))
        db.execute("UPDATE context_sessions SET context_mode='none',context_count=0 WHERE conversation_id=? AND user_id=?",
                   (event.conversation_id,event.user_id))

    def natural(self,db,event,role) -> None:
        if event.kind=='group' and not any(s['type']=='at' and str(s.get('data',{}).get('qq'))==event.bot_id for s in event.segments):
            return
        conversation=db.execute('SELECT ai_enabled FROM conversations WHERE id=?',(event.conversation_id,)).fetchone()
        if not self.app.settings.ai_enabled or not conversation['ai_enabled']:
            return
        try:
            self.admit(db,event,role in {'vip','admin','superuser'})
        except ValueError as exc:
            self.app.enqueue(db,event,str(exc))

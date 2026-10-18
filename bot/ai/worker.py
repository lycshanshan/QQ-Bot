from __future__ import annotations

import asyncio
import json
from pathlib import Path
import time

from nonebot import logger

from bot.ai.provider import CodexProvider
from bot.ai.requests import AIRequests
from bot.ai.workspace import Workspaces
from bot.media.inputs import prepare_media
from bot.media.render import MarkdownRenderer
from bot.services.application import can_send


class AIWorker:
    def __init__(self,app,provider=None):
        self.app=app
        self.provider=provider or CodexProvider(app.settings.agent_workspaces,app.database)
        self.tasks: dict[str,asyncio.Task]={}
        self.renderer=MarkdownRenderer()

    def recover(self):
        with self.app.database.transaction() as db:
            requests=[dict(r) for r in db.execute("SELECT * FROM ai_requests WHERE status='running'")]
        with self.app.database.transaction() as db:
            db.execute("UPDATE ai_tasks SET status='uncertain' WHERE status='running'")
        for request in requests:
            self.settle(request,'uncertain','服务在任务执行期间退出；未自动重新生成，请重试。',charge=False)
            with self.app.database.transaction() as db:
                db.execute('UPDATE context_sessions SET thread_id=NULL,generation=generation+1 WHERE conversation_id=? AND user_id=?',
                           (request['conversation_id'],request['user_id']))

    @staticmethod
    def allowed(db,row) -> bool:
        if not can_send(db,dict(row,actor_id=row['user_id'])):
            return False
        conversation=db.execute('SELECT ai_enabled FROM conversations WHERE id=?',(row['conversation_id'],)).fetchone()
        return bool(conversation and conversation['ai_enabled'])

    async def pump(self,bots):
        for identifier,task in list(self.tasks.items()):
            if task.done():
                # run() contains failures; retrieve any unexpected exception without leaking task inputs.
                if not task.cancelled():
                    error = task.exception()
                    if error is not None:
                        logger.error('AI task escaped failure handling: request={} error_type={}', identifier, type(error).__name__)
                        with self.app.database.transaction() as db:
                            row = db.execute('SELECT * FROM ai_requests WHERE id=?', (identifier,)).fetchone()
                        if row is not None and row['status'] == 'running':
                            self.settle(dict(row), 'failed', 'AI 任务异常停止，已释放预留，请重试。', charge=False)
                            with self.app.database.transaction() as db:
                                db.execute('UPDATE context_sessions SET thread_id=NULL WHERE conversation_id=? AND user_id=? AND generation=?',
                                           (row['conversation_id'], row['user_id'], row['generation']))
                self.tasks.pop(identifier)
        with self.app.database.transaction() as db:
            rows=[dict(r) for r in db.execute("SELECT * FROM ai_requests WHERE status IN ('queued','running') ORDER BY created_at,id")]
            policies={r['id']:self.allowed(db,r) and self.app.settings.ai_enabled for r in rows}
        active_users={r['user_id'] for r in rows if r['id'] in self.tasks}
        for row in rows:
            cancelled=row['cancel_requested'] or not policies[row['id']]
            if row['id'] in self.tasks:
                if cancelled and not self.tasks[row['id']].cancelling():
                    self.tasks[row['id']].cancel()
                continue
            if row['status']!='queued':
                continue
            if cancelled:
                self.settle(row,'cancelled','任务已取消。',charge=False)
                continue
            if time.time()-row['created_at']>self.app.settings.ai_queue_timeout:
                self.settle(row,'failed','AI 排队超时，未扣除额度。',charge=False)
                continue
            with self.app.database.transaction() as db:
                spent=db.execute('SELECT coalesce(sum(tokens),0) FROM ai_requests WHERE business_date=?',(row['business_date'],)).fetchone()[0]
            if spent>=self.app.settings.ai_daily_tokens:
                self.settle(row,'failed','AI 今日全局预算已用尽，已释放预留。',charge=False)
                continue
            if len(self.tasks)>=self.app.settings.ai_concurrency or row['user_id'] in active_users or row['bot_id'] not in bots:
                continue
            with self.app.database.transaction() as db:
                changed=db.execute("UPDATE ai_requests SET status='running' WHERE id=? AND status='queued'",(row['id'],)).rowcount
            if changed:
                self.tasks[row['id']]=asyncio.create_task(self.run(row,bots[row['bot_id']]))
                active_users.add(row['user_id'])

    async def run(self,request,bot):
        turn=None
        begin_task=None
        result_task=None
        outcome=None
        generated=False
        raw_result=None
        tokens=0
        phase='media'
        started_at=time.monotonic()
        logger.info('AI request started: request={} model={}', request['id'], request['model'])
        try:
            async with asyncio.timeout(self.app.settings.ai_timeout):
                workspace=Workspaces(self.app.settings.agent_workspaces).create()
                envelope=json.loads(request['input'])
                images=await prepare_media(bot,envelope,workspace)
                phase='provider_begin'
                with self.app.database.transaction() as db:
                    current=db.execute('SELECT * FROM context_sessions WHERE conversation_id=? AND user_id=?',
                                       (request['conversation_id'],request['user_id'])).fetchone()
                    persistent=request['conversation_id'].startswith('private:')
                    thread_id=current['thread_id'] if persistent and current['generation']==request['generation'] else None
                    db.execute('UPDATE ai_requests SET workspace_id=? WHERE id=?',(workspace.id,request['id']))
                begin_task=asyncio.create_task(self.provider.begin(request,workspace,json.dumps(envelope,ensure_ascii=False),images,thread_id))
                thread,turn=await asyncio.shield(begin_task)
                logger.info('AI provider turn started: request={} thread={} turn={}', request['id'], thread.id, turn.id)
                with self.app.database.transaction() as db:
                    db.execute('UPDATE ai_requests SET started=1,thread_id=?,turn_id=? WHERE id=?',(thread.id,turn.id,request['id']))
                    if persistent:
                        db.execute('UPDATE context_sessions SET thread_id=? WHERE conversation_id=? AND user_id=? AND generation=?',
                                   (thread.id,request['conversation_id'],request['user_id'],request['generation']))
                result_task=asyncio.create_task(turn.run())
                phase='generation'
                result=await asyncio.shield(result_task)
                if result.status.value!='completed' or not result.final_response:
                    raise RuntimeError('Provider did not complete')
                generated=True
                raw_result=result.final_response
                tokens=result.usage.last.total_tokens if result.usage else 0
                payload=json.loads(result.final_response)
                phase='formatting'
                blocks=await self.format_blocks(payload,workspace)
                tokens=result.usage.last.total_tokens if result.usage else 0
                self.settle(request,'succeeded',None,charge=True,result=result.final_response,blocks=blocks,tokens=tokens)
        except asyncio.CancelledError:
            with self.app.database.transaction() as db:
                latest=dict(db.execute('SELECT * FROM ai_requests WHERE id=?',(request['id'],)).fetchone())
            user_cancel=bool(latest['cancel_requested'])
            outcome=('cancelled' if user_cancel else 'failed',
                     '任务已取消。' if user_cancel else '任务因服务或功能状态变化而停止，未扣除额度。',user_cancel)
        except Exception as exc:
            # Exception messages/traceback locals can contain chat text or provider credentials.
            logger.error('AI request failed: request={} phase={} error_type={} generated={}',
                         request['id'], phase, type(exc).__name__, generated)
            text=str(exc) if isinstance(exc,ValueError) and not isinstance(exc,json.JSONDecodeError) else type(exc).__name__
            outcome=('failed',f'生成结果处理失败：{text[:160]}。结果已保留。' if generated else f'AI 请求失败：{text[:160]}。未扣除额度。',generated)
        finally:
            if begin_task is not None and turn is None:
                try:
                    _,turn=await asyncio.wait_for(asyncio.shield(begin_task),timeout=10)
                except BaseException:
                    begin_task.cancel()
            if turn is not None and (result_task is None or not result_task.done()):
                try:
                    await asyncio.wait_for(turn.interrupt(),timeout=10)
                except Exception:
                    pass
                if result_task is not None:
                    try:
                        await asyncio.wait_for(asyncio.shield(result_task),timeout=5)
                    except BaseException:
                        result_task.cancel()
            if outcome is not None:
                state,text,user_cancel=outcome
                if turn is not None:
                    with self.app.database.transaction() as db:
                        db.execute('UPDATE ai_requests SET started=1 WHERE id=?',(request['id'],))
                self.settle(request,state,text,charge=bool(user_cancel and (state=='failed' or turn is not None)),result=raw_result,tokens=tokens)
            # Interrupted/failed private turns may contain partial user/tool state. Start a clean thread next time.
            with self.app.database.transaction() as db:
                status=db.execute('SELECT status FROM ai_requests WHERE id=?',(request['id'],)).fetchone()[0]
                if status!='succeeded':
                    db.execute('UPDATE context_sessions SET thread_id=NULL WHERE conversation_id=? AND user_id=? AND generation=?',
                               (request['conversation_id'],request['user_id'],request['generation']))
            logger.info('AI request finished: request={} status={} elapsed_s={:.1f} tokens={}',
                        request['id'], status, time.monotonic()-started_at, tokens)

    async def format_blocks(self,payload,workspace):
        raw=payload.get('blocks')
        if not isinstance(raw,list) or not 1<=len(raw)<=8:
            raise ValueError('AI 输出格式无效')
        blocks=[]
        for item in raw:
            if item.get('type')=='markdown':
                text=item.get('text')
                if not isinstance(text,str) or not text or len(text)>40000:
                    raise ValueError('AI 输出文本大小无效')
                folder=workspace.path/'output'/('render_'+__import__('uuid').uuid4().hex)
                try:
                    images=await self.renderer.render(text,folder)
                    blocks.extend([[{'type':'image','data':{'file':p.as_uri()}}] for p in images])
                except asyncio.CancelledError:
                    raise
                except Exception:
                    folder.mkdir(parents=True,exist_ok=True)
                    source=folder/'answer.md'
                    source.write_text(text,encoding='utf-8')
                    blocks.append([{'type':'text','data':{'text':'图片渲染暂不可用，已提供 Markdown 源文件。'}}])
                    blocks.append([{'type':'_upload_file','data':{'path':str(source),'name':'answer.md'}}])
            else:
                blocks.extend(self.validate_blocks({'blocks':[item]},workspace))
        if len(blocks)>32:raise ValueError('AI 输出消息过多')
        return blocks

    @staticmethod
    def validate_blocks(payload,workspace):
        raw=payload.get('blocks')
        if not isinstance(raw,list) or not 1<=len(raw)<=8:
            raise ValueError('AI 输出格式无效')
        blocks=[]
        for block in raw:
            kind=block.get('type')
            if kind in {'text','markdown'}:
                text=block.get('text')
                if not isinstance(text,str) or not text or len(text)>40000:
                    raise ValueError('AI 输出文本大小无效')
                for start in range(0,len(text),1500):
                    blocks.append([{'type':'text','data':{'text':text[start:start+1500]}}])
            elif kind=='artifact':
                path=workspace.artifact(block.get('path',''))
                if path.stat().st_size>10*1024*1024:
                    raise ValueError('AI 输出文件超过 10 MiB')
                name=Path(block.get('filename') or path.name).name
                if not name or len(name)>120 or any(ord(c)<32 for c in name):
                    raise ValueError('AI 输出文件名无效')
                blocks.append([{'type':'_upload_file','data':{'path':str(path),'name':name}}])
            else:
                raise ValueError('AI 输出类型无效')
        if len(blocks)>32:
            raise ValueError('AI 输出消息过多')
        return blocks

    def settle(self,request,status,text,charge=False,result=None,blocks=None,tokens=0):
        with self.app.database.transaction() as db:
            latest=db.execute('SELECT * FROM ai_requests WHERE id=?',(request['id'],)).fetchone()
            if latest is None or latest['status'] not in {'queued','running'}:
                return
            charged=int(charge and latest['reserved'])
            if latest['reserved']:
                db.execute('UPDATE quota_daily SET reserved=max(0,reserved-1),used=used+? WHERE user_id=? AND business_date=?',
                           (charged,latest['user_id'],latest['business_date']))
                AIRequests.ledger(db,latest['user_id'],latest['business_date'],latest['id'],
                                  'charge' if charged else 'release',charged or 1,time.time())
            db.execute('UPDATE ai_requests SET status=?,charged=?,result=?,tokens=?,error=? WHERE id=?',
                       (status,charged,result,tokens,None if status=='succeeded' else text,latest['id']))
            session=db.execute('SELECT generation FROM context_sessions WHERE conversation_id=? AND user_id=?',
                               (latest['conversation_id'],latest['user_id'])).fetchone()
            if session and session['generation']!=latest['generation']:
                return
            payloads=blocks if blocks is not None else [[{'type':'text','data':{'text':text}}]] if text else []
            for index,payload in enumerate(payloads):
                db.execute('''INSERT OR IGNORE INTO outbox(dedup_key,bot_id,conversation_id,actor_id,payload,created_at)
                              VALUES(?,?,?,?,?,?)''',(f'ai:{latest["id"]}:{index}',latest['bot_id'],latest['conversation_id'],
                              latest['user_id'],json.dumps(payload,ensure_ascii=False),time.time()))

    async def close(self):
        for task in self.tasks.values():
            if not task.cancelling():
                task.cancel()
        await asyncio.gather(*self.tasks.values(),return_exceptions=True)
        self.tasks.clear()
        await self.provider.close()

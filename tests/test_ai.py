from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime
import asyncio
import json
import time
from types import SimpleNamespace

import pytest

from bot.config import load_settings, SUPERUSER
from bot.domain.messages import Incoming
from bot.services.application import Application
from bot.ai.worker import AIWorker
from bot.workers.outbox import Outbox

USER='12345678'


def incoming(mid,text='/ask hello',user=USER,group=None,segments=None):
    now=time.time()
    return Incoming('3653125828','group' if group else 'private',group or user,user,str(mid),int(now),now,
                    'tester','',segments or [{'type':'text','data':{'text':text}}],text)


@pytest.fixture
def ai_app(tmp_path):
    return Application(replace(load_settings(),database=tmp_path/'ai.db',archives=tmp_path/'archives',
                              ai_policy=tmp_path/'policy.toml',agent_workspaces=tmp_path/'tasks',
                              command_interval=0,send_interval=0))


def read(app,sql,args=()):
    db=app.database.connect()
    try:return [dict(r) for r in db.execute(sql,args)]
    finally:db.close()


class FakeTurn:
    def __init__(self,gate=None,failure=False):
        self.id='turn'
        self.gate=gate
        self.failure=failure
        self.interrupted=False
    async def run(self):
        if self.gate: await self.gate.wait()
        if self.failure: raise RuntimeError('provider failed')
        return SimpleNamespace(status=SimpleNamespace(value='completed'),usage=SimpleNamespace(last=SimpleNamespace(total_tokens=42)),
                               final_response=json.dumps({'blocks':[{'type':'text','text':'AI_OK','path':'','filename':''}]}))
    async def interrupt(self):
        self.interrupted=True
        if self.gate:self.gate.set()


class FakeProvider:
    def __init__(self,gate=None,failure=False):
        self.calls=[]
        self.gate=gate
        self.failure=failure
        self.turns=[]
    async def begin(self,request,workspace,prompt,images,thread_id):
        self.calls.append({'user':request['user_id'],'thread_id':thread_id,'prompt':json.loads(prompt)})
        turn=FakeTurn(self.gate,self.failure)
        self.turns.append(turn)
        return SimpleNamespace(id='thread_'+request['user_id']),turn
    async def close(self):pass


async def drain(worker,bot=None):
    for _ in range(30):
        await worker.pump({'3653125828':bot or object()})
        if not worker.tasks:break
        await asyncio.sleep(0.005)


def test_concurrent_quota_reservation_does_not_overspend(ai_app):
    ai_app.settings.ai_policy.write_text('[quota]\ndefault_user_daily_limit=2\n',encoding='utf-8')
    with ThreadPoolExecutor(max_workers=12) as pool:
        list(pool.map(lambda i:ai_app.process(incoming(i)),range(16)))
    assert len(read(ai_app,'SELECT * FROM ai_requests'))==2
    assert read(ai_app,'SELECT reserved FROM quota_daily')[0]['reserved']==2


def test_generation_success_is_charged_before_qq_delivery(ai_app):
    ai_app.process(incoming(1))
    worker=AIWorker(ai_app,FakeProvider())
    asyncio.run(drain(worker))
    request=read(ai_app,'SELECT * FROM ai_requests')[0]
    assert request['status']=='succeeded' and request['charged']==1 and request['tokens']==42
    assert read(ai_app,'SELECT used,reserved FROM quota_daily')[0]=={'used':1,'reserved':0}
    assert read(ai_app,'SELECT status FROM outbox')[0]['status']=='pending'
    worker.settle(request,'succeeded',None,charge=True)
    assert read(ai_app,'SELECT used FROM quota_daily')[0]['used']==1


def test_provider_failure_and_cancel_before_generation_release(ai_app):
    ai_app.process(incoming(1))
    worker=AIWorker(ai_app,FakeProvider(failure=True))
    asyncio.run(drain(worker))
    assert read(ai_app,'SELECT used,reserved FROM quota_daily')[0]=={'used':0,'reserved':0}
    ai_app.process(incoming(2))
    ai_app.process(incoming(3,'/cancel'))
    asyncio.run(drain(worker))
    assert read(ai_app,'SELECT used,reserved FROM quota_daily')[0]=={'used':0,'reserved':0}


def test_running_cancel_interrupts_sdk_and_charges_once(ai_app):
    async def scenario():
        gate=asyncio.Event()
        provider=FakeProvider(gate=gate)
        worker=AIWorker(ai_app,provider)
        ai_app.process(incoming(1))
        await worker.pump({'3653125828':object()})
        for _ in range(20):
            if read(ai_app,'SELECT started FROM ai_requests')[0]['started']:break
            await asyncio.sleep(0.005)
        ai_app.process(incoming(2,'/cancel'))
        await drain(worker)
        assert provider.turns[0].interrupted
        await worker.close()
    asyncio.run(scenario())
    assert read(ai_app,'SELECT status,charged FROM ai_requests')[0]=={'status':'cancelled','charged':1}
    assert read(ai_app,'SELECT used,reserved FROM quota_daily')[0]=={'used':1,'reserved':0}


def test_private_threads_are_per_user_and_reset_generation(ai_app):
    provider=FakeProvider()
    worker=AIWorker(ai_app,provider)
    async def scenario():
        ai_app.process(incoming(1))
        await drain(worker)
        ai_app.process(incoming(2))
        await drain(worker)
        ai_app.process(incoming(3,user='22345678'))
        await drain(worker)
        ai_app.process(incoming(4,'/new'))
        ai_app.process(incoming(5))
        await drain(worker)
    asyncio.run(scenario())
    assert [r['thread_id'] for r in provider.calls]==[None,'thread_'+USER,None,None]


def test_group_at_rule_context_one_shot_and_scope(ai_app):
    group='1049251004'
    ai_app.process(incoming('watch','/watch add '+group,SUPERUSER),{group})
    ai_app.process(incoming('on','/ai on',SUPERUSER,group))
    ai_app.process(incoming('history','group history',USER,group))
    ai_app.process(incoming('private','private secret','22345678'))
    ai_app.process(incoming('ctx','/context group 20',USER,group))
    assert len(read(ai_app,'SELECT * FROM ai_requests'))==1  # private natural text only
    segments=[{'type':'at','data':{'qq':'3653125828'}},{'type':'text','data':{'text':'question'}}]
    ai_app.process(incoming('at','question',USER,group,segments))
    request=read(ai_app,'SELECT input FROM ai_requests WHERE conversation_id LIKE ?',('group:%',))[0]
    envelope=json.loads(request['input'])
    assert envelope['context']
    assert all(row['conversation_id']=='group:3653125828:'+group for row in envelope['context'])
    assert 'private secret' not in request['input']
    assert read(ai_app,'SELECT context_count FROM context_sessions WHERE conversation_id LIKE ?',('group:%',))[0]['context_count']==0
    ai_app.process(incoming('plain','question',USER,group))
    assert len(read(ai_app,'SELECT * FROM ai_requests WHERE conversation_id LIKE ?',('group:%',)))==1


def test_vip_bypasses_business_quota_and_policy_updates_are_audited(ai_app):
    ai_app.process(incoming('limit','/quota-admin limit default 0',SUPERUSER))
    assert ai_app.ai.policy.limit(USER)==0
    assert read(ai_app,'SELECT status FROM policy_changes')[0]['status']=='applied'
    ai_app.process(incoming(1))
    assert not read(ai_app,'SELECT * FROM ai_requests')
    ai_app.process(incoming('vip','/vip add '+USER,SUPERUSER))
    ai_app.process(incoming(2))
    worker=AIWorker(ai_app,FakeProvider())
    asyncio.run(drain(worker))
    assert read(ai_app,'SELECT reserved,charged FROM ai_requests')[0]=={'reserved':0,'charged':0}


def test_policy_crash_after_file_publish_can_recover(ai_app):
    event=incoming(1,user=SUPERUSER)
    with ai_app.database.transaction() as db:
        ai_app.ai.policy.prepare(db,event.event_key,SUPERUSER,'default',9)
        change=dict(db.execute('SELECT * FROM policy_changes').fetchone())
    ai_app.settings.ai_policy.write_text(change['content'],encoding='utf-8')
    ai_app.ai.policy.apply_pending(ai_app.database)
    assert ai_app.ai.policy.limit(USER)==9
    assert read(ai_app,'SELECT status FROM policy_changes')[0]['status']=='applied'


def test_restart_does_not_replay_running_inference(ai_app):
    ai_app.process(incoming(1))
    with ai_app.database.transaction() as db:db.execute("UPDATE ai_requests SET status='running',started=1")
    provider=FakeProvider()
    worker=AIWorker(ai_app,provider)
    worker.recover()
    asyncio.run(drain(worker))
    assert not provider.calls
    assert read(ai_app,'SELECT status FROM ai_requests')[0]['status']=='uncertain'
    assert read(ai_app,'SELECT reserved FROM quota_daily')[0]['reserved']==0

def test_completed_ai_result_is_suppressed_when_group_ai_disabled(ai_app):
    group='1049251004'
    ai_app.process(incoming('watch','/watch add '+group,SUPERUSER),{group})
    ai_app.process(incoming('on','/ai on',SUPERUSER,group))
    ai_app.process(incoming('q','/ask hello',USER,group))
    worker=AIWorker(ai_app,FakeProvider())
    asyncio.run(drain(worker))
    ai_app.process(incoming('off','/ai off',SUPERUSER,group))
    class Bot:
        async def call_api(self,*args,**kwargs):return {'message_id':777}
    for _ in range(6):asyncio.run(Outbox(ai_app.database).deliver_one({'3653125828':Bot()}))
    assert read(ai_app,"SELECT status FROM outbox WHERE dedup_key LIKE 'ai:%'")[0]['status']=='suppressed'
    assert read(ai_app,'SELECT used FROM quota_daily WHERE user_id=?',(USER,))[0]['used']==1


def test_ai_file_delivery_accepts_upload_api_without_message_id(ai_app,tmp_path):
    ai_app.process(incoming(1))
    with ai_app.database.transaction() as db:
        request=dict(db.execute('SELECT * FROM ai_requests').fetchone())
    result_file=tmp_path/'result.txt'
    result_file.write_text('synthetic')
    AIWorker(ai_app,FakeProvider()).settle(request,'succeeded',None,charge=True,
        blocks=[[{'type':'_upload_file','data':{'path':str(result_file),'name':'result.txt'}}]])
    class Bot:
        async def call_api(self,action,**params):
            assert action=='upload_private_file'
            assert params['file']==str(result_file)
            return {}
    assert asyncio.run(Outbox(ai_app.database).deliver_one({'3653125828':Bot()}))=='sent'


def test_escaped_task_exception_releases_reservation_and_does_not_replay(ai_app):
    ai_app.process(incoming(1))
    provider=FakeProvider()
    worker=AIWorker(ai_app,provider)
    async def unexpected_failure(request,bot):
        raise RuntimeError('PRIVATE_EXCEPTION_SENTINEL')
    worker.run=unexpected_failure
    from nonebot import logger
    records=[]
    sink=logger.add(lambda message:records.append(str(message)),format='{message}')
    try:
        asyncio.run(drain(worker))
    finally:
        logger.remove(sink)
    assert read(ai_app,'SELECT status FROM ai_requests')[0]['status']=='failed'
    assert read(ai_app,'SELECT used,reserved FROM quota_daily')[0]=={'used':0,'reserved':0}
    assert not provider.calls and not worker.tasks
    assert any('escaped failure' in message for message in records)
    assert all('PRIVATE_EXCEPTION_SENTINEL' not in message for message in records)


def test_provider_failure_logs_stage_without_input_or_exception_text(ai_app):
    ai_app.process(incoming(1,'/ask PRIVATE_INPUT_SENTINEL'))
    worker=AIWorker(ai_app,FakeProvider(failure=True))
    from nonebot import logger
    records=[]
    sink=logger.add(lambda message:records.append(str(message)),format='{message}')
    try:
        asyncio.run(drain(worker))
    finally:
        logger.remove(sink)
    assert any('phase=generation error_type=RuntimeError' in message for message in records)
    assert any('status=failed' in message for message in records)
    assert all('PRIVATE_INPUT_SENTINEL' not in message and 'provider failed' not in message for message in records)

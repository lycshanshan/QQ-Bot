from dataclasses import replace
from pathlib import Path
import asyncio,json,time
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from bot.ai.provider import OrchestratedTurn,RULES
from bot.media.render import markdown_html
from bot.workers.archive import Archiver
from bot.workers.nas_archive import ArchivePacks
from bot.workers.subscriptions import LiveSubscriptions
from bot.workers.outbox import Outbox
from bot.integrations.bilibili import BiliError
from bot.services.application import Application
from bot.config import load_settings,SUPERUSER
from bot.domain.messages import Incoming


def event(mid,text,user=SUPERUSER,group=None):
    now=time.time()
    return Incoming('3653125828','group' if group else 'private',group or user,user,str(mid),int(now),now,
                    'test','',[{'type':'text','data':{'text':text}}],text)


@pytest.fixture
def app(tmp_path):
    return Application(replace(load_settings(),database=tmp_path/'db',archives=tmp_path/'archives',
                       ai_enabled=False,command_interval=0,ai_policy=tmp_path/'policy.toml',agent_workspaces=tmp_path/'tasks'))


def records(app,sql,args=()):
    with app.database.transaction() as db:return [dict(r) for r in db.execute(sql,args)]


def subscribe(app,group='1049251004'):
    app.process(event('watch'+group,'/watch add '+group),{group})
    app.process(event('sub'+group,'/subscribe bili-live 1863474053',group=group))


def snapshot(online=False,session='one'):
    return {'room_id':'1863474053','uid':'397315493','online':online,'session':session,'title':'synthetic'}


def test_live_baseline_restart_and_session_dedup(app):
    subscribe(app)
    live=LiveSubscriptions(app)
    live.ingest('1863474053',snapshot(True),1000)
    assert not records(app,"SELECT * FROM outbox WHERE dedup_key LIKE 'live:%'")
    LiveSubscriptions(app).ingest('1863474053',snapshot(True),1100)
    assert not records(app,"SELECT * FROM outbox WHERE dedup_key LIKE 'live:%'")
    live.ingest('1863474053',snapshot(False),1200)
    live.ingest('1863474053',snapshot(True),1250)
    assert not records(app,"SELECT * FROM outbox WHERE dedup_key LIKE 'live:%'")
    live.ingest('1863474053',snapshot(True,'two'),1300)
    live.ingest('1863474053',snapshot(True,'two'),1400)
    assert len(records(app,"SELECT * FROM outbox WHERE dedup_key LIKE 'live:%'"))==1


def test_shared_live_snapshot_fans_out_and_unsubscribe_suppresses(app):
    subscribe(app)
    subscribe(app,'1049251005')
    live=LiveSubscriptions(app)
    live.ingest('1863474053',snapshot(),1000)
    live.ingest('1863474053',snapshot(True),1100)
    assert len(records(app,"SELECT * FROM outbox WHERE dedup_key LIKE 'live:%'"))==2
    app.process(event('unsub','/unsubscribe bili-live 1863474053',group='1049251004'))
    class Bot:
        async def call_api(self,*args,**kwargs):return {'message_id':123}
    for _ in range(8):asyncio.run(Outbox(app.database).deliver_one({'3653125828':Bot()}))
    status=records(app,"SELECT status FROM outbox WHERE dedup_key LIKE 'live:group:3653125828:1049251004:%'")[0]
    assert status['status']=='suppressed'


def test_bot_off_does_not_send_system_notifications_as_superuser(app):
    subscribe(app)
    live=LiveSubscriptions(app)
    live.ingest('1863474053',snapshot(),1000)
    live.ingest('1863474053',snapshot(True),1100)
    app.process(event('off','/bot off'))
    class Bot:
        async def call_api(self,*args,**kwargs):return {'message_id':123}
    for _ in range(6):asyncio.run(Outbox(app.database).deliver_one({'3653125828':Bot()}))
    assert records(app,"SELECT status FROM outbox WHERE dedup_key LIKE 'live:%'")[0]['status']=='suppressed'


def test_live_412_pauses_and_does_not_retry(app):
    subscribe(app)
    class Provider:
        calls=0
        def snapshot(self,room):
            self.calls+=1
            raise BiliError(412)
    provider=Provider()
    live=LiveSubscriptions(app,provider)
    assert asyncio.run(live.poll_one())=='failed'
    assert asyncio.run(live.poll_one()) is None
    assert provider.calls==1
    assert records(app,'SELECT paused,error FROM subscription_state')[0]=={'paused':1,'error':'412'}


def test_non_vip_cannot_subscribe_and_dynamic_stays_disabled(app):
    subscribe(app)
    app.process(event('denied','/subscribe bili-live 123456',user='12345678',group='1049251004'))
    assert len(records(app,'SELECT * FROM subscriptions'))==1
    app.process(event('dynamic','/subscribe bili-dynamic 330430013',group='1049251004'))
    assert len(records(app,'SELECT * FROM subscriptions'))==1


def archived(app):
    app.settings=replace(app.settings,message_window=1,archive_batch=1)
    for i in range(4):app.process(event(str(i),'synthetic'))
    exporter=Archiver(app.database,app.settings)
    while exporter.export_one():pass


def test_nas_pack_wait_force_and_verified_local_retention(app):
    archived(app)
    packs=ArchivePacks(app.database,app.settings,keep_local=True)
    assert packs.pack_one() is None
    identifier=packs.pack_one(force=True)
    assert identifier
    class Storage:
        def upload_verified(self,path,relative):
            assert path.is_file() and relative.startswith('archives/3653125828/private/')
            return '/home/'+relative
    assert packs.upload_one(Storage())==identifier
    assert records(app,'SELECT status FROM archive_packs')[0]['status']=='verified'
    assert len(list(app.settings.archives.glob('*.json')))==3
    assert len(list((app.settings.archives/'packs').glob('*.zip')))==1


def test_nas_upload_recovery_retains_same_pack_and_content(app):
    archived(app)
    packs=ArchivePacks(app.database,app.settings)
    identifier=packs.pack_one(force=True)
    class Broken:
        def upload_verified(self,*args):raise OSError('network down')
    assert packs.upload_one(Broken())=='retry'
    with app.database.transaction() as db:db.execute("UPDATE archive_packs SET status='uploading'")
    packs.recover()
    assert records(app,'SELECT id,status FROM archive_packs')[0]=={'id':identifier,'status':'retry'}
    assert packs.pack_one(force=True) is None
    assert list((app.settings.archives/'packs').glob('*.zip'))


def test_markdown_disables_raw_html_and_external_images():
    rendered=markdown_html('<script>window.PWNED=1</script>\n\n![x](https://evil.example/a.png)\n\n$x^2$')
    assert '<script>' not in rendered and '<img' not in rendered
    assert 'data-math="x^2"' in rendered


def test_advanced_tasks_use_child_rules_and_then_main_integration():
    def result(data):return SimpleNamespace(status=SimpleNamespace(value='completed'),final_response=json.dumps(data),usage=SimpleNamespace(last=SimpleNamespace(total_tokens=5)))
    class Turn:
        id='turn'
        def __init__(self,data):self.data=data
        async def run(self):return result(self.data)
        async def interrupt(self):pass
    class Provider:
        database=None
        calls=[]
        async def raw_begin(self,request,workspace,prompt,images,thread_id,rules,schema,effort):
            self.calls.append((request['model'],thread_id,rules,effort))
            return SimpleNamespace(id='child' if thread_id is None else thread_id),Turn({'blocks':[{'type':'text','text':'56','path':'','filename':''}]})
    provider=Provider()
    initial=Turn({'blocks':[],'advanced_tasks':[{'model':'gpt-6.1-sol','goal':'calculate 7*8'}]})
    orchestrated=OrchestratedTurn(provider,{'model':'gpt-6-luna'},object(),SimpleNamespace(id='main'),initial,'{}')
    final=asyncio.run(orchestrated.run())
    assert [entry[0] for entry in provider.calls]==['gpt-6.1-sol','gpt-6-luna']
    assert provider.calls[0][1] is None and provider.calls[0][3]=='high'
    assert provider.calls[0][2]!=RULES
    assert provider.calls[1][1]=='main'
    assert final.usage.last.total_tokens==15


def test_verified_nas_pack_cleans_only_matching_local_files(app):
    archived(app)
    packs=ArchivePacks(app.database,app.settings)
    identifier=packs.pack_one(force=True)
    class Storage:
        def upload_verified(self,path,relative):return '/home/'+relative
    assert packs.upload_one(Storage())==identifier
    assert records(app,'SELECT status FROM archive_packs')[0]['status']=='cleaned'
    assert not list(app.settings.archives.glob('*.json'))
    assert not list((app.settings.archives/'packs').glob('*.zip'))
    assert len(records(app,'SELECT * FROM archive_jobs'))==3

"""Real SDK interrupt through the production scheduler, with an isolated DB and no QQ send."""
import asyncio,json,sys,time,uuid
from pathlib import Path
from dataclasses import replace
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from bot.config import load_settings
from bot.services.application import Application
from bot.domain.messages import Incoming
from bot.ai.worker import AIWorker

def event(mid,text):
    now=time.time()
    return Incoming('3653125828','private','99999','99999',mid,int(now),now,'synthetic','',
                    [{'type':'text','data':{'text':text}}],text)

async def main():
    base=ROOT/'artifacts/feasibility/cancel-production'/uuid.uuid4().hex
    app=Application(replace(load_settings(),database=base/'db',archives=base/'archives',ai_policy=base/'policy',
                            agent_workspaces=ROOT/'agent-workspace',command_interval=0))
    worker=AIWorker(app)
    app.process(event('ask','/ask 合成取消测试：请先使用 shell 执行 Python，在 scratch/cancel-started 写入 started，然后 sleep 20 秒，最后回复 CANCEL_TEST_DONE。只操作当前任务目录，不访问网络。'))
    try:
        for _ in range(600):
            await worker.pump({'3653125828':object()})
            with app.database.transaction() as db:request=dict(db.execute('SELECT * FROM ai_requests').fetchone())
            if request['started'] and request['workspace_id'] and (app.settings.agent_workspaces/request['workspace_id']/'scratch/cancel-started').exists():break
            await asyncio.sleep(0.05)
        if not request['started'] or not (app.settings.agent_workspaces/request['workspace_id']/'scratch/cancel-started').exists():raise RuntimeError('Synthetic command did not start')
        app.process(event('cancel','/cancel'))
        for _ in range(400):
            await worker.pump({'3653125828':object()})
            if not worker.tasks:break
            await asyncio.sleep(0.05)
        with app.database.transaction() as db:
            request=dict(db.execute('SELECT * FROM ai_requests').fetchone())
            quota=dict(db.execute('SELECT * FROM quota_daily').fetchone())
        thread=await worker.provider.client.thread_resume(request['thread_id'])
        state=await thread.read(include_turns=True)
        sdk_status=state.thread.turns[-1].status.value
        for _ in range(40):
            if sdk_status!='inProgress':break
            await asyncio.sleep(0.25)
            state=await thread.read(include_turns=True)
            sdk_status=state.thread.turns[-1].status.value
        report={'status':request['status'],'charged':request['charged'],'reserved':quota['reserved'],'sdk_turn_status':sdk_status}
        print(json.dumps(report))
        (ROOT/'artifacts/feasibility/production-cancel-result.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
        assert request['status']=='cancelled' and request['charged']==1 and quota['reserved']==0 and sdk_status=='interrupted'
    finally:await worker.close()

if __name__=='__main__':asyncio.run(main())

"""End-to-end local provider smoke: synthetic file, no QQ send and no credentials in prompts."""
import asyncio,json,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from bot.ai.workspace import Workspaces
from bot.ai.provider import CodexProvider
from bot.ai.worker import AIWorker

async def main():
    workspace=Workspaces(ROOT/'agent-workspace').create()
    class RecordingProvider(CodexProvider):
        def __init__(self,*args):
            super().__init__(*args)
            self.models_used=[]
        async def raw_begin(self,request,*args,**kwargs):
            self.models_used.append(request['model'])
            return await super().raw_begin(request,*args,**kwargs)
    provider=RecordingProvider(ROOT/'agent-workspace')
    turn=None
    try:
        thread,turn=await asyncio.wait_for(provider.begin({'model':'gpt-6-luna'},workspace,
            json.dumps({'input':'合成高级任务测试：必须先提出一个 model=gpt-6.1-sol 的 advanced_task，让它计算 7*8 并在 output/sdk-smoke.txt 写入 ASCII 文本 QQ_BOT_AGENT_OK。后端完成高级任务后，请整合结果，返回一个 text 块说明 7*8 和一个 artifact 块交付 sdk-smoke.txt。只操作当前任务目录，不要读取别处文件，不要访问网络。','limits':{'default_high_model':'gpt-6.1-sol'}},ensure_ascii=False),[],None),timeout=45)
        result=await asyncio.wait_for(turn.run(),timeout=90)
        data=json.loads(result.final_response)
        blocks=AIWorker.validate_blocks(data,workspace)
        report={'models_used':provider.models_used,'status':result.status.value,'workspace_id':workspace.id,'file_exists':(workspace.path/'output/sdk-smoke.txt').is_file(),
                'block_types':[b[0]['type'] for b in blocks],'tokens':result.usage.last.total_tokens if result.usage else None}
        if report['file_exists']:
            report['file_content_ok']='QQ_BOT_AGENT_OK' in (workspace.path/'output/sdk-smoke.txt').read_text(encoding='utf-8-sig')
        print(json.dumps(report,ensure_ascii=True))
        (ROOT/'artifacts/feasibility/sdk-advanced-smoke.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    finally:
        if turn:
            try: await asyncio.wait_for(turn.interrupt(),timeout=5)
            except Exception: pass
        await provider.close()

if __name__=='__main__': asyncio.run(main())

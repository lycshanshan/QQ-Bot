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
    provider=CodexProvider(ROOT/'agent-workspace')
    turn=None
    try:
        thread,turn=await asyncio.wait_for(provider.begin({'model':'gpt-6-luna'},workspace,
            '合成连通测试：只操作当前任务目录。请使用 shell 在 output/sdk-smoke.txt 写入 ASCII 文本 QQ_BOT_AGENT_OK。然后返回一个 text 块说明完成和一个 artifact 块交付 sdk-smoke.txt。不要读取别处文件，不要访问网络。',[],None),timeout=45)
        result=await asyncio.wait_for(turn.run(),timeout=90)
        data=json.loads(result.final_response)
        blocks=AIWorker.validate_blocks(data,workspace)
        report={'status':result.status.value,'workspace_id':workspace.id,'file_exists':(workspace.path/'output/sdk-smoke.txt').is_file(),
                'block_types':[b[0]['type'] for b in blocks],'tokens':result.usage.last.total_tokens if result.usage else None}
        if report['file_exists']:
            report['file_content_ok']='QQ_BOT_AGENT_OK' in (workspace.path/'output/sdk-smoke.txt').read_text(encoding='utf-8-sig')
        print(json.dumps(report,ensure_ascii=True))
        (ROOT/'artifacts/feasibility/sdk-production-smoke.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    finally:
        if turn:
            try: await asyncio.wait_for(turn.interrupt(),timeout=5)
            except Exception: pass
        await provider.close()

if __name__=='__main__': asyncio.run(main())

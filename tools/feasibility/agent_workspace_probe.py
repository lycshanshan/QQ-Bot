"""Synthetic command sandbox test: never reads real credentials or calls a model."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import uuid

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from bot.ai.workspace import Workspaces


def run(implementation: str, runtime: Path) -> dict:
    workspace = Workspaces(ROOT/'agent-workspace').create()
    out = ROOT/'artifacts/feasibility/agent-workspace'
    out.mkdir(parents=True, exist_ok=True)
    canary = out/(uuid.uuid4().hex+'.txt')
    canary.write_text('SYNTHETIC_OUTSIDE_CANARY', encoding='utf-8')
    sibling = Workspaces(ROOT/'agent-workspace').create()
    sibling_file = sibling.path/'sibling-canary.txt'
    sibling_file.write_text('SYNTHETIC_OTHER_TASK_CANARY', encoding='utf-8')
    # All paths are generated locally; no user input enters the PowerShell program.
    def ps(value: Path) -> str:
        return "'"+str(value).replace("'", "''")+"'"
    script = workspace.path/'probe.ps1'
    script.write_text(f'''$ErrorActionPreference = 'Stop'
$result = @{{}}
try {{ Set-Content -LiteralPath 'output/write-ok.txt' -Value 'WORKSPACE_WRITE_OK'; $result.inside_write=$true }} catch {{ $result.inside_write=$false }}
try {{ $null = Get-Content -LiteralPath {ps(canary)}; $result.outside_read_denied=$false }} catch {{ $result.outside_read_denied=$true }}
try {{ Add-Content -LiteralPath {ps(canary)} -Value 'SHOULD_NOT_WRITE'; $result.outside_write_denied=$false }} catch {{ $result.outside_write_denied=$true }}
try {{ $null = Get-Content -LiteralPath {ps(sibling_file)}; $result.sibling_read_denied=$false }} catch {{ $result.sibling_read_denied=$true }}
$result | ConvertTo-Json -Compress
''', encoding='utf-8')
    command = [str(runtime)]
    for override in workspace.permission_overrides(implementation):
        command.extend(['-c',override])
    command.extend(['sandbox','--include-managed-config','--permission-profile','qqbot_task','--',
                    'powershell.exe','-NoProfile','-NonInteractive','-File',str(script)])
    report = {'implementation':implementation, 'workspace_id':workspace.id, 'success':False}
    try:
        completed = subprocess.run(command, cwd=workspace.path, capture_output=True, encoding='utf-8', errors='replace', timeout=45)
        report.update(returncode=completed.returncode, stdout=completed.stdout[-4000:], stderr=completed.stderr[-6000:])
        if completed.returncode == 0:
            result = json.loads(completed.stdout.strip().splitlines()[-1])
            report['checks'] = result
            report['success'] = (all(result.get(k) is True for k in ['inside_write','outside_read_denied','outside_write_denied','sibling_read_denied'])
                                  and (workspace.path/'output/write-ok.txt').is_file()
                                  and canary.read_text(encoding='utf-8') == 'SYNTHETIC_OUTSIDE_CANARY')
    except Exception as exc:
        report.update(error_type=type(exc).__name__, error=str(exc)[:2000])
    (out/(implementation+'-result.json')).write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    return report


def main() -> None:
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--implementation',choices=['mxc','elevated'],required=True)
    parser.add_argument('--runtime',type=Path,default=ROOT/'artifacts/feasibility/sdk-venv/Lib/site-packages/codex_cli_bin/bin/codex.exe')
    args=parser.parse_args()
    print(json.dumps(run(args.implementation,args.runtime.resolve()),ensure_ascii=True,indent=2))


if __name__=='__main__':
    main()

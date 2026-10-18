from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import sqlite3
import tomllib


def digest(text: str) -> str:
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


class Policy:
    def __init__(self, path: Path):
        self.path=path

    def read(self) -> tuple[int, dict[str,int], int, str]:
        text=self.path.read_text(encoding='utf-8') if self.path.exists() else ''
        data=tomllib.loads(text) if text else {}
        quota=data.get('quota',{})
        default=quota.get('default_user_daily_limit',20)
        overrides=quota.get('user_daily_limits',{})
        if type(default) is not int or default<0 or any(type(v) is not int or v<0 for v in overrides.values()):
            raise ValueError('Quota limits must be nonnegative integers')
        if quota.get('reset_timezone','Asia/Shanghai')!='Asia/Shanghai' or quota.get('reset_hour',0)!=0:
            raise ValueError('Quota resets at Shanghai midnight')
        return default,overrides,data.get('revision',0),text

    def limit(self,user_id: str) -> int:
        default,overrides,_,_=self.read()
        return overrides.get(user_id,default)

    def prepare(self,db: sqlite3.Connection,event_key: str,actor: str,target: str,value: int) -> None:
        default,overrides,revision,old=self.read()
        if target=='default':
            default=value
        else:
            overrides=dict(overrides)
            overrides[target]=value
        text=(f'schema_version = 1\nrevision = {revision+1}\n\n[quota]\ndefault_user_daily_limit = {default}\n'
              'charge_per_successful_request = 1\ncharge_advanced_task_extra = 0\ncharge_provider_failure = false\n'
              'charge_cancel_before_generation = false\ncharge_cancel_after_generation = true\n'
              'reset_timezone = "Asia/Shanghai"\nreset_hour = 0\n\n[quota.user_daily_limits]\n')
        text+=''.join(f'{json.dumps(k)} = {v}\n' for k,v in sorted(overrides.items()))
        db.execute('INSERT INTO policy_changes(event_key,actor_id,old_hash,new_hash,content) VALUES(?,?,?,?,?)',
                   (event_key,actor,digest(old),digest(text),text))

    def apply_pending(self,database) -> None:
        with database.transaction() as db:
            changes=[dict(r) for r in db.execute("SELECT * FROM policy_changes WHERE status='pending' ORDER BY id")]
        for change in changes:
            try:
                current=self.path.read_text(encoding='utf-8') if self.path.exists() else ''
                current_hash=digest(current)
                if current_hash not in {change['old_hash'],change['new_hash']}:
                    raise ValueError('Policy changed externally')
                if current_hash!=change['new_hash']:
                    self.path.parent.mkdir(parents=True,exist_ok=True)
                    temporary=self.path.with_suffix('.tmp')
                    with temporary.open('w',encoding='utf-8',newline='\n') as stream:
                        stream.write(change['content'])
                        stream.flush()
                        os.fsync(stream.fileno())
                    os.replace(temporary,self.path)
                state,text='applied','额度策略已更新，对后续请求生效。'
            except Exception:
                state,text='rejected','额度策略更新失败，保留现有策略；请检查配置文件。'
            with database.transaction() as db:
                db.execute('UPDATE policy_changes SET status=? WHERE id=?',(state,change['id']))
                db.execute("UPDATE outbox SET payload=? WHERE dedup_key=? AND status='pending'",
                           (json.dumps([{'type':'text','data':{'text':text}}],ensure_ascii=False),change['event_key']+':reply'))
                db.execute('INSERT INTO audit_log(actor_id,action,target,detail,created_at) VALUES(?,?,?,?,unixepoch())',
                           (change['actor_id'],'quota_policy',str(change['id']),state))

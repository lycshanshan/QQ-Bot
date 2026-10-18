"""Send a labelled acceptance message only to the two explicitly approved targets."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from bot.config import load_settings, SUPERUSER
from bot.repositories.database import Database


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('target', choices=['private', 'group'])
    args = parser.parse_args()
    kind = args.target
    target = SUPERUSER if kind == 'private' else '1049251004'
    bot_id = '3653125828'
    scope = f'{kind}:{bot_id}:{target}'
    database = Database(load_settings().database)
    with database.transaction() as db:
        if kind == 'group':
            row = db.execute('SELECT watched FROM conversations WHERE id=?', (scope,)).fetchone()
            if not row or not row['watched']:
                parser.error('请先由管理员私聊执行 /watch add 1049251004，再发送群测试。')
        else:
            db.execute('''INSERT OR IGNORE INTO conversations(id,bot_id,kind,target_id,watched,ai_enabled)
                          VALUES(?,?,?,?,1,1)''', (scope, bot_id, kind, target))
        label = 'P1/P2 开发验收测试：机器人已重启。请发送 /ping、/帮助、/状态 验证基础命令。'
        if kind == 'private':
            label += '\n私聊执行 /watch add 1049251004 后，在群内执行 /checkin on，再发送 晚安（当前为夜间），最后 /打卡查询。'
        else:
            label += '\n管理员开启 /checkin on 后，可发送 晚安、晚安喵 验证同一时段不重复计数。'
        cursor = db.execute('''INSERT INTO outbox(dedup_key,bot_id,conversation_id,actor_id,payload,created_at)
                               VALUES(?,?,?,?,?,?)''', ('acceptance:'+uuid.uuid4().hex, bot_id, scope, SUPERUSER,
                               json.dumps([{'type':'text','data':{'text':label}}], ensure_ascii=False), time.time()))
        db.execute("INSERT INTO audit_log(actor_id,action,target,detail,created_at) VALUES('local_operator','acceptance_send',?,'approved_targets_only',?)",
                   (scope, time.time()))
        print(f'Queued acceptance outbox ID {cursor.lastrowid}')


if __name__ == '__main__':
    main()

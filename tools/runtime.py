"""Local operator commands. Does not expose a network/admin API."""
from __future__ import annotations

import argparse
from pathlib import Path
import sys
import sqlite3

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from bot.config import load_settings
from bot.repositories.database import Database


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    sub.add_parser('outbox')
    for command in ('retry', 'suppress'):
        item = sub.add_parser(command)
        item.add_argument('id', type=int)
        if command == 'retry':
            item.add_argument('--confirm-not-delivered', action='store_true', required=True)
    backup = sub.add_parser('backup')
    backup.add_argument('destination', type=Path)
    args = parser.parse_args()
    settings = load_settings()
    if args.command == 'backup':
        if args.destination.exists():
            parser.error('Refusing to overwrite an existing backup')
        if not settings.database.is_file():
            parser.error('Source database does not exist')
        args.destination.parent.mkdir(parents=True,exist_ok=True)
        source=sqlite3.connect(settings.database)
        target=sqlite3.connect(args.destination)
        try:source.backup(target)
        finally:target.close();source.close()
        print('Backup completed')
        return
    database=Database(settings.database)
    with database.transaction() as db:
        if args.command == 'outbox':
            for row in db.execute("SELECT id,conversation_id,status,attempts,error,message_id FROM outbox WHERE status NOT IN ('sent','suppressed') ORDER BY id"):
                print(dict(row))
        else:
            row = db.execute('SELECT * FROM outbox WHERE id=?', (args.id,)).fetchone()
            if not row or row['status'] not in {'uncertain', 'failed', 'retry'}:
                parser.error('Only uncertain/failed/retry items may be resolved')
            status = 'retry' if args.command == 'retry' else 'suppressed'
            db.execute('UPDATE outbox SET status=?,next_attempt=0,error=? WHERE id=?', (status, 'operator_resolved', args.id))
            db.execute("INSERT INTO audit_log(actor_id,action,target,detail,created_at) VALUES('local_operator','outbox_resolve',?,?,unixepoch())",
                       (str(args.id), status))
            print(f'{args.id}: {status}')


if __name__ == '__main__':
    main()

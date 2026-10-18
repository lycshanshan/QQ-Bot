from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import uuid

from bot.config import Settings
from bot.repositories.database import Database
from bot.services.application import dumps


class Archiver:
    """Immutable local JSON export; NAS packaging/retention are separate later stages."""
    def __init__(self, database: Database, settings: Settings):
        self.database, self.settings = database, settings

    def export_one(self) -> str | None:
        with self.database.transaction() as db:
            job = db.execute("SELECT * FROM archive_jobs WHERE status='pending' ORDER BY rowid LIMIT 1").fetchone()
            if job is None:
                group = db.execute('''SELECT conversation_id,count(*) AS n FROM messages
                    GROUP BY conversation_id HAVING n>=? ORDER BY min(id) LIMIT 1''',
                    (self.settings.message_window+self.settings.archive_batch,)).fetchone()
                if group is None:
                    return None
                rows = db.execute('SELECT * FROM messages WHERE conversation_id=? ORDER BY id LIMIT ?',
                                  (group['conversation_id'], self.settings.archive_batch)).fetchall()
                payloads = []
                for row in rows:
                    payload = json.loads(row['payload'])
                    payload['revoked'] = bool(row['revoked'])
                    payloads.append(payload)
                identifier = uuid.uuid4().hex
                snapshot = dumps(payloads)
                digest = hashlib.sha256(snapshot.encode('utf-8')).hexdigest()
                db.execute('''INSERT INTO archive_jobs(id,conversation_id,first_id,last_id,count,filename,sha256,payload)
                    VALUES(?,?,?,?,?,?,?,?)''', (identifier, group['conversation_id'], rows[0]['id'], rows[-1]['id'],
                                                len(rows), identifier+'.json', digest, snapshot))
                job = db.execute('SELECT * FROM archive_jobs WHERE id=?', (identifier,)).fetchone()
        directory = self.settings.archives
        directory.mkdir(parents=True, exist_ok=True)
        final = directory / job['filename']
        expected = job['sha256']
        if not final.exists():
            temporary = final.with_suffix('.tmp')
            with temporary.open('wb') as stream:
                stream.write(job['payload'].encode('utf-8'))
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, final)
        data = final.read_bytes()
        if hashlib.sha256(data).hexdigest() != expected or len(json.loads(data)) != job['count']:
            raise RuntimeError('Archive validation failed; source messages retained')
        with self.database.transaction() as db:
            db.execute("UPDATE archive_jobs SET status='completed',payload='' WHERE id=? AND status='pending'", (job['id'],))
            db.execute('DELETE FROM messages WHERE conversation_id=? AND id BETWEEN ? AND ?',
                       (job['conversation_id'], job['first_id'], job['last_id']))
        return job['id']

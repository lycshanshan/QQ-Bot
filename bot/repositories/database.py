from __future__ import annotations

from contextlib import closing, contextmanager
from pathlib import Path
import sqlite3
from typing import Iterator

SCHEMA = '''
CREATE TABLE users(user_id TEXT PRIMARY KEY, nickname TEXT NOT NULL DEFAULT '',
 role TEXT NOT NULL DEFAULT 'user' CHECK(role IN ('user','vip','admin')),
 banned INTEGER NOT NULL DEFAULT 0 CHECK(banned IN (0,1)));
CREATE TABLE bot_state(bot_id TEXT PRIMARY KEY, enabled INTEGER NOT NULL DEFAULT 1);
CREATE TABLE conversations(id TEXT PRIMARY KEY, bot_id TEXT NOT NULL, kind TEXT NOT NULL,
 target_id TEXT NOT NULL, watched INTEGER NOT NULL DEFAULT 0,
 ai_enabled INTEGER NOT NULL DEFAULT 0, checkin_enabled INTEGER NOT NULL DEFAULT 0);
CREATE TABLE event_receipts(event_key TEXT PRIMARY KEY, received_at REAL NOT NULL);
CREATE TABLE messages(id INTEGER PRIMARY KEY, event_key TEXT NOT NULL UNIQUE,
 conversation_id TEXT NOT NULL REFERENCES conversations(id), message_id TEXT NOT NULL,
 sender_id TEXT NOT NULL, occurred_at INTEGER NOT NULL, payload TEXT NOT NULL, revoked INTEGER NOT NULL DEFAULT 0);
CREATE INDEX messages_scope ON messages(conversation_id,id);
CREATE TABLE outbox(id INTEGER PRIMARY KEY, dedup_key TEXT NOT NULL UNIQUE,
 bot_id TEXT NOT NULL, conversation_id TEXT NOT NULL REFERENCES conversations(id), actor_id TEXT NOT NULL,
 payload TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending'
 CHECK(status IN ('pending','sending','retry','sent','uncertain','failed','suppressed')),
 attempts INTEGER NOT NULL DEFAULT 0, next_attempt REAL NOT NULL DEFAULT 0,
 message_id TEXT, error TEXT, created_at REAL NOT NULL);
CREATE INDEX outbox_ready ON outbox(status,next_attempt,id);
CREATE TABLE audit_log(id INTEGER PRIMARY KEY, actor_id TEXT NOT NULL, action TEXT NOT NULL,
 target TEXT NOT NULL, detail TEXT NOT NULL, created_at REAL NOT NULL);
CREATE TABLE checkins(id INTEGER PRIMARY KEY, conversation_id TEXT NOT NULL REFERENCES conversations(id),
 user_id TEXT NOT NULL, business_date TEXT NOT NULL, period TEXT NOT NULL, copy_id TEXT NOT NULL,
 event_key TEXT NOT NULL UNIQUE, occurred_at INTEGER NOT NULL,
 UNIQUE(conversation_id,user_id,business_date,period));
CREATE TABLE archive_jobs(id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL REFERENCES conversations(id),
 first_id INTEGER NOT NULL, last_id INTEGER NOT NULL, count INTEGER NOT NULL,
 status TEXT NOT NULL DEFAULT 'pending', filename TEXT NOT NULL, sha256 TEXT, payload TEXT NOT NULL DEFAULT '');
CREATE UNIQUE INDEX archive_active ON archive_jobs(conversation_id) WHERE status='pending';
CREATE TABLE message_tombstones(conversation_id TEXT NOT NULL, message_id TEXT NOT NULL,
 revoked_at INTEGER NOT NULL, PRIMARY KEY(conversation_id,message_id));
'''


AI_SCHEMA = '''
ALTER TABLE bot_state ADD COLUMN default_model TEXT NOT NULL DEFAULT 'gpt-6-luna';
ALTER TABLE bot_state ADD COLUMN high_model TEXT NOT NULL DEFAULT 'gpt-6.1-sol';
CREATE TABLE context_sessions(conversation_id TEXT NOT NULL, user_id TEXT NOT NULL, generation INTEGER NOT NULL DEFAULT 0,
 thread_id TEXT, context_mode TEXT NOT NULL DEFAULT 'none', context_count INTEGER NOT NULL DEFAULT 0,
 PRIMARY KEY(conversation_id,user_id));
CREATE TABLE quota_daily(user_id TEXT NOT NULL, business_date TEXT NOT NULL, used INTEGER NOT NULL DEFAULT 0,
 reserved INTEGER NOT NULL DEFAULT 0, grants INTEGER NOT NULL DEFAULT 0, PRIMARY KEY(user_id,business_date));
CREATE TABLE quota_ledger(id INTEGER PRIMARY KEY, user_id TEXT NOT NULL, business_date TEXT NOT NULL,
 request_id TEXT, action TEXT NOT NULL, amount INTEGER NOT NULL, created_at REAL NOT NULL);
CREATE TABLE ai_requests(id TEXT PRIMARY KEY,event_key TEXT NOT NULL UNIQUE,
 conversation_id TEXT NOT NULL REFERENCES conversations(id),bot_id TEXT NOT NULL,user_id TEXT NOT NULL,
 business_date TEXT NOT NULL,generation INTEGER NOT NULL,status TEXT NOT NULL DEFAULT 'queued',
 started INTEGER NOT NULL DEFAULT 0,reserved INTEGER NOT NULL,charged INTEGER NOT NULL DEFAULT 0,
 cancel_requested INTEGER NOT NULL DEFAULT 0,model TEXT NOT NULL,input TEXT NOT NULL,
 created_at REAL NOT NULL,thread_id TEXT,turn_id TEXT,workspace_id TEXT,result TEXT,error TEXT,tokens INTEGER NOT NULL DEFAULT 0);
CREATE INDEX ai_queue ON ai_requests(status,created_at);
CREATE TABLE policy_changes(id INTEGER PRIMARY KEY,event_key TEXT NOT NULL UNIQUE,actor_id TEXT NOT NULL,
 old_hash TEXT NOT NULL,new_hash TEXT NOT NULL,content TEXT NOT NULL,status TEXT NOT NULL DEFAULT 'pending');
'''


EXTENSION_SCHEMA = '''
CREATE TABLE ai_tasks(id TEXT PRIMARY KEY,request_id TEXT NOT NULL REFERENCES ai_requests(id),ordinal INTEGER NOT NULL,
 model TEXT NOT NULL,goal TEXT NOT NULL,status TEXT NOT NULL,thread_id TEXT,turn_id TEXT,result TEXT,tokens INTEGER NOT NULL DEFAULT 0,
 UNIQUE(request_id,ordinal));
CREATE TABLE archive_packs(id TEXT PRIMARY KEY,conversation_id TEXT NOT NULL,filename TEXT NOT NULL UNIQUE,
 sha256 TEXT NOT NULL,bytes INTEGER NOT NULL,job_ids TEXT NOT NULL,status TEXT NOT NULL DEFAULT 'local',
 remote_path TEXT,attempts INTEGER NOT NULL DEFAULT 0,next_attempt REAL NOT NULL DEFAULT 0,error TEXT,created_at REAL NOT NULL);
ALTER TABLE archive_jobs ADD COLUMN pack_id TEXT;
CREATE TABLE subscriptions(id INTEGER PRIMARY KEY,conversation_id TEXT NOT NULL REFERENCES conversations(id),
 kind TEXT NOT NULL,source_id TEXT NOT NULL,active INTEGER NOT NULL DEFAULT 1,created_by TEXT NOT NULL,
 UNIQUE(conversation_id,kind,source_id));
CREATE TABLE subscription_state(kind TEXT NOT NULL,source_id TEXT NOT NULL,baseline TEXT,last_success REAL,
 next_poll REAL NOT NULL DEFAULT 0,failures INTEGER NOT NULL DEFAULT 0,error TEXT,paused INTEGER NOT NULL DEFAULT 0,
 candidate TEXT,candidate_since REAL,PRIMARY KEY(kind,source_id));
'''


class Database:
    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self.connect()) as db:
            db.execute('PRAGMA journal_mode=WAL')
            version = db.execute('PRAGMA user_version').fetchone()[0]
            if version > 3:
                raise RuntimeError('Database schema is newer than this bot')
            if version == 0:
                try:
                    db.executescript('BEGIN IMMEDIATE;\n' + SCHEMA + '\nPRAGMA user_version=1;\nCOMMIT;')
                except BaseException:
                    db.rollback()
                    raise
            if version < 2:
                db.executescript('BEGIN IMMEDIATE;\n' + AI_SCHEMA + '\nPRAGMA user_version=2;\nCOMMIT;')
            if version < 3:
                db.executescript('BEGIN IMMEDIATE;\n' + EXTENSION_SCHEMA + '\nPRAGMA user_version=3;\nCOMMIT;')

    def connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path, timeout=5)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA foreign_keys=ON')
        db.execute('PRAGMA busy_timeout=5000')
        return db

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        db = self.connect()
        try:
            db.execute('BEGIN IMMEDIATE')
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def recover_outbox(self) -> None:
        with self.transaction() as db:
            db.execute("UPDATE outbox SET status='uncertain',error='interrupted_during_send' WHERE status='sending'")

    def backup(self, destination: Path) -> None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        source = self.connect()
        target = sqlite3.connect(destination)
        try:
            source.backup(target)
        finally:
            target.close()
            source.close()

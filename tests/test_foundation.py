from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import datetime
import json
from pathlib import Path
import sqlite3
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pytest

from bot.config import load_settings, SUPERUSER
from bot.domain.checkin import classify
from bot.domain.messages import Incoming
from bot.repositories.database import Database
from bot.services.application import Application
from bot.services.instance import InstanceLock
from bot.workers.archive import Archiver
from bot.workers.outbox import Outbox

ZONE = ZoneInfo('Asia/Shanghai')
USER, ADMIN, GROUP = '12345678', '22345678', '1049251004'


def stamp(value: str) -> int:
    return int(datetime.fromisoformat(value).replace(tzinfo=ZONE).timestamp())


@pytest.fixture
def app(tmp_path):
    settings = replace(load_settings(), database=tmp_path/'db.sqlite3', archives=tmp_path/'archives',
                       command_interval=0, send_interval=0, ai_enabled=False, ai_policy=tmp_path/'policy.toml', agent_workspaces=tmp_path/'agent')
    return Application(settings)


def event(mid='1', text='/ping', user=USER, group=None, at='2026-10-15T08:00:00', delay=0, segments=None):
    return Incoming('3653125828', 'group' if group else 'private', group or user, user, str(mid), stamp(at),
                    stamp(at)+delay, 'tester', 'card', segments or [{'type': 'text', 'data': {'text': text}}], text)


def rows(app, sql, args=()):
    db = app.database.connect()
    try:
        return [dict(r) for r in db.execute(sql, args)]
    finally:
        db.close()


def watch(app, group=GROUP):
    app.process(event('watch'+group, f'/watch add {group}', SUPERUSER), {group})


def enable(app, group=GROUP):
    watch(app, group)
    app.process(event('enable'+group, '/checkin on', SUPERUSER, group))


@pytest.mark.parametrize('text,at,expected', [
    ('早安','2026-10-15T04:29:59',None),
    ('早安','2026-10-15T04:30:00',('2026-10-15','morning')),
    ('早上好喵','2026-10-15T12:00:00',('2026-10-15','morning')),
    ('早安','2026-10-15T12:00:01',None),
    ('晚安','2026-10-15T19:59:59',None),
    ('晚安喵','2026-10-15T20:00:00',('2026-10-15','night')),
    ('晚安','2026-10-16T00:00:00',('2026-10-15','night')),
    ('晚安','2026-10-16T03:00:00',('2026-10-15','night')),
    ('晚安','2026-10-16T03:00:01',None),
    (' 早安','2026-10-15T08:00:00',None),
    ('早安！','2026-10-15T08:00:00',None),
])
def test_boundaries(text, at, expected):
    assert classify(text, stamp(at)) == expected


def test_unwatched_is_neither_recorded_nor_answered(app):
    assert not app.process(event(group=GROUP))
    assert not rows(app, 'SELECT * FROM messages')
    assert not rows(app, 'SELECT * FROM users')
    assert not rows(app, 'SELECT * FROM outbox')
    assert not app.process(event('2', '/watch add '+GROUP, group=GROUP), {GROUP})


def test_admin_bootstrap_requires_membership(app):
    assert app.process(event(text='/watch add '+GROUP, user=SUPERUSER), set())
    assert not rows(app, "SELECT * FROM conversations WHERE kind='group'")
    app.process(event('2', '/admin add '+ADMIN, SUPERUSER))
    app.process(event('3', '/watch add '+GROUP, ADMIN, GROUP), {GROUP})
    assert rows(app, "SELECT watched FROM conversations WHERE kind='group'")[0]['watched'] == 1


def test_roles_persist_and_cannot_replace_superuser(app):
    app.process(event(text='/admin add '+ADMIN, user=USER))
    assert rows(app, 'SELECT role FROM users WHERE user_id=?', (ADMIN,)) == []
    app.process(event('2', '/admin add '+ADMIN, SUPERUSER))
    app.process(event('3', '/admin remove '+SUPERUSER, SUPERUSER))
    restarted = Application(app.settings)
    restarted.process(event('4', '/admin add '+USER, ADMIN))
    assert rows(app, 'SELECT role FROM users WHERE user_id=?', (USER,))[0]['role'] == 'user'
    assert rows(app, 'SELECT role FROM users WHERE user_id=?', (ADMIN,))[0]['role'] == 'admin'
    assert any('不可修改' in r['payload'] for r in rows(app, 'SELECT payload FROM outbox'))


def test_dedup_and_off_recording_recovery(app):
    assert app.process(event())
    assert not app.process(event())
    app.process(event('off', '/bot off', SUPERUSER))
    app.process(event('2'))
    assert len(rows(app, 'SELECT * FROM outbox WHERE actor_id=?', (USER,))) == 1
    assert len(rows(app, 'SELECT * FROM messages WHERE sender_id=?', (USER,))) == 2
    app.process(event('on', '/bot on', SUPERUSER))
    app.process(event('3'))
    assert len(rows(app, 'SELECT * FROM outbox WHERE actor_id=?', (USER,))) == 2


def test_checkin_once_per_period_and_conversation(app):
    enable(app)
    app.process(event('1', '早安', group=GROUP))
    app.process(event('2', '早上好', group=GROUP))
    app.process(event('3', '晚安', group=GROUP, at='2026-10-15T22:00:00'))
    app.process(event('4', '晚安', group=GROUP, at='2026-10-16T02:00:00'))
    assert len(rows(app, 'SELECT * FROM checkins')) == 2
    enable(app, '1049251005')
    app.process(event('1', '早安', group='1049251005'))
    assert len(rows(app, 'SELECT * FROM checkins')) == 3


def test_delay_and_rich_text_do_not_checkin(app):
    enable(app)
    app.process(event('late', '早安', group=GROUP, delay=301))
    app.process(event('img', '早安', group=GROUP, segments=[{'type':'text','data':{'text':'早安'}}, {'type':'image','data':{'file':'abc'}}]))
    app.process(event('reply', '早安', group=GROUP, segments=[{'type':'reply','data':{'id':'123'}}, {'type':'text','data':{'text':'早安'}}]))
    assert not rows(app, 'SELECT * FROM checkins')
    app.process(event('valid', '早安', group=GROUP, delay=300))
    assert len(rows(app, 'SELECT * FROM checkins')) == 1


def test_streaks_collection_and_missed_day(app):
    enable(app)
    for mid, text, at in [('a','早安','2026-10-12T08:00:00'), ('b','晚安','2026-10-12T22:00:00'),
                          ('c','晚安','2026-10-13T22:00:00')]:
        app.process(event(mid, text, group=GROUP, at=at))
    app.process(event('query', '/打卡查询', group=GROUP, at='2026-10-14T08:00:00'))
    output = rows(app, 'SELECT payload FROM outbox ORDER BY id DESC LIMIT 1')[0]['payload']
    assert '当前连续 2 天 / 3 次' in output
    app.process(event('d', '早安', group=GROUP, at='2026-10-15T08:00:00'))
    output = rows(app, 'SELECT payload FROM outbox ORDER BY id DESC LIMIT 1')[0]['payload']
    assert '当前连续 1 天 / 1 次' in output
    assert '历史最长 2 天 / 3 次' in output
    assert '累计 3 天 / 4 次' in output


def test_checkin_and_outbox_rollback_together(app):
    enable(app)
    with patch.object(app, 'enqueue', side_effect=sqlite3.OperationalError('injected')):
        with pytest.raises(sqlite3.OperationalError):
            app.process(event('1', '早安', group=GROUP))
    assert not rows(app, 'SELECT * FROM checkins')
    assert not rows(app, "SELECT * FROM event_receipts WHERE event_key LIKE '%:1'")
    app.process(event('1', '早安', group=GROUP))
    selected = rows(app, 'SELECT copy_id FROM checkins')[0]['copy_id']
    assert not app.process(event('1', '早安', group=GROUP))
    assert rows(app, 'SELECT copy_id FROM checkins')[0]['copy_id'] == selected


class FakeBot:
    def __init__(self, failure=None):
        self.calls = []
        self.failure = failure

    async def call_api(self, action, **params):
        self.calls.append((action, params))
        if self.failure:
            raise self.failure
        return {'message_id': 999+len(self.calls)}


def test_outbox_sent_and_escaped_cq_text(app):
    app.process(event(text='/echo [CQ:at,qq=all]'))
    bot = FakeBot()
    assert asyncio.run(Outbox(app.database).deliver_one({'3653125828':bot})) == 'sent'
    assert bot.calls[0][1]['message'][0].type == 'text'
    assert bot.calls[0][1]['message'][0].data['text'] == '[CQ:at,qq=all]'
    assert rows(app, 'SELECT status FROM outbox')[0]['status'] == 'sent'
    assert len(rows(app, 'SELECT * FROM messages')) == 2
    assert asyncio.run(Outbox(app.database).deliver_one({'3653125828':bot})) is None


def test_uncertain_send_not_retried_or_overtaken(app):
    app.process(event())
    app.process(event('2'))
    bot = FakeBot(TimeoutError())
    outbox = Outbox(app.database)
    assert asyncio.run(outbox.deliver_one({'3653125828':bot})) == 'uncertain'
    assert asyncio.run(outbox.deliver_one({'3653125828':bot})) is None
    assert len(bot.calls) == 1
    with app.database.transaction() as db:
        db.execute("UPDATE outbox SET status='sending' WHERE status='pending'")
    app.database.recover_outbox()
    assert {r['status'] for r in rows(app, 'SELECT status FROM outbox')} == {'uncertain'}


def test_explicit_api_rejection_can_retry(app):
    from nonebot.adapters.onebot.v11.exception import ActionFailed
    app.process(event())
    bot = FakeBot(ActionFailed(retcode=1400))
    assert asyncio.run(Outbox(app.database).deliver_one({'3653125828':bot})) == 'rejected'
    assert rows(app, 'SELECT status FROM outbox')[0]['status'] == 'retry'
    assert len(rows(app, 'SELECT * FROM messages')) == 1


@pytest.mark.parametrize('change', ['ban', 'off', 'unwatch'])
def test_outbox_rechecks_policy(app, change):
    watch(app)
    app.process(event(group=GROUP))
    if change == 'ban':
        app.process(event('change', '/ban '+USER, SUPERUSER))
    elif change == 'off':
        app.process(event('change', '/bot off', SUPERUSER))
    else:
        app.process(event('change', '/watch remove '+GROUP, SUPERUSER))
    bot = FakeBot()
    outbox = Outbox(app.database)
    for _ in range(4):
        asyncio.run(outbox.deliver_one({'3653125828':bot}))
    assert rows(app, 'SELECT status FROM outbox WHERE actor_id=?', (USER,))[0]['status'] == 'suppressed'
    assert not any('group_id' in params for _, params in bot.calls)


def test_archive_rollback_recovery_and_dedup(app):
    app.settings = replace(app.settings, message_window=2, archive_batch=2)
    for i in range(6):
        app.process(event(str(i), text='stored'))
    exporter = Archiver(app.database, app.settings)
    with patch('bot.workers.archive.os.replace', side_effect=OSError('interrupted')):
        with pytest.raises(OSError):
            exporter.export_one()
    assert len(rows(app, 'SELECT * FROM messages')) == 6
    assert rows(app, 'SELECT status FROM archive_jobs')[0]['status'] == 'pending'
    exporter.export_one()
    exporter.export_one()
    assert len(rows(app, 'SELECT * FROM messages')) == 2
    assert not app.process(event('0', text='stored'))
    archived = [item for file in app.settings.archives.glob('*.json') for item in json.loads(file.read_text(encoding='utf-8'))]
    assert len(archived) == 4
    assert len({m['event_key'] for m in archived}) == 4


def test_archive_after_publish_recovery_and_corruption(app):
    app.settings = replace(app.settings, message_window=1, archive_batch=1)
    app.process(event('1', text='stored'))
    app.process(event('2', text='stored'))
    exporter = Archiver(app.database, app.settings)
    with patch('bot.workers.archive.os.replace', side_effect=OSError('interrupted')):
        with pytest.raises(OSError):
            exporter.export_one()
    job = rows(app, 'SELECT * FROM archive_jobs')[0]
    destination = app.settings.archives/job['filename']
    destination.write_text('bad', encoding='utf-8')
    with pytest.raises(RuntimeError):
        exporter.export_one()
    assert len(rows(app, 'SELECT * FROM messages')) == 2
    destination.write_text(job['payload'], encoding='utf-8')
    exporter.export_one()
    assert len(rows(app, 'SELECT * FROM messages')) == 1


def test_backup_and_schema_version(app, tmp_path):
    app.process(event())
    backup = tmp_path/'backup.sqlite3'
    app.database.backup(backup)
    with sqlite3.connect(backup) as db:
        assert db.execute('SELECT count(*) FROM messages').fetchone()[0] == 1
        assert db.execute('PRAGMA user_version').fetchone()[0] == 3
    with sqlite3.connect(backup) as db:
        db.execute('PRAGMA user_version=99')
    with pytest.raises(RuntimeError):
        Database(backup)


def test_instance_lock(tmp_path):
    lock = InstanceLock(tmp_path/'bot.lock')
    try:
        with pytest.raises(RuntimeError):
            InstanceLock(tmp_path/'bot.lock')
    finally:
        lock.close()
    InstanceLock(tmp_path/'bot.lock').close()


def test_recall_and_cross_conversation_reply(app):
    app.process(event('1', text='secret'))
    incoming = replace(event('2', text='reply', user=ADMIN), reply={'message_id':'1'})
    app.process(incoming)
    payload = json.loads(rows(app, 'SELECT payload FROM messages WHERE sender_id=?', (ADMIN,))[0]['payload'])
    assert payload['reply_to']['scope_verified'] is False
    app.recall('3653125828', 'private', USER, '1', stamp('2026-10-15T09:00:00'))
    assert rows(app, "SELECT revoked FROM messages WHERE message_id='1'")[0]['revoked'] == 1
    assert len(rows(app, 'SELECT * FROM message_tombstones')) == 1


def test_config_rejects_unknown_and_invalid_values(tmp_path):
    config = tmp_path/'bad.toml'
    for content in ['[bot]\ntimezone="UTC"', '[storage]\nmessage_window=0', '[bot]\nsend_interval=nan',
                    '[bot]\nsuperuser="99999"', '[checkin]\ncopies=[]']:
        config.write_text(content, encoding='utf-8')
        with pytest.raises(ValueError):
            load_settings(config)

def test_archive_transaction_failure_after_publish_keeps_sources(app):
    from contextlib import contextmanager
    app.settings = replace(app.settings, message_window=1, archive_batch=1)
    app.process(event('1', text='stored'))
    app.process(event('2', text='stored'))
    original = app.database.transaction
    calls = 0
    @contextmanager
    def interrupted_transaction():
        nonlocal calls
        calls += 1
        with original() as db:
            yield db
            if calls == 2:
                raise OSError('simulated crash before final DB commit')
    exporter = Archiver(app.database, app.settings)
    with patch.object(app.database, 'transaction', interrupted_transaction):
        with pytest.raises(OSError):
            exporter.export_one()
    assert len(list(app.settings.archives.glob('*.json'))) == 1
    assert len(rows(app, 'SELECT * FROM messages')) == 2
    assert rows(app, 'SELECT status FROM archive_jobs')[0]['status'] == 'pending'
    exporter.export_one()
    assert len(rows(app, 'SELECT * FROM messages')) == 1
    assert rows(app, 'SELECT status FROM archive_jobs')[0]['status'] == 'completed'

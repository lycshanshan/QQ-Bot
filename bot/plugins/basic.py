from __future__ import annotations

import asyncio
import json
import os
import time

import nonebot
from nonebot import logger, on_message, on_notice
from nonebot.adapters.onebot.v11 import Bot, MessageEvent, NoticeEvent
from nonebot.compat import model_dump

from bot.config import load_settings
from bot.domain.messages import Incoming
from bot.services.application import Application, RANK, role_for
from bot.services.connection import ConnectionHealth
from bot.workers.archive import Archiver
from bot.workers.outbox import Outbox
from bot.ai.worker import AIWorker
from bot.workers.nas_archive import ArchivePacks

application: Application | None = None
ai_worker: AIWorker | None = None
connection_health: ConnectionHealth | None = None
worker_tasks: list[asyncio.Task] = []
process_lock = asyncio.Lock()
messages = on_message(priority=1, block=True)
notices = on_notice(priority=1, block=False)
driver = nonebot.get_driver()


@driver.on_bot_connect
async def bot_connected(bot: Bot) -> None:
    if connection_health is not None:
        connection_health.connect(str(bot.self_id))
    logger.info('QQ transport connected: bot={}', bot.self_id)


@driver.on_bot_disconnect
async def bot_disconnected(bot: Bot) -> None:
    if connection_health is not None:
        connection_health.disconnect(str(bot.self_id))
    logger.warning('QQ transport disconnected: bot={}; service remains running; check NapCat login if reconnect fails', bot.self_id)


async def connection_loop() -> None:
    while True:
        if connection_health is not None and connection_health.needs_warning():
            logger.warning('QQ transport unavailable for 60s; check NapCat WebUI login/OneBot connection. No automatic account login attempted.')
        await asyncio.sleep(5)


def normalize(event: MessageEvent) -> Incoming:
    # Adapter mutates message while resolving @/reply. original_message preserves order.
    raw = model_dump(event)
    kind = event.message_type
    return Incoming(str(event.self_id), kind, str(event.group_id if kind == 'group' else event.user_id),
                    str(event.user_id), str(event.message_id), event.time, time.time(),
                    event.sender.nickname or '', event.sender.card or '', raw['original_message'],
                    event.get_plaintext(), {'message_id': str(event.reply.message_id)} if event.reply else None,
                    str(raw['message_seq']) if raw.get('message_seq') is not None else None)


@messages.handle()
async def route(bot: Bot, event: MessageEvent) -> None:
    if application is None:
        return
    incoming = normalize(event)
    async with process_lock:
        joined: set[str] | None = None
        words = incoming.command_text.strip().split()
        db = application.database.connect()
        try:
            role = role_for(db, incoming.user_id)
        finally:
            db.close()
        if words[:2] == ['/watch', 'add'] and RANK[role] >= RANK['admin']:
            try:
                groups = await asyncio.wait_for(bot.call_api('get_group_list'), timeout=10)
                joined = {str(g['group_id']) for g in groups}
            except Exception:
                joined = None
        if len(words)==3 and words[0] in {'/model','/high-model'} and role=='superuser' and ai_worker is not None:
            try:
                application.available_models=await asyncio.wait_for(ai_worker.provider.models(),timeout=30)
            except Exception:
                application.available_models=set()
        replacement = None
        reload_error = False
        if words == ['/system', 'reload'] and RANK[role] >= RANK['admin']:
            try:
                replacement = load_settings()
                application.ai.policy.read()
                if (replacement.database, replacement.archives) != (application.settings.database, application.settings.archives):
                    raise ValueError('Storage paths require restart')
            except Exception:
                reload_error = True
        accepted = application.process(incoming, joined)
        publish = False
        if accepted and (replacement or reload_error):
            with application.database.transaction() as db:
                result = db.execute('SELECT id FROM outbox WHERE dedup_key=?', (incoming.event_key+':reply',)).fetchone()
                if result:
                    if reload_error:
                        db.execute('UPDATE outbox SET payload=? WHERE id=?',
                                   (json.dumps([{'type': 'text', 'data': {'text': '配置无效或修改了存储路径；保留原配置。'}}], ensure_ascii=False), result['id']))
                        application.audit(db, incoming, 'reload_rejected', 'config.toml', '')
                    else:
                        publish = True
                        application.audit(db, incoming, 'reload', 'config.toml', 'applied')
        if publish:
            application.settings = replacement


@notices.handle()
async def recall(event: NoticeEvent) -> None:
    if application is None or getattr(event, 'notice_type', '') not in {'group_recall', 'friend_recall'}:
        return
    raw = model_dump(event)
    kind = 'group' if raw['notice_type'] == 'group_recall' else 'private'
    target = str(raw.get('group_id', raw.get('user_id')))
    async with process_lock:
        application.recall(str(event.self_id), kind, target, str(raw['message_id']), event.time)


async def send_loop() -> None:
    assert application is not None
    sender = Outbox(application.database,lambda: application.settings.ai_enabled,lambda: application.settings.bili_enabled)
    while True:
        try:
            result = await sender.deliver_one(nonebot.get_bots())
            await asyncio.sleep(max(0.1, application.settings.send_interval) if result else 0.5)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.error('Outbox worker failed: {}', type(exc).__name__)
            await asyncio.sleep(2)


async def ai_loop() -> None:
    assert ai_worker is not None
    while True:
        try:
            await ai_worker.pump(nonebot.get_bots())
            await asyncio.sleep(0.5)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.error('AI worker failed: {}', type(exc).__name__)
            await asyncio.sleep(2)


async def nas_loop() -> None:
    assert application is not None
    while True:
        try:
            if os.getenv('NAS_ARCHIVE_ENABLED','false').lower()=='true':
                packs=ArchivePacks(application.database,application.settings,
                    threshold=int(os.getenv('NAS_ARCHIVE_BYTES','10000000')),
                    max_wait=int(os.getenv('NAS_ARCHIVE_MAX_WAIT','86400')),
                    keep_local=os.getenv('NAS_ARCHIVE_KEEP_LOCAL','false').lower()=='true')
                await asyncio.to_thread(packs.pack_one)
                await asyncio.to_thread(packs.upload_one)
                await asyncio.to_thread(packs.cleanup_one)
            await asyncio.sleep(30)
        except asyncio.CancelledError:raise
        except Exception as exc:
            logger.error('NAS archive worker failed: {}',type(exc).__name__)
            await asyncio.sleep(30)


async def subscription_loop() -> None:
    assert application is not None
    while True:
        try:
            if application.settings.bili_enabled:
                await application.subscriptions.poll_one()
            await asyncio.sleep(5)
        except asyncio.CancelledError:raise
        except Exception as exc:
            logger.error('Subscription worker failed: {}',type(exc).__name__)
            await asyncio.sleep(10)


async def archive_loop() -> None:
    assert application is not None
    while True:
        try:
            exported = await asyncio.to_thread(Archiver(application.database, application.settings).export_one)
            await asyncio.sleep(0.1 if exported else 10)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.error('Archive worker failed: {}', type(exc).__name__)
            await asyncio.sleep(10)


@driver.on_startup
async def startup() -> None:
    global application, ai_worker, connection_health
    application = Application(load_settings())
    connection_health = ConnectionHealth(application.settings.database.with_name('connection.json'))
    application.database.recover_outbox()
    application.ai.policy.apply_pending(application.database)
    ai_worker = AIWorker(application)
    ai_worker.recover()
    ArchivePacks(application.database,application.settings).recover()
    worker_tasks.extend([asyncio.create_task(send_loop()), asyncio.create_task(archive_loop()), asyncio.create_task(ai_loop()),asyncio.create_task(nas_loop()),asyncio.create_task(subscription_loop()),asyncio.create_task(connection_loop())])
    logger.info('Bot services ready: foundation, checkin, AI, renderer, NAS archive and live subscriptions')


@driver.on_shutdown
async def shutdown() -> None:
    for task in worker_tasks:
        task.cancel()
    await asyncio.gather(*worker_tasks, return_exceptions=True)
    worker_tasks.clear()
    if ai_worker is not None:
        await ai_worker.close()

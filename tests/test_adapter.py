from dataclasses import replace
import asyncio
import json

import nonebot
from nonebot.adapters.onebot.v11 import GroupMessageEvent, PrivateMessageEvent

from bot.services.application import Application
from bot.config import load_settings, SUPERUSER

nonebot.init(driver='~fastapi', host='127.0.0.1', port=18080)
nonebot.load_plugin('bot.plugins.basic')
from bot.plugins import basic


def test_real_adapter_normalization_and_plugin_route(tmp_path):
    basic.application = Application(replace(load_settings(), database=tmp_path/'plugin.db', archives=tmp_path/'archive', command_interval=0))
    event = PrivateMessageEvent.model_validate(dict(time=1792022400, self_id=3653125828, post_type='message',
            message_type='private', sub_type='friend', user_id=int(SUPERUSER), message_id=1,
            message=[{'type':'text','data':{'text':'/ping'}}], raw_message='/ping', font=0,
            sender={'user_id':int(SUPERUSER), 'nickname':'test'}))
    asyncio.run(basic.route(None, event))
    db = basic.application.database.connect()
    try:
        assert db.execute('SELECT count(*) FROM messages').fetchone()[0] == 1
        assert 'pong' in db.execute('SELECT payload FROM outbox').fetchone()[0]
    finally:
        db.close()
    group = GroupMessageEvent.model_validate(dict(time=1792022400, self_id=3653125828, post_type='message',
            message_type='group', sub_type='normal', group_id=1049251004, user_id=12345678, message_id=2,
            message=[{'type':'at','data':{'qq':'3653125828'}}, {'type':'text','data':{'text':'早安'}}],
            raw_message='[CQ:at,qq=3653125828]早安', font=0, sender={'nickname':'test'}))
    group.message.pop(0)
    normalized = basic.normalize(group)
    assert normalized.segments[0]['type'] == 'at'
    assert normalized.pure_text is None
    assert normalized.command_text == '早安'


def test_privacy_filter_runs_after_framework_patching():
    from types import SimpleNamespace
    from bot.__main__ import privacy_filter
    record = {'file':SimpleNamespace(path='C:/venv/nonebot/message.py'), 'message':'PRIVATE_SENTINEL',
              'function':'handle_event', 'extra':{}, 'level':SimpleNamespace(no=25)}
    assert privacy_filter(record) is False
    record['file'] = SimpleNamespace(path='C:/venv/nonebot/adapters/onebot/v11/adapter.py')
    record['exception'] = ValueError('secret')
    assert privacy_filter(record) is True
    assert 'PRIVATE_SENTINEL' not in record['message']
    assert record['exception'] is None


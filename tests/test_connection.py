import json

from bot.services.connection import ConnectionHealth


def test_offline_warning_once_then_reconnect_resets(tmp_path):
    path = tmp_path/'connection.json'
    health = ConnectionHealth(path, now=0)
    assert not health.needs_warning(now=59)
    assert health.needs_warning(now=60)
    assert not health.needs_warning(now=120)
    health.connect('3653125828', now=121)
    assert not health.needs_warning(now=1000)
    assert json.loads(path.read_text())['connected_bot_ids'] == ['3653125828']
    health.disconnect('3653125828', now=1001)
    health.disconnect('3653125828', now=1030)
    assert not health.needs_warning(now=1060)
    assert health.needs_warning(now=1061)
    data = json.loads(path.read_text())
    assert data['connected_bot_ids'] == [] and data['offline_since'] == 1001
    assert not path.with_suffix('.tmp').exists()


def test_one_disconnect_does_not_report_other_connected_bot_offline(tmp_path):
    health = ConnectionHealth(tmp_path/'connection.json', now=0)
    health.connect('one', now=1)
    health.connect('two', now=2)
    health.disconnect('one', now=3)
    assert not health.needs_warning(now=1000)

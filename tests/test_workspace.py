from pathlib import Path
import os
from unittest.mock import patch

import pytest

from bot.ai.workspace import Workspaces


def test_task_workspaces_are_distinct_and_private_by_default(tmp_path):
    manager=Workspaces(tmp_path/'tasks')
    one,two=manager.create(),manager.create()
    assert one.path != two.path
    assert manager.get(one.id) == one
    assert {p.name for p in one.path.iterdir()} == {'input','output','scratch'}
    overrides=one.permission_overrides('mxc')
    assert any(':root' in value and 'deny' in value for value in overrides)
    assert not any(str(two.path) in value for value in overrides)
    assert 'approval_policy="never"' in overrides
    with pytest.raises(ValueError):
        one.permission_overrides('unelevated')
    with pytest.raises(ValueError):
        manager.get('../other-user')


@pytest.mark.parametrize('relative',['../secret.txt','../../secret.txt','C:/secret.txt','C:secret.txt','/secret.txt','x.txt:stream',''])
def test_output_path_rejects_traversal_drives_and_streams(tmp_path,relative):
    task=Workspaces(tmp_path/'tasks').create()
    with pytest.raises(ValueError):
        task.artifact(relative)


def test_valid_artifact_and_hardlink_rejection(tmp_path):
    task=Workspaces(tmp_path/'tasks').create()
    target=task.path/'output/result.txt'
    target.write_text('result')
    assert task.artifact('result.txt') == target
    outside=tmp_path/'private.txt'
    outside.write_text('synthetic secret')
    os.link(outside,task.path/'output/linked.txt')
    with pytest.raises(ValueError):
        task.artifact('linked.txt')


def test_output_root_link_is_rejected(tmp_path):
    task=Workspaces(tmp_path/'tasks').create()
    target=task.path/'output/result.txt'
    target.write_text('result')
    with patch('bot.ai.workspace.is_link',side_effect=lambda p:p==task.path/'output'):
        with pytest.raises(ValueError):
            task.artifact('result.txt')

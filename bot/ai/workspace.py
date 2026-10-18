from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import re
import uuid


def is_link(path: Path) -> bool:
    return path.is_symlink() or (hasattr(path, 'is_junction') and path.is_junction())


@dataclass(frozen=True)
class TaskWorkspace:
    id: str
    path: Path

    def artifact(self, relative: str) -> Path:
        candidate = Path(relative)
        if candidate.is_absolute() or candidate.drive or not candidate.parts or any(p in {'.', '..'} or ':' in p for p in candidate.parts):
            raise ValueError('Artifact must be a relative path without traversal or streams')
        output = self.path / 'output'
        if is_link(self.path) or is_link(output) or not output.resolve().is_relative_to(self.path.resolve()):
            raise ValueError('Task/output directory must not be linked')
        current = output
        for part in candidate.parts:
            current = current / part
            if is_link(current):
                raise ValueError('Linked artifacts are not allowed')
        resolved = current.resolve()
        if not resolved.is_relative_to(output.resolve()) or not resolved.is_file():
            raise ValueError('Artifact is not a file inside the task output directory')
        if resolved.stat().st_nlink > 1:
            raise ValueError('Hard-linked artifacts are not allowed')
        return resolved

    def permission_overrides(self, implementation: str = 'elevated') -> tuple[str, ...]:
        if implementation not in {'mxc', 'elevated'}:
            raise ValueError('This design needs a sandbox that can deny reads')
        # Use an isolated CODEX_HOME for the real SDK runner. These are per-task overrides;
        # do not pass legacy sandbox=... or sandbox_mode alongside them.
        filesystem = '{' + ','.join(f'{json.dumps(k)}={json.dumps(v)}' for k,v in {
            ':root':'deny', ':minimal':'read', self.path.as_posix():'write'}.items()) + '}'
        return ('default_permissions="qqbot_task"', 'approval_policy="never"',
                f'windows.sandbox="{implementation}"', f'permissions.qqbot_task.filesystem={filesystem}',
                'permissions.qqbot_task.network.enabled=false', 'features.shell_tool=true',
                'features.apps=false', 'features.plugins=false', 'features.multi_agent=false',
                'features.hooks=false', 'features.memories=false', 'features.js_repl=false',
                'features.browser_use=false', 'web_search="disabled"',
                'shell_environment_policy.inherit="core"',
                'shell_environment_policy.ignore_default_excludes=false')


class Workspaces:
    def __init__(self, root: Path):
        if is_link(root):
            raise ValueError('Workspace root must not be a link')
        root.mkdir(parents=True, exist_ok=True)
        self.root = root.resolve()

    def create(self) -> TaskWorkspace:
        identifier = uuid.uuid4().hex
        directory = self.root / identifier
        directory.mkdir(exist_ok=False)
        for name in ('input', 'output', 'scratch'):
            (directory/name).mkdir()
        return TaskWorkspace(identifier, directory)

    def get(self, identifier: str) -> TaskWorkspace:
        if not re.fullmatch('[0-9a-f]{32}', identifier):
            raise ValueError('Invalid task workspace ID')
        directory = self.root / identifier
        if is_link(directory) or not directory.is_dir():
            raise ValueError('Task workspace is missing or linked')
        if directory.resolve().parent != self.root:
            raise ValueError('Task workspace escaped root')
        return TaskWorkspace(identifier, directory)

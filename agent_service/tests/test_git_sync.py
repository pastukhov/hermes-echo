import hashlib
import json
import subprocess
from pathlib import Path

import pytest

from codex_voice.git_sync import GitSync


def git(root, *args):
    return subprocess.check_output(['git', '-C', str(root), *args], text=True).strip()


@pytest.fixture
def repo(tmp_path):
    remote = tmp_path / 'remote.git'
    subprocess.run(['git', 'init', '--bare', str(remote)], check=True, capture_output=True)
    root = tmp_path / 'vault';root.mkdir()
    git(root, 'init', '-b', 'main')
    git(root, 'config', 'user.name', 'Test')
    git(root, 'config', 'user.email', 'test@example.invalid')
    (root/'initial.md').write_text('initial')
    git(root, 'add', '.')
    git(root, 'commit', '-m', 'initial')
    git(root, 'remote', 'add', 'origin', str(remote))
    git(root, 'push', '-u', 'origin', 'main')
    return root, remote


def enqueue(root, ident='1', content='idea', order=1):
    page = root / 'Hermes/ideas/idea.md';page.parent.mkdir(parents=True, exist_ok=True)
    page.write_text(content)
    queue = root/'Hermes/.sync';queue.mkdir(exist_ok=True)
    task = queue/f'{ident}.json'
    task.write_text(json.dumps(dict(id=ident, created_ns=order, files={
        'Hermes/ideas/idea.md': hashlib.sha256(content.encode()).hexdigest()})))
    return task


def test_sync_commits_and_pushes_only_published_pages_preserving_index(repo):
    root, remote = repo
    (root/'personal.md').write_text('private draft')
    git(root, 'add', 'personal.md')
    task = enqueue(root)
    result = GitSync(root).run_once()
    assert result['status'] == 'synced'
    assert git(root, 'diff', '--cached', '--name-only') == 'personal.md'
    assert git(root, 'show', '--pretty=', '--name-only', 'HEAD') == 'Hermes/ideas/idea.md'
    assert git(remote, 'rev-parse', 'refs/heads/main') == git(root, 'rev-parse', 'HEAD')
    assert not task.exists()


def test_push_failure_retries_without_duplicate_commit(repo):
    root, remote = repo
    task = enqueue(root)
    git(root, 'remote', 'set-url', 'origin', str(remote) + '-offline')
    assert GitSync(root).run_once()['status'] == 'retry'
    assert task.exists()
    head = git(root, 'rev-parse', 'HEAD')
    git(root, 'remote', 'set-url', 'origin', str(remote))
    assert GitSync(root).run_once()['status'] == 'synced'
    assert git(root, 'rev-parse', 'HEAD') == head
    assert not task.exists()


def test_sync_coalesces_updates_but_blocks_unpublished_manual_changes(repo):
    root, remote = repo
    enqueue(root, 'first', 'old', 1)
    enqueue(root, 'second', 'new', 2)
    assert GitSync(root).run_once()['status'] == 'synced'
    task = enqueue(root, 'third', 'published', 3)
    (root/'Hermes/ideas/idea.md').write_text('unpublished manual edit')
    before = git(root, 'rev-parse', 'HEAD')
    assert GitSync(root).run_once()['status'] == 'conflict'
    assert git(root, 'rev-parse', 'HEAD') == before
    assert task.exists()


def test_rejects_paths_outside_hermes_and_symlinks(repo):
    root, _ = repo
    task = enqueue(root)
    payload = json.loads(task.read_text())
    payload['files'] = {'personal.md': hashlib.sha256(b'private').hexdigest()}
    task.write_text(json.dumps(payload))
    assert GitSync(root).run_once()['status'] == 'conflict'
    task.unlink()
    task = enqueue(root)
    page = root/'Hermes/ideas/idea.md';page.unlink()
    (root/'outside.md').write_text('idea')
    page.symlink_to(root/'outside.md')
    assert GitSync(root).run_once()['status'] == 'conflict'

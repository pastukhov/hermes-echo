"""Host-side Git outbox consumer. Credentials never enter the voice container."""
from __future__ import annotations

import asyncio
import fcntl
import hashlib
import json
import logging
import os
from pathlib import Path
import subprocess

logger = logging.getLogger(__name__)


class GitSync:
    def __init__(self, vault: Path):
        self.vault = Path(vault).resolve()
        self.queue = self.vault / 'Hermes/.sync'
        self.last_result = {'status': 'idle'}

    def _git(self, *args, check=True):
        env = {**os.environ, 'GIT_TERMINAL_PROMPT': '0',
               'GIT_SSH_COMMAND': 'ssh -o BatchMode=yes -o ConnectTimeout=10'}
        return subprocess.run(['git', '-C', str(self.vault), *args], env=env,
                              text=True, capture_output=True, timeout=30, check=check)

    def run_once(self):
        if not self.queue.is_dir() or self.queue.is_symlink():
            return {'status': 'idle'}
        try:
            with (self.queue / 'writer.lock').open('a') as lock:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                result = self._sync()
        except BlockingIOError:
            return {'status': 'busy'}
        except (subprocess.SubprocessError, OSError):
            result = {'status': 'retry'}
        except (ValueError, KeyError, TypeError):
            result = {'status': 'conflict'}
        self.last_result = result
        return result

    def _sync(self):
        tasks = []
        for path in self.queue.glob('*.json'):
            if path.is_symlink():
                raise ValueError('symlink task')
            data = json.loads(path.read_text())
            tasks.append((data['created_ns'], path, data))
        if not tasks:
            return {'status': 'idle'}
        # Later publications supersede earlier hashes of the same page.
        files = {}
        for _, _, task in sorted(tasks):
            files.update(task['files'])
        if not files:
            raise ValueError('empty publication')
        for relative, expected in files.items():
            parts = Path(relative).parts
            if (len(parts) < 2 or parts[0] != 'Hermes' or '..' in parts or
                    '.sync' in parts or not relative.endswith('.md')):
                raise ValueError('unmanaged path')
            path = self.vault
            for part in parts:
                path = path / part
                if path.is_symlink():
                    raise ValueError('symlink page')
            if hashlib.sha256(path.read_bytes()).hexdigest() != expected:
                raise ValueError('unpublished edit')
        # Refuse detached HEAD, another repository root, and merge conflicts.
        if Path(self._git('rev-parse', '--show-toplevel').stdout.strip()).resolve() != self.vault:
            raise ValueError('wrong repository')
        branch = self._git('symbolic-ref', '--quiet', '--short', 'HEAD').stdout.strip()
        if self._git('ls-files', '-u').stdout:
            raise ValueError('unmerged index')
        paths = sorted(files)
        dirty = self._git('status', '--porcelain', '--', *paths).stdout
        if dirty:
            # --only commits these working-tree paths while retaining other staged files.
            self._git('add', '--', *paths)
            self._git('commit', '--only', '-m', f'voice: publish {len(tasks)} Obsidian update(s)', '--', *paths)
        revision = self._git('rev-parse', 'HEAD').stdout.strip()
        # Never pull/rebase/force implicitly: divergence remains a visible retry.
        self._git('push', 'origin', f'HEAD:refs/heads/{branch}')
        for _, path, _ in tasks:
            path.unlink()
        return {'status': 'synced', 'commit': revision, 'updates': len(tasks)}

    async def run(self):
        previous_status = None
        while True:
            result = await asyncio.to_thread(self.run_once)
            if result['status'] not in ('idle', 'busy') and result['status'] != previous_status:
                logger.info('Obsidian Git synchronization: %s', result['status'])
            previous_status = result['status']
            await asyncio.sleep(15)

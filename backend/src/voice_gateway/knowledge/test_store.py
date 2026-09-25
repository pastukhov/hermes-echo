import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from backend.src.voice_gateway.knowledge.store import KnowledgeStore, KnowledgeConflict
from backend.src.voice_gateway.models.hermes_response import HermesNote


def capture(store, number=1, text='Хочу вести идеи'):
    job = dict(device_id='mic', request_id=f'request-{number}', turn_id=f'turn-{number}',
               created_at=f'2026-09-25T10:00:{number:02d}+00:00')
    source = store.capture(job, text)
    return source, store.context('mic', source, text), job


def note(source, operation='capture', target=None, pages=True):
    return HermesNote.model_validate(dict(create=True, title='Идеи', content='Вести идеи, бюджет 1000, не 10000.',
        tags=['идеи'], knowledge=dict(operation=operation, target_id=target, pages=[dict(
            path='wiki/concepts/ideas.md', title='Идеи', content='Идеи сохраняются с контекстом.', sources=[source]
        )] if pages else [])))


@pytest.fixture
def store(tmp_path):
    vault = tmp_path / 'vault'
    vault.mkdir()
    return KnowledgeStore(vault, tmp_path / 'state')


def test_capture_publish_and_idempotent_replay(store):
    source, context, job = capture(store)
    receipt = store.publish(source, 'mic', note(source), context, 'untrusted claim')
    assert receipt['reply'].startswith('Сохранил мысль')
    assert store.capture(job, 'Хочу вести идеи') == source
    assert store.publish(source, 'mic', note(source), context, '') == receipt
    assert len(list((store.root / 'ideas').glob('*.md'))) == 1
    assert (store.root / 'log.md').read_text().count(source) == 2  # source + idea path
    page = (store.root / 'wiki/concepts/ideas.md').read_text()
    assert f'[[Hermes/sources/{source}' in page
    assert store.context('mic', source, 'дополни')['active_idea_id'] == source
    with pytest.raises(KnowledgeConflict):
        store.capture(job, 'different source')


def test_amend_preserves_sources_and_uses_same_idea(store):
    first, context, _ = capture(store)
    store.publish(first, 'mic', note(first), context, '')
    second, context, _ = capture(store, 2, 'Не 1000, а 2000')
    result = store.publish(second, 'mic', note(second, 'amend', first), context, '')
    assert result['idea_id'] == first
    assert len(list((store.root / 'ideas').glob('*.md'))) == 1
    body = (store.root / f'ideas/{first}.md').read_text()
    assert first in body and second in body


def test_manual_edit_after_context_is_never_overwritten(store):
    first, context, _ = capture(store)
    store.publish(first, 'mic', note(first), context, '')
    second, context, _ = capture(store, 2)
    page = store.root / 'wiki/concepts/ideas.md'
    page.write_text(page.read_text() + '\nПравка вручную\n')
    before = page.read_bytes()
    with pytest.raises(KnowledgeConflict):
        store.publish(second, 'mic', note(second), context, '')
    assert page.read_bytes() == before
    assert not (store.root / f'ideas/{second}.md').exists()


def test_crash_recovery_replays_journal_without_duplicate_log(store, monkeypatch):
    source, context, _ = capture(store)
    original = store._apply
    def crash(changes):
        original(changes[:1])
        raise OSError('simulated power loss')
    monkeypatch.setattr(store, '_apply', crash)
    with pytest.raises(OSError):
        store.publish(source, 'mic', note(source), context, '')
    restored = KnowledgeStore(store.vault, store.state)
    assert restored.receipt(source)['source_id'] == source
    assert (store.root / 'wiki/concepts/ideas.md').exists()


def test_recovery_stops_when_manual_edit_conflicts(store, monkeypatch):
    source, context, _ = capture(store)
    original = store._apply
    def crash(changes):
        original(changes[:1])
        raise OSError('power loss')
    monkeypatch.setattr(store, '_apply', crash)
    with pytest.raises(OSError):
        store.publish(source, 'mic', note(source), context, '')
    page = store.root / f'ideas/{source}.md'
    page.write_text('Ручная правка после сбоя')
    restored = KnowledgeStore(store.vault, store.state)
    with pytest.raises(KnowledgeConflict):
        restored.receipt(source)
    assert page.read_text() == 'Ручная правка после сбоя'


def test_rejects_unknown_sources_broken_links_and_traversal(store):
    source, context, _ = capture(store)
    proposed = note(source)
    proposed.knowledge.pages[0].sources = ['0' * 32]
    with pytest.raises(KnowledgeConflict):
        store.publish(source, 'mic', proposed, context, '')
    proposed = note(source)
    proposed.content = '[[Hermes/wiki/concepts/missing]]'
    with pytest.raises(KnowledgeConflict):
        store.publish(source, 'mic', proposed, context, '')
    with pytest.raises(ValidationError):
        HermesNote.model_validate(dict(knowledge=dict(operation='capture', pages=[dict(
            path='../escape.md', title='x', content='x', sources=[source])])) )
    assert not (store.root / f'ideas/{source}.md').exists()


def test_rejects_symlink_and_unseen_existing_page(store, tmp_path):
    source, context, _ = capture(store)
    outside = tmp_path / 'outside'
    outside.mkdir()
    (store.root / 'ideas').symlink_to(outside)
    with pytest.raises(KnowledgeConflict):
        store.publish(source, 'mic', note(source), context, '')
    assert list(outside.iterdir()) == []


def test_query_and_plan_do_not_change_active_idea(store):
    first, context, _ = capture(store)
    store.publish(first, 'mic', note(first), context, '')
    second, context, _ = capture(store, 2)
    query = HermesNote.model_validate(dict(knowledge=dict(operation='query')))
    assert store.publish(second, 'mic', query, context, 'Ответ из базы')['reply'] == 'Ответ из базы'
    third, context, _ = capture(store, 3)
    store.publish(third, 'mic', note(third, 'plan', first, pages=False), context, '')
    assert (store.root / f'builds/{third}.md').exists()
    assert store.context('mic', third, '')['active_idea_id'] == first


def test_lint_detects_broken_links_without_reading_raw_as_instructions(store):
    from backend.src.voice_gateway.knowledge.__main__ import lint
    source, context, _ = capture(store, text='Текст с [[не ссылка]]')
    store.publish(source, 'mic', note(source), context, '')
    assert lint(store.vault) == []
    with (store.root / 'index.md').open('a') as output:
        output.write('\n[[Hermes/wiki/concepts/missing]]\n')
    assert lint(store.vault)[0]['kind'] == 'broken_link'


def test_publication_emits_git_outbox_after_files_with_exact_hashes(store):
    import hashlib
    source, context, _ = capture(store)
    store.publish(source, 'mic', note(source), context, '')
    task = json.loads((store.root / f'.sync/{source}.json').read_text())
    assert f'Hermes/sources/{source}.md' in task['files']
    assert 'Hermes/schema.md' in task['files']
    for path, expected in task['files'].items():
        assert hashlib.sha256((store.vault / path).read_bytes()).hexdigest() == expected
    assert (store.root / '.sync/writer.lock').exists()

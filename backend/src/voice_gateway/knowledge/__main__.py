"""Read-only validation of the managed vault: python -m ...knowledge VAULT."""
import argparse
import json
from pathlib import Path
import re


def lint(vault: Path) -> list[dict]:
    root = vault / 'Hermes'
    findings = []
    linked = set()
    pages = [path for path in root.rglob('*.md') if not path.is_symlink()]
    for path in pages:
        relative = path.relative_to(root).as_posix()
        if relative.startswith('sources/'):
            continue  # Raw speech can contain arbitrary bracketed text.
        for target in re.findall(r'\[\[([^\]]+)\]\]', path.read_text()):
            target = target.split('|', 1)[0].split('#', 1)[0]
            if not target.startswith('Hermes/'):
                findings.append(dict(kind='unscoped_link', page=relative, target=target))
                continue
            destination = vault / (target if target.endswith('.md') else target + '.md')
            if '..' in Path(target).parts or not destination.resolve().is_relative_to(root.resolve()):
                findings.append(dict(kind='unsafe_link', page=relative))
            elif not destination.is_file():
                findings.append(dict(kind='broken_link', page=relative, target=target))
            else:
                linked.add(destination)
    for path in pages:
        if path.parent.name == 'sources' and path not in linked:
            findings.append(dict(kind='source_without_note', page=path.relative_to(root).as_posix()))
    return findings


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Check Hermes Obsidian links without changing notes')
    parser.add_argument('vault', type=Path)
    args = parser.parse_args()
    issues = lint(args.vault)
    print(json.dumps({'findings': issues}, ensure_ascii=False, indent=2))
    raise SystemExit(1 if any(item['kind'] != 'source_without_note' for item in issues) else 0)

"""Rule 13: no secret in a string; nothing in the tree matches a vendor key's shape."""

import re
from pathlib import Path

from tests.rules.tree import FIXTURES, ROOT, TESTS

# Anthropic and OpenAI, ElevenLabs and Cartesia, Pinecall's own keys, LiveKit, Twilio, AWS, Google.
SHAPES = tuple(
    re.compile(shape)
    for shape in (
        r"\bsk-[A-Za-z0-9_-]{20,}",
        r"\bsk_[A-Za-z0-9]{20,}",
        r"\bpc_(?:live|test)_[A-Za-z0-9_-]{16,}",
        r"\bpc_ops_[0-9a-f]{32,}",
        r"\bpk_[A-Za-z0-9_-]{30,}",
        r"\bAPI(?=[A-Za-z0-9]*\d)[A-Za-z0-9]{12,}\b",
        r"\b(?:AC|SK)[0-9a-f]{32}\b",
        r"\bAKIA[0-9A-Z]{16}\b",
        r"\bAIza[0-9A-Za-z_-]{35}\b",
    )
)
READ = (
    *("*.py", "*.md", "*.toml", "*.sql", "*.yaml", "*.yml", "*.service", "*.env.example"),
    *("*.tf", "*.tfvars", "*.json", "*.sh", "*.tpl", "*.csv", "*.ts"),
)
NOT_READ = (ROOT / ".venv", FIXTURES, TESTS / "fakes", ROOT / ".git", ROOT / "build", ROOT / "dist")
# Wherever they sit, what the tree holds but never wrote: providers Terraform downloaded, packages.
NOT_WRITTEN_HERE = frozenset({".terraform", ".terraform-check", "node_modules", ".lab", ".local"})


def leaks(paths: list[Path]) -> list[str]:
    found: list[str] = []
    for path in paths:
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            if any(shape.search(line) for shape in SHAPES):
                found.append(f"{path}:{number}")
    return found


def tree_files() -> list[Path]:
    return [
        path
        for pattern in READ
        for path in sorted(ROOT.rglob(pattern))
        if not any(skipped == path or skipped in path.parents for skipped in NOT_READ)
        and NOT_WRITTEN_HERE.isdisjoint(path.relative_to(ROOT).parts)
    ]


def test_no_line_of_the_tree_looks_like_a_key() -> None:
    assert leaks(tree_files()) == []


def test_the_rule_catches_every_shape_it_names() -> None:
    assert len(leaks([FIXTURES / "rule13/leaked.py"])) == 5

"""Rule 22: a migration never breaks the release before it, or names who stopped reading."""

import re
import tomllib
from pathlib import Path

from tests.rules.tree import FIXTURES, PACKAGE, ROOT, relative

MIGRATIONS = PACKAGE / "postgres/migrations"

# The migrations the rule was born after; the ones before it are applied and never edited.
LAST_UNCHECKED = 20

# The line a contracting migration carries, once per table or column it contracts:
# `-- pinecall:contracts <table>[.<column>] unread since <version>`.
MARKER = re.compile(
    r"^-- pinecall:contracts (?P<what>[a-z_][a-z0-9_]*(?:\.[a-z_][a-z0-9_]*)?) "
    r"unread since (?P<version>\d+\.\d+\.\d+)$",
    re.MULTILINE,
)

A_NAME = r'"?([a-z_][a-z0-9_]*)"?'
# A table may be named with its schema: the name is what follows the dot.
SCHEMA = r'(?:"?[a-z_][a-z0-9_]*"?\.)?'
ALTER_TABLE = re.compile(
    rf"^alter\s+table\s+(?:if\s+exists\s+)?(?:only\s+)?{SCHEMA}{A_NAME}\s+(.*)$", re.DOTALL
)
CREATE_TABLE = re.compile(rf"^create\s+table\s+(?:if\s+not\s+exists\s+)?{SCHEMA}{A_NAME}\s*\(")
RENAMED = re.compile(r"\brename\s+to\s+")
DROP_TABLE = re.compile(r"^drop\s+table\s+(?:if\s+exists\s+)?(.*?)(?:\s+(?:cascade|restrict))?$")
DROP_COLUMN = re.compile(rf"^drop\s+(?:column\s+)?(?:if\s+exists\s+)?{A_NAME}")
RENAME_COLUMN = re.compile(rf"^rename\s+(?:column\s+)?{A_NAME}\s+to\s+")
ADD_COLUMN = re.compile(rf"^add\s+(?:column\s+)?(?:if\s+not\s+exists\s+)?{A_NAME}\s+(.*)$")
SET_NOT_NULL = re.compile(rf"^alter\s+(?:column\s+)?{A_NAME}\s+set\s+not\s+null$")
# What follows ADD or DROP when it is not a column: a constraint comes and goes on its own.
NOT_A_COLUMN = re.compile(r"^(?:add|drop)\s+(?:constraint|primary|unique|foreign|check|exclude)\b")
# A value an old release's insert does not give is given by the column itself.
GIVEN = re.compile(r"\b(?:default|generated)\b")
NOT_NULL = re.compile(r"\bnot\s+null\b")

REFUSED = "{file}: contracts {what}; the release before it may read it: {marker}"
TOO_NEW = "{file}: {what} unread since {version}, a release after this checkout's {current}"
HOW = "say `-- pinecall:contracts {what} unread since <the release that stopped reading it>`"


def contracted(sql: str) -> list[str]:
    """Return every table or column the migration drops, renames or makes NOT NULL, in order."""
    found: list[str] = []
    statements = _statements(sql)
    # A table renamed away and made again under its name in the same migration (a swap: the old
    # one becomes a partition of the new) keeps the name the release before it reads.
    made = {created.group(1) for created in map(CREATE_TABLE.match, statements) if created}
    for statement in statements:
        dropped = DROP_TABLE.match(statement)
        if dropped is not None:
            found += [
                name.strip().rsplit(".", 1)[-1].strip('"') for name in dropped.group(1).split(",")
            ]
            continue
        altered = ALTER_TABLE.match(statement)
        if altered is not None:
            found += [
                what
                for what in _altered(altered.group(1), altered.group(2))
                if not (what == altered.group(1) and what in made and RENAMED.search(statement))
            ]
    return found


def refused(paths: list[Path], current: str) -> list[str]:
    """Return every contraction no marker answers, and every marker naming a later release."""
    found: list[str] = []
    for path in paths:
        text = path.read_text(encoding="utf-8")
        markers = {marker["what"]: marker["version"] for marker in MARKER.finditer(text)}
        for what in contracted(text):
            if what not in markers:
                marker = HOW.format(what=what)
                found.append(REFUSED.format(file=relative(path), what=what, marker=marker))
            elif _version(markers[what]) > _version(current):
                found.append(
                    TOO_NEW.format(
                        file=relative(path), what=what, version=markers[what], current=current
                    )
                )
    return found


def checked_migrations(folder: Path = MIGRATIONS) -> list[Path]:
    """Return the migrations after the last one the rule leaves alone, in order."""
    return [path for path in sorted(folder.glob("*.sql")) if _number(path) > LAST_UNCHECKED]


def this_release() -> str:
    """Return the version this checkout says it is: the last one released."""
    with (ROOT / "pyproject.toml").open("rb") as project:
        return str(tomllib.load(project)["project"]["version"])


def _altered(table: str, actions: str) -> list[str]:
    found: list[str] = []
    for action in _split(actions):
        if NOT_A_COLUMN.match(action):
            continue
        if re.match(r"^rename\s+to\s+", action):
            found.append(table)
            continue
        added = ADD_COLUMN.match(action)
        if added is not None:
            is_contract = NOT_NULL.search(added.group(2)) and not GIVEN.search(added.group(2))
            found += [f"{table}.{added.group(1)}"] if is_contract else []
            continue
        for shape in (DROP_COLUMN, RENAME_COLUMN, SET_NOT_NULL):
            matched = shape.match(action)
            if matched is not None:
                found.append(f"{table}.{matched.group(1)}")
                break
    return found


# Comments out, lower case, one statement per item; a body quoted in dollars is not read.
def _statements(sql: str) -> list[str]:
    bare = re.sub(r"\$\$.*?\$\$", "''", sql, flags=re.DOTALL)
    bare = re.sub(r"--[^\n]*", "", bare)
    return [" ".join(item.split()).lower() for item in bare.split(";") if item.strip()]


# The actions of one ALTER TABLE, split on the commas outside parentheses.
def _split(actions: str) -> list[str]:
    parts: list[str] = []
    depth = 0
    current = ""
    for character in actions:
        depth += {"(": 1, ")": -1}.get(character, 0)
        if character == "," and depth == 0:
            parts.append(current.strip())
            current = ""
            continue
        current += character
    return [*parts, current.strip()]


def _number(path: Path) -> int:
    return int(path.name.split("_", 1)[0])


def _version(text: str) -> tuple[int, ...]:
    return tuple(int(part) for part in text.split("."))


def test_no_migration_contracts_what_the_release_before_it_may_read() -> None:
    assert refused(checked_migrations(), this_release()) == []


def test_the_rule_reads_every_way_a_migration_contracts() -> None:
    sql = (FIXTURES / "rule22/0101_contracts.sql").read_text(encoding="utf-8")
    assert contracted(sql) == [
        "calls",
        "old_notes",
        "call_log_head.written",
        "call_log_head.note",
        "call_log_head.kept",
        "call_log_head.seen",
        "call_facts.outcome",
        "call_facts",
    ]


def test_the_rule_leaves_alone_what_the_release_before_it_can_live_with() -> None:
    sql = (FIXTURES / "rule22/0102_expands.sql").read_text(encoding="utf-8")
    assert contracted(sql) == []


def test_a_contraction_is_refused_without_its_marker_and_with_one_naming_a_later_release() -> None:
    folder = FIXTURES / "rule22"
    unmarked = "tests/rules/fixtures/rule22/0101_contracts.sql"
    assert refused(checked_migrations(folder), "0.1.2") == [
        *(
            REFUSED.format(file=unmarked, what=what, marker=HOW.format(what=what))
            for what in contracted((folder / "0101_contracts.sql").read_text(encoding="utf-8"))
        ),
        TOO_NEW.format(
            file="tests/rules/fixtures/rule22/0103_marked.sql",
            what="call_log_head.spent",
            version="0.1.3",
            current="0.1.2",
        ),
    ]


def test_the_rule_starts_after_the_migrations_already_applied() -> None:
    assert [path.name for path in checked_migrations(FIXTURES / "rule22")] == [
        "0101_contracts.sql",
        "0102_expands.sql",
        "0103_marked.sql",
    ]

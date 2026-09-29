"""Rule 21: the wire's pages describe it whole: every event, command and shape, field by field."""

import inspect
import re
import types
import typing
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, TypeAliasType

from pydantic import BaseModel
from pydantic.fields import FieldInfo

from pinecall.domain import agent, names
from pinecall.wire import commands, events, frames, metrics, parts, state
from tests.rules.tree import FIXTURES, ROOT

PAGES = ROOT / "docs/wire"

# Where a name on a page is looked for, in this order: the wire first, then what it is made of.
MODULES = (frames, parts, state, metrics, commands, events, names, agent)

# The metrics page is one table of every block; the other pages hold a section per name.
METRICS_PAGE = "metrics.md"

SECTION = re.compile(r"^### `([^`]+)`\n", re.MULTILINE)
FIELD_ROW = re.compile(r"^\| `([^`]+)` \| `([^`]+)` \| (yes|no) \|", re.MULTILINE)
BLOCK_ROW = re.compile(r"^\| `([A-Za-z]+)` \| `([^`]+)` \| [^|]* \| (yes|no) \|", re.MULTILINE)
VALUES = re.compile(r"^One of: ((?:`[^`]+`(?:, )?)+)\.$", re.MULTILINE)
MEMBERS = re.compile(r"^One of ((?:`[^`]+`(?:, )?)+), told apart by `([^`]+)`\.$", re.MULTILINE)
MAP = re.compile(r"^A map by name: every value is a `([^`]+)`\.$", re.MULTILINE)
DATA = re.compile(r"^Data: `([^`]+)`", re.MULTILINE)
QUOTED = re.compile(r"`([^`]+)`")

SCALARS: Mapping[object, str] = {str: "string", int: "integer", float: "number", bool: "boolean"}

UNIONS: frozenset[object] = frozenset({types.UnionType, typing.Union})
LISTS: frozenset[object] = frozenset({list, tuple, frozenset, set})

# Named aliases the page writes as the JSON they stand for.
AS_JSON = {"JsonObject": "object", "Json": "any"}

type Resolver = Callable[[str], object | None]


@dataclass(frozen=True)
class Row:
    """One field as a page or the code says it: its type as written, and whether it is required."""

    type: str
    required: bool


@dataclass(frozen=True)
class Described:
    """What a page says a name is: fields, a closed list, a union, a map, or a shape's data."""

    fields: dict[str, Row] = field(default_factory=dict[str, Row])
    values: tuple[str, ...] = ()
    members: tuple[str, ...] = ()
    map_of: str | None = None
    data: str | None = None


def rendered(annotation: object) -> str:
    """The type as the pages write it: `string | null`, `CostRow[]`, `"say" | "whisper"`."""
    if isinstance(annotation, TypeAliasType):
        return AS_JSON.get(annotation.__name__, annotation.__name__)
    if inspect.isclass(annotation) and issubclass(annotation, BaseModel):
        return annotation.__name__
    simple = SCALARS.get(annotation) or ("null" if annotation is type(None) else None)
    return simple or _composite(typing.get_origin(annotation), typing.get_args(annotation))


def written(pages: Path) -> dict[str, Described]:
    """Every name the pages describe, and what each says about it."""
    found: dict[str, Described] = {}
    for page in sorted(pages.glob("*.md")):
        text = page.read_text(encoding="utf-8")
        if page.name == METRICS_PAGE:
            for block, name, required in BLOCK_ROW.findall(text):
                described = found.setdefault(block, Described())
                described.fields[name] = Row(type="", required=required == "yes")
            continue
        titles = SECTION.split(text)[1:]
        for title, body in zip(titles[::2], titles[1::2], strict=True):
            found[title] = _described(body)
    return found


def expected(model: object) -> Described:
    """What the code says a name is, in the words a page would say it."""
    if isinstance(model, TypeAliasType):
        value = model.__value__
        if typing.get_origin(value) is typing.Annotated:
            value = typing.get_args(value)[0]
        if typing.get_origin(value) is Literal:
            return Described(values=tuple(str(item) for item in typing.get_args(value)))
        if typing.get_origin(value) in (dict, Mapping):
            return Described(map_of=rendered(typing.get_args(value)[1]))
        return Described(members=tuple(rendered(item) for item in typing.get_args(value)))
    if inspect.isclass(model) and issubclass(model, BaseModel):
        return Described(
            fields={
                info.alias or name: Row(rendered(info.annotation), _required(info))
                for name, info in model.model_fields.items()
            }
        )
    return Described()


def drift(pages: Path, resolve: Resolver, must: Iterable[str]) -> list[str]:
    """What the pages and the code disagree on, each in one line that names both sides."""
    on_pages = written(pages)
    found = [f"{name}: in the code, on no page" for name in must if name not in on_pages]
    for name, described in on_pages.items():
        model = resolve(name)
        if model is None:
            found.append(f"{name}: on a page, not in the code")
            continue
        if described.data is not None:
            if getattr(model, "__name__", None) != described.data:
                found.append(f"{name}: the page says its data is {described.data}")
            continue
        found += _differences(name, described, expected(model))
    return found


def shapes() -> list[str]:
    """Every model the wire's shape modules define, but the base every model stands on."""
    return [
        name
        for module in (frames, parts, state, metrics)
        for name, model in inspect.getmembers(module, inspect.isclass)
        if issubclass(model, BaseModel)
        and model.__module__ == module.__name__
        and model is not frames.WireModel
    ]


def resolved(name: str) -> object | None:
    """The event, the command, or the model or alias of the wire a page names."""
    if name in events.EVENTS:
        return events.EVENTS[name]
    if name in commands.COMMANDS:
        return commands.COMMANDS[name]
    for module in MODULES:
        found = getattr(module, name, None)
        if isinstance(found, TypeAliasType) or (
            inspect.isclass(found) and issubclass(found, BaseModel)
        ):
            return found
    return None


def test_every_event_command_and_shape_is_on_its_page_as_the_code_has_it() -> None:
    assert drift(PAGES, resolved, [*events.EVENTS, *commands.COMMANDS, *shapes()]) == []


def test_the_rule_catches_a_field_a_type_and_a_flag_the_page_got_wrong() -> None:
    class Ringing(BaseModel):
        to: str
        tries: int | None = None
        channel: Literal["phone", "web"]

    models: dict[str, object] = {"call.ringing": Ringing}
    assert drift(FIXTURES / "rule21", models.get, ["call.ringing", "call.ended"]) == [
        "call.ended: in the code, on no page",
        "call.ringing: to is required in the code, not on the page",
        "call.ringing: tries is integer | null in the code, string on the page",
        "call.ringing: channel in the code, on no page",
        "call.ringing: gone on the page, not in the code",
        "Mystery: on a page, not in the code",
    ]


# A discriminator the model fills in itself (`kind`, `verb`, `role`) always travels written.
def _required(info: FieldInfo) -> bool:
    if info.is_required():
        return True
    literal = typing.get_origin(info.annotation) is Literal
    return literal and len(typing.get_args(info.annotation)) == 1


def _composite(origin: object, arguments: tuple[object, ...]) -> str:
    if origin is typing.Annotated:
        return rendered(arguments[0])
    if origin is Literal:
        return " | ".join(f'"{value}"' for value in arguments)
    if origin in UNIONS:
        present = [rendered(item) for item in arguments if item is not type(None)]
        return " | ".join(present + (["null"] if type(None) in arguments else []))
    if origin in LISTS:
        return f"{rendered(arguments[0])}[]"
    return "object"


def _described(body: str) -> Described:
    rows = {
        name: Row(type_, required == "yes") for name, type_, required in FIELD_ROW.findall(body)
    }
    if rows:
        return Described(fields=rows)
    if (values := VALUES.search(body)) is not None:
        return Described(values=tuple(QUOTED.findall(values.group(1))))
    if (members := MEMBERS.search(body)) is not None:
        return Described(members=tuple(QUOTED.findall(members.group(1))))
    if (mapping := MAP.search(body)) is not None:
        return Described(map_of=mapping.group(1))
    if (data := DATA.search(body)) is not None:
        return Described(data=data.group(1))
    return Described()


# An optional field may be absent: the page writes its type, the model `T | None = None`. A closed
# list no page names is written out whole, value by value.
def _same_type(page: str, code: Row) -> bool:
    if page in (code.type, _expanded(code.type)):
        return True
    return (
        not code.required and code.type.endswith(" | null") and page == code.type[: -len(" | null")]
    )


def _expanded(name: str) -> str:
    alias = resolved(name)
    if isinstance(alias, TypeAliasType) and typing.get_origin(alias.__value__) is Literal:
        return rendered(alias.__value__)
    return name


def _differences(name: str, page: Described, code: Described) -> list[str]:
    if page.values != code.values or page.members != code.members or page.map_of != code.map_of:
        page_list = page.values or page.members or page.map_of
        code_list = code.values or code.members or code.map_of
        return [f"{name}: the page lists {page_list}, the code {code_list}"]
    found: list[str] = []
    for key, row in code.fields.items():
        on_page = page.fields.get(key)
        if on_page is None:
            found.append(f"{name}: {key} in the code, on no page")
            continue
        if on_page.type and not _same_type(on_page.type, row):
            found.append(f"{name}: {key} is {row.type} in the code, {on_page.type} on the page")
        if on_page.required != row.required:
            flag = "required" if row.required else "optional"
            other = "not" if row.required else "required"
            found.append(f"{name}: {key} is {flag} in the code, {other} on the page")
    found += [
        f"{name}: {key} on the page, not in the code"
        for key in page.fields
        if key not in code.fields
    ]
    return found

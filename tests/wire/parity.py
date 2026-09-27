"""Parity with the generated protocol: every model of one family of it, here field for field."""

from pathlib import Path
from types import ModuleType
from typing import TypeAliasType, get_args, get_origin

from pinecall.wire.frames import WireModel
from pinecall_protocol._base import WireModel as TheirModel

FIXTURES = Path(__file__).resolve().parents[3] / "protocol/python/pinecall_protocol/fixtures"
GOLDEN_LOG = FIXTURES / "call-log-golden.json"
GOLDEN_STATE = FIXTURES / "call-log-golden.state.json"

# Ours says Json where theirs says Any.
AS_THEIRS = {"Json": "Any", "JsonObject": "dict[str, Any]"}


def models_of(module: ModuleType, base: type) -> dict[str, type]:
    """Return every model a module declares, by name."""
    return {
        name: one
        for name, one in vars(module).items()
        if isinstance(one, type) and issubclass(one, base) and one is not base
    }


def mismatches(ours: ModuleType, *theirs: ModuleType) -> list[str]:
    """Return every way the models of `theirs` differ from the ones `ours` declares."""
    mine = models_of(ours, WireModel)
    found: list[str] = []
    for family in theirs:
        for name, their in models_of(family, TheirModel).items():
            if name not in mine:
                found.append(f"{name} is on the wire and not in {ours.__name__}")
                continue
            found += _fields_differ(name, mine[name], their)
    return found


def _fields_differ(name: str, our: type[WireModel], their: type[TheirModel]) -> list[str]:
    if set(our.model_fields) != set(their.model_fields):
        return [f"{name}: fields {sorted(our.model_fields)} != {sorted(their.model_fields)}"]
    found: list[str] = []
    for field, declared in their.model_fields.items():
        mine = our.model_fields[field]
        if mine.alias != declared.alias:
            found.append(f"{name}.{field}: alias")
        if mine.is_required() != declared.is_required():
            found.append(f"{name}.{field}: required")
        if _shape(mine.annotation) != _shape(declared.annotation):
            found.append(f"{name}.{field}: type")
        plain_default = not declared.is_required() and declared.default_factory is None
        if plain_default and mine.default != declared.default:
            found.append(f"{name}.{field}: default")
    return found


def _shape(annotation: object) -> str:
    if isinstance(annotation, TypeAliasType):
        return AS_THEIRS.get(annotation.__name__) or _shape(annotation.__value__)
    origin = get_origin(annotation)
    if origin is not None and get_args(annotation):
        inner = ", ".join(_shape(one) for one in get_args(annotation))
        return f"{_shape(origin)}[{inner}]"
    return annotation.__name__ if isinstance(annotation, type) else repr(annotation)

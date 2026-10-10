"""What a class declares of its environment: the settings it fixes, each over the org's own."""

from collections.abc import Mapping

from pinecall.domain.agent import (
    AgentConfig,
    Docs,
    Greeting,
    Hangup,
    MemoryPolicy,
    Turn,
    Voice,
)
from pinecall.domain.errors import DeclarationRefused
from pinecall.domain.names import Json
from pinecall.providers.build import doing
from pinecall.providers.declared import model_from
from pinecall.wire.parts import AgentConfig as Declared
from pinecall.wire.parts import GreetingConfig, VoiceConfig

NO_VENDOR = (
    "a voice the class declares names its vendor and the voice: @voice('<vendor>', '<voice>')"
)


# Sent as null, a setting goes back to the org's; left out, it stays as it was declared before.
def environment_of(current: AgentConfig, declared: Declared) -> dict[str, object]:
    """The fields of the config a declaration's environment changes, and the settings it fixes."""
    converted = _converted(declared)
    changed: dict[str, object] = {}
    fixed = set(current.fixed)
    for name in declared.model_fields_set & converted.keys():
        if getattr(declared, name) is None:
            fixed.discard(name)
            continue
        fixed.add(name)
        field, value = converted[name]
        changed[field] = value
    changed["fixed"] = frozenset(fixed)
    return changed


# Each setting a class may declare, by the declaration's name: the config's field it becomes and
# its value there. A setting not sent is converted too and never used.
def _converted(declared: Declared) -> dict[str, tuple[str, object]]:
    memory = declared.memory
    return {
        "language": ("language", declared.language),
        "voice": ("voice", _voice_of(declared.voice)),
        "llm": ("llm", model_from(declared.llm, "llm")),
        "stt": ("stt", model_from(declared.stt, "stt")),
        "judge": ("judge", model_from(declared.judge, "llm")),
        "greeting": ("greeting", _greeting_of(declared.greeting)),
        "hangup": ("hangup", None if declared.hangup is None else Hangup(declared.hangup.when)),
        "turn": ("turn", None if declared.turn is None else Turn(**declared.turn.model_dump())),
        "says": ("says", {item.word: item.spoken for item in declared.says or ()}),
        "hears": ("hears", tuple(declared.hears or ())),
        "knowledge": ("knowledge", None if declared.knowledge is None else declared.knowledge.text),
        "docs": ("bases", () if declared.docs is None else (Docs(**declared.docs.model_dump()),)),
        "memory": (
            "memory",
            None
            if memory is None
            else MemoryPolicy(remember=tuple(memory.remember), forget=tuple(memory.forget)),
        ),
        "record": ("record", declared.record),
    }


def _greeting_of(wanted: GreetingConfig | None) -> Greeting | None:
    return None if wanted is None else Greeting(**wanted.model_dump())


def _voice_of(wanted: VoiceConfig | None) -> Voice | None:
    if wanted is None:
        return None
    if not wanted.provider or not (wanted.voice_id or wanted.name):
        raise DeclarationRefused(NO_VENDOR)
    return Voice(
        provider=doing(wanted.provider.lower(), "tts"),
        model=wanted.model,
        voice_id=wanted.voice_id or wanted.name,
        builds=wanted.builds,
        options=_options(wanted.options),
    )


def _options(given: Mapping[str, Json] | None) -> dict[str, Json]:
    return {} if given is None else dict(given)

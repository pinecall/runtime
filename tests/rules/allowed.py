"""The exceptions the rules allow, each with its file and a one-line reason."""

from dataclasses import dataclass


@dataclass(frozen=True)
class Allowed:
    """One allowed occurrence: the file, the text the rule matches, and why."""

    file: str
    text: str
    why: str


# Rule 3: Protocol, ABC, cast(, TYPE_CHECKING.
PROTOCOLS_AND_CASTS: tuple[Allowed, ...] = ()

# Rule 9: noqa, type: ignore, pyright: ignore, pragma: no cover.
SUPPRESSIONS: tuple[Allowed, ...] = (
    Allowed(
        "tests/fakes.py",
        "pyright: " + "ignore",
        "livekit's ChunkedStream takes its TTS unparameterised, so its constructor is Unknown",
    ),
    Allowed(
        "pinecall/session/prompt.py",
        "pyright: " + "ignore",
        "livekit's to_provider_format is overloaded and returns a bare list[dict]; the one way to "
        "keep static blocks apart is to override it",
    ),
    Allowed(
        "pinecall/session/session.py",
        "pyright: " + "ignore",
        "livekit's event emitters take a bare Callable, and Agent's constructor and "
        "AgentSession.start carry its unparameterised generics",
    ),
    Allowed(
        "pinecall/session/room.py",
        "pyright: " + "ignore",
        "livekit's Room emits through a bare Callable, and BackgroundAudioPlayer.start takes an "
        "unparameterised AgentSession",
    ),
    Allowed(
        "pinecall/session/widget.py",
        "pyright: " + "ignore",
        "livekit's Room emits through a bare Callable",
    ),
    Allowed(
        "pinecall/worker/job.py",
        "pyright: " + "ignore",
        "livekit's Room emits through a bare Callable",
    ),
    Allowed(
        "pinecall/worker/main.py",
        "pyright: " + "ignore",
        "AgentSession.start carries livekit's unparameterised generics",
    ),
)

# importlib, getattr on a string.
IMPORTS_BY_NAME: tuple[Allowed, ...] = (
    Allowed(
        "pinecall/providers/build.py",
        "importlib.import_module",
        "an installed livekit plugin is a vendor: found in livekit.plugins, imported by its name",
    ),
    Allowed(
        "pinecall/providers/build.py",
        "getattr(",
        "the plugin's class is the one the stage or the operator's row names (STT, STTv2)",
    ),
    Allowed(
        "pinecall/providers/voices.py",
        "getattr(",
        "a plugin that lists its voices does it with list_voices; the others have none",
    ),
)

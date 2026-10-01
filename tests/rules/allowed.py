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
        "pinecall/session/voice.py",
        "pyright: " + "ignore",
        "smart-turn-livekit ships no py.typed; its SmartTurnDetector is typed inline",
    ),
    Allowed(
        "tests/fakes/livekit.py",
        "pyright: " + "ignore",
        "livekit's ParticipantInfo stub types `kind` as its enum and refuses the wire's int",
    ),
    Allowed(
        "tests/fakes/acme.py",
        "pyright: " + "ignore",
        "livekit's ChunkedStream takes its TTS unparameterised, so its constructor is Unknown",
    ),
    Allowed(
        "pinecall/session/_prompt.py",
        "pyright: " + "ignore",
        "livekit's to_provider_format is overloaded and returns a bare list[dict]; the one way to "
        "keep static blocks apart is to override it",
    ),
    Allowed(
        "pinecall/session/_agent.py",
        "pyright: " + "ignore",
        "livekit's Agent constructor carries its unparameterised generics",
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
        "livekit's Room emits through a bare Callable",
    ),
    Allowed(
        "pinecall/session/hold.py",
        "pyright: " + "ignore",
        "BackgroundAudioPlayer.start takes an unparameterised AgentSession; PyAV's add_stream, "
        "encode and mux are typed loosely",
    ),
    Allowed(
        "pinecall/session/widget.py",
        "pyright: " + "ignore",
        "livekit's Room emits through a bare Callable",
    ),
    Allowed(
        "pinecall/worker/_job.py",
        "pyright: " + "ignore",
        "livekit's Room emits through a bare Callable",
    ),
    Allowed(
        "pinecall/worker/main.py",
        "pyright: " + "ignore",
        "AgentSession.start carries livekit's unparameterised generics",
    ),
    Allowed(
        "pinecall/process/signal.py",
        "pyright: " + "ignore",
        "redis-py's asyncio client takes untyped **kwargs (from_url, publish), an untyped "
        "callback, and hands its messages over as dicts it does not type",
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

# Rule 17: public modules no package reaches yet, each waiting for the door that will.
NOT_YET_REACHED: tuple[Allowed, ...] = (
    Allowed(
        "pinecall/providers/voices.py",
        "voices",
        "GET /v1/voices and the voice sample: doors of the last step",
    ),
    Allowed(
        "pinecall/retrieval/embed.py",
        "embed",
        "the knowledge and memory doors are on their own branches",
    ),
    Allowed(
        "pinecall/retrieval/search.py",
        "search",
        "the knowledge and memory doors are on their own branches",
    ),
    Allowed(
        "pinecall/wire/rest/retrieval.py", "rest.retrieval", "the bodies of those doors, the same"
    ),
)

# Rule 14: the files that create a task, and who cancels or awaits it.
TASK_OWNERS: tuple[Allowed, ...] = (
    Allowed(
        "pinecall/runner/main.py",
        "create_task",
        "working holds one per app; close() cancels and awaits them",
    ),
    Allowed(
        "pinecall/gateway/app.py",
        "create_task",
        "the lifespan's exit stack cancels the reaper and the rebuild",
    ),
    Allowed(
        "pinecall/gateway/api/chat.py",
        "create_task",
        "the socket's finally cancels the sender and the hang-up watch",
    ),
    Allowed(
        "pinecall/gateway/calls/threads.py",
        "create_task",
        "answering_now and handed hold them; closed() cancels and awaits them",
    ),
    Allowed(
        "pinecall/process/signal.py",
        "create_task",
        "the sender and the listener are cancelled and awaited in RedisSignal.close()",
    ),
    Allowed(
        "pinecall/gateway/api/apps.py",
        "create_task",
        "the socket's listener for calls bound to it is cancelled and awaited when it closes",
    ),
    Allowed(
        "pinecall/gateway/_served.py",
        "create_task",
        "a call's command listener is cancelled in close(); a dev.request's answer listener when "
        "its future is done",
    ),
    Allowed(
        "pinecall/evals/runs.py",
        "create_task",
        "the lease's renewal is cancelled and awaited when the run's alone() ends",
    ),
    Allowed(
        "pinecall/tenancy/remembered.py",
        "create_task",
        "the revocation listener is cancelled and awaited in RememberedKeys.close()",
    ),
    Allowed(
        "pinecall/process/shared.py",
        "create_task",
        "the listener and the beat are cancelled and awaited in Shared.close()",
    ),
    Allowed(
        "pinecall/log/_relay.py",
        "create_task",
        "a following's task and its fill are cancelled in Relay._let_go and awaited in close()",
    ),
    Allowed(
        "pinecall/session/session.py", "create_task", "closing and waiting are cancelled in close()"
    ),
    Allowed(
        "pinecall/log/_writer.py",
        "create_task",
        "the writing task ends when the queue is empty; Writer.drained() awaits it",
    ),
    Allowed("pinecall/session/call.py", "create_task", "the drain task ends in Writing.close()"),
    Allowed(
        "pinecall/session/hold.py", "create_task", "pending is cancelled when the player stops"
    ),
    Allowed(
        "pinecall/session/tools.py",
        "create_task",
        "_Running gathers every tool's task or cancels the rest",
    ),
    Allowed(
        "pinecall/session/widget.py",
        "create_task",
        "tailing is cancelled in close(); answers are held in the set",
    ),
    Allowed(
        "pinecall/tenancy/mail.py",
        "create_task",
        "in_flight holds every letter; the outbox awaits them at close",
    ),
    Allowed(
        "pinecall/worker/_job.py",
        "create_task",
        "commands and the timer are cancelled when the job ends",
    ),
    Allowed(
        "pinecall/worker/main.py",
        "create_task",
        "asyncio.wait on the set, then every task cancelled at shutdown",
    ),
)

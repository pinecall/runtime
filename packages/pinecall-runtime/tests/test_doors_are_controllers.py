"""A door is a controller: under api/, a coroutine is a door, a guard, a stream or a named role."""

import ast
from collections.abc import Iterator

import pytest

from pinecall_testkit.tree import ROOT

pytestmark = pytest.mark.unit

API = ROOT / "packages" / "pinecall-runtime" / "src" / "pinecall" / "api"

# What a FastAPI router decorates a door with.
DOOR_VERBS = frozenset({"get", "post", "put", "patch", "delete", "websocket", "api_route"})

# A dependency is named for what it hands the door (`get_x`), or for what it refuses (`require_x`);
# the ones a surface shares sit in its `deps.py`, and `scope/` is the request's own guards, whole.
GUARDS = ("get_", "require_")
GUARD_FILES = ("deps.py",)
GUARD_DIRECTORIES = ("scope/",)

# The files whose coroutines are not doors and are not verbs either, each with the role it plays.
# A verb — a use case that reads and writes the domain's ports — belongs in the domain package
# that owns the entity it changes (accounts/, telephony/, orgs/, live/, …); a new coroutine here
# fails the test below until it is a door, a guard, a stream, or one of these roles.
ROLES = {
    "app.py": "the lifespan: what the gateway starts and stops around its doors",
    "agents/socket.py": "the app socket's handler, command by command, for the life of a socket",
    "agents/call_commands.py": "the app socket's handlers for the commands that name a live call",
    "agents/dev.py": "the relay of a console's dev.* verb down the app's socket and back",
    "agents/registry_reads.py": "the answers of the agents and line doors, naming each holder",
    "agents/pipeline_report.py": "the pipeline door's report: what the agent measured, drawn",
    "agents/hold_melody.py": "the hold melody door's bytes: the org's clip, or the runtime's own",
    "calls/chat.py": "the chat socket's handler: a text call, turn by turn, for the socket's life",
    "calls/tools.py": "the app socket's handler for a tool result on a call this gateway serves",
    "calls/commands.py": "the socket that holds a worker's commands until the worker takes them",
    "calls/events.py": "the log stream every reader of a call is held on",
    "calls/listing.py": "the page two list doors draw: the calls that match, folded to rows",
    "calls/log_sink.py": "the log doors' shared reader, and the guard against another org's log",
    "calls/reaper.py": "a background task: the calls a gone worker left open, sealed",
    "calls/supervise/aiming.py": "the desk verbs' aim: which live call a supervisor's verb reaches",
    "memory/extraction.py": "the extraction door's runs: one conversation, one agent's config",
    "ops/box_signin.py": "the operator's sign-in page: each provider as it stands",
    "org/usage.py": "the usage feed's stream and its pages",
    "evals/runner.py": "a background job a door starts: a run of goldens, conversation by one",
    "evals/golden_call.py": "the run's one golden conversation, played against the agent",
    "evals/spoken_golden.py": "the run's one spoken golden, placed and waited on",
    "evals/agent_finished.py": "a run's wait for the agent's last answer to land in the log",
    "evals/run_attachment.py": "a run's watcher: cancelled with the run it watches",
    "whatsapp/unanswered.py": "a background task: the waiting room answered, round after round",
    "whatsapp/waiting_loop.py": "a background task: the waiting room's loop, started with the app",
}


def coroutines_under_api() -> Iterator[tuple[str, ast.AsyncFunctionDef]]:
    """Every top-level coroutine of every module under api/, with its path from api/."""
    for path in sorted(API.rglob("*.py")):
        for node in ast.parse(path.read_text(encoding="utf-8")).body:
            if isinstance(node, ast.AsyncFunctionDef):
                yield path.relative_to(API).as_posix(), node


def is_a_door(node: ast.AsyncFunctionDef) -> bool:
    """Whether a router decorates it."""
    return any(
        isinstance(decorator, ast.Call)
        and isinstance(decorator.func, ast.Attribute)
        and decorator.func.attr in DOOR_VERBS
        for decorator in node.decorator_list
    )


def is_a_guard(path: str, node: ast.AsyncFunctionDef) -> bool:
    """Whether it is a dependency: named as one, or where the surface keeps its dependencies."""
    return (
        node.name.startswith(GUARDS)
        or path.endswith(GUARD_FILES)
        or path.startswith(GUARD_DIRECTORIES)
    )


def is_a_stream(node: ast.AsyncFunctionDef) -> bool:
    """Whether it yields: an async generator, what an SSE or a socket's feed is drawn from."""
    return any(isinstance(inner, ast.Yield | ast.YieldFrom) for inner in ast.walk(node))


def test_under_api_a_coroutine_is_a_door_a_guard_a_stream_or_a_named_role() -> None:
    strays = [
        f"{path}:{node.lineno} {node.name}"
        for path, node in coroutines_under_api()
        if not is_a_door(node)
        and not is_a_guard(path, node)
        and not is_a_stream(node)
        and path not in ROLES
    ]
    assert not strays, "a verb under api/ — it goes to the domain package it changes: " + ", ".join(
        strays
    )


def test_every_role_names_a_file_that_still_has_a_coroutine_of_its_role() -> None:
    held = {path for path, node in coroutines_under_api() if not is_a_door(node)}
    assert set(ROLES) <= held, sorted(set(ROLES) - held)


def test_the_walk_reaches_the_doors() -> None:
    doors = [node for _path, node in coroutines_under_api() if is_a_door(node)]
    assert len(doors) > 100, "the walk found almost no doors: the tree moved under the test"

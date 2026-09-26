"""The app socket's call-scoped commands: everything an app says about a call that is running."""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from pinecall.api.agents.handlers import Handler, Socket, asked, handles
from pinecall.api.calls.codes import NOBODY_ISSUED, claimed
from pinecall.providers import declaration
from pinecall.session.text.session import TextSession
from pinecall.types import DeclarationRefused
from pinecall_protocol import Command
from pinecall_protocol.commands import (
    AgentReply,
    AgentSay,
    CallAttention,
    CallCallback,
    CallClaim,
    CallEvent,
    CallHangup,
    CallLog,
    CallTransfer,
    PromptSet,
    SessionConfigure,
    StateSet,
    ToolsSet,
)
from pinecall_protocol.events import AgentConfigured, CallTransferred

# A command that names a call this process is not running is not a bad command: it is a command
# that arrived late, or on the wrong node. The app hears which, in the protocol's own words.
NO_SESSION = "no_session"

# The code the agent heard is not one a page is waiting on: call.claim's refusal, by the protocol.
NO_CODE = "no_code"

# What a verb of the line answers on a written conversation. A chat has no audio to hold, no leg
# to send anywhere and no tones to send down one, so each is refused by name — and the one thing
# that DOES work on a thread is named, because the app asking for a transfer wants a person.
NO_LINE = (
    "{type}: a written conversation has no line — call.attention asks a person to take this one"
)

type CallHandler = Callable[[Command, TextSession], Awaitable[None]]


# The one piece of ceremony every call-scoped handler shares: find the session, prove this socket
# speaks for the agent, and refuse in one place instead of ten.
def in_a_call(type: str) -> Callable[[CallHandler], Handler]:
    """Register a handler that only makes sense inside a live call, and hand it the session."""

    def take(handler: CallHandler) -> Handler:
        async def with_a_session(socket: Socket, command: Command) -> None:
            if socket.registry.on(socket.env, command.agent, socket.id) is None:
                raise DeclarationRefused(f"agent {command.agent} is not registered on this socket")
            # The socket knows nothing about text/: it hands back whatever is running on that
            # call, and this is the module that knows a running call is a TextSession.
            session: TextSession | None = socket.live.of(command.call)
            if session is not None and session.agent == command.agent:
                await handler(command, session)
                return
            # A call this process does not run itself is a worker's, and the live memory holds
            # the command until that worker reads it off GET /v1/calls/{call}/commands. What runs
            # it there is session/voice/commands.py — see docs/decisions/voice-bridge.md.
            if socket.live.commanded(command.call, command.agent, command):
                return
            await socket.refuse(
                command.agent,
                NO_SESSION,
                f"{command.type} names call {command.call!r}, which is not running here",
                command.model_dump(),
            )

        handles(type)(with_a_session)
        return with_a_session

    return take


# The two rules below are written as plain functions and registered on top of them, because the
# eval runner (api/evals/golden_call.py) stands in for the app's backend and must set a
# golden's opening state and inject its facts through the very same doors a live app does.
async def configure(session: TextSession, wanted: SessionConfigure) -> None:
    """Set this call up before the first turn: the app's state, and any config of its own."""
    if wanted.config is not None:
        session.config = declaration.configured(session.config, wanted.config)
        await session.emit(
            "agent.configured",
            AgentConfigured(changed=list(declaration.changed_by(wanted.config))),
        )
    if wanted.state is not None:
        await session.set_state(StateSet(state=dict(wanted.state)))


async def an_event(session: TextSession, fact: CallEvent) -> None:
    """A fact from the tenant's backend: declared events land in the log, undeclared are refused."""
    if not session.config.accepts(fact.name, "app"):
        raise DeclarationRefused(
            f"agent {session.agent} never declared the event {fact.name!r} from the app: "
            f"declare it in agent.configure before sending it"
        )
    await session.receives(fact.name, fact.data)


@in_a_call("session.configure")
async def configure_the_session(command: Command, session: TextSession) -> None:
    """Set this call up before the first turn: the app's state, and any config of its own."""
    await configure(session, asked(command, SessionConfigure))


@in_a_call("prompt.set")
async def set_the_prompt(command: Command, session: TextSession) -> None:
    """Rewrite one block of the prompt, whole, by name; an undeclared name is refused."""
    wanted = asked(command, PromptSet)
    await session.set_prompt(wanted.name, wanted.text)


@in_a_call("tools.set")
async def set_the_tools(command: Command, session: TextSession) -> None:
    """The subset of the declared tools the model may see in the state the app is in now."""
    await session.set_tools(asked(command, ToolsSet).tools)


@in_a_call("state.set")
async def set_the_state(command: Command, session: TextSession) -> None:
    """The app's state moved; the whole of it travels, with what changed it when we know."""
    await session.set_state(asked(command, StateSet))


@in_a_call("agent.say")
async def say_it(command: Command, session: TextSession) -> None:
    """The agent says this, verbatim, with no model in the loop."""
    await session.say(asked(command, AgentSay).text)


# allow_interruptions has no meaning where nothing is being played: in text a reply is one entry,
# and there is no audio for the caller to talk over.
@in_a_call("agent.reply")
async def reply_now(command: Command, session: TextSession) -> None:
    """One model turn now, guided by an instruction the caller never sees."""
    await session.reply(asked(command, AgentReply).instructions)


@in_a_call("call.event")
async def take_an_event(command: Command, session: TextSession) -> None:
    """A fact from the tenant's backend: declared events land in the log, undeclared are refused."""
    await an_event(session, asked(command, CallEvent))


@in_a_call("call.log")
async def write_a_line(command: Command, session: TextSession) -> None:
    """A line of the app's own in the call's log, with a seq like everything else."""
    line = asked(command, CallLog)
    await session.log_custom(line.name, line.data)


# The three verbs of a spoken line. On a call this gateway does not run they are held for the
# worker, exactly as every other command is; here, on a thread, they are refused by name.
@in_a_call("call.hold")
async def hold_the_line(command: Command, session: TextSession) -> None:  # noqa: ARG001 — the shape
    """Put the caller on hold: a thread has nothing to hold, and says so."""
    raise DeclarationRefused(NO_LINE.format(type=command.type))


@in_a_call("call.unhold")
async def give_the_line_back(command: Command, session: TextSession) -> None:  # noqa: ARG001
    """Take the caller off hold: a thread was never holding anybody."""
    raise DeclarationRefused(NO_LINE.format(type=command.type))


@in_a_call("call.dtmf")
async def send_tones(command: Command, session: TextSession) -> None:  # noqa: ARG001
    """Touch tones down the line: a thread has no line to send them down."""
    raise DeclarationRefused(NO_LINE.format(type=command.type))


# The refusal is the OUTCOME here and not a refusal frame: whoever asked for a transfer is waiting
# for `call.transferred` to say how it went, and "nothing was attempted" is how it went.
@in_a_call("call.transfer")
async def transfer_the_call(command: Command, session: TextSession) -> None:
    """Send the caller on: a thread has no line, so nothing moves and the log says why."""
    wanted = asked(command, CallTransfer)
    await session.emit(
        "call.transferred",
        CallTransferred(to=wanted.to, mode=None, ok=False, error=NO_LINE.format(type=command.type)),
    )


@in_a_call("call.attention")
async def ask_for_a_person(command: Command, session: TextSession) -> None:
    """The thread waits for a supervisor to take it, and the model answers nothing meanwhile."""
    await session.attending.asked(asked(command, CallAttention))


@in_a_call("call.callback")
async def call_them_back(command: Command, session: TextSession) -> None:
    """The caller asked to be rung back: the number goes in the log for the app to read and dial."""
    await session.call_back(asked(command, CallCallback))


@in_a_call("call.hangup")
async def hang_up(command: Command, session: TextSession) -> None:
    """The app ends the call: call.ended, then the summary, then the log is sealed."""
    asked(command, CallHangup)
    await session.hangup("agent_hung_up", "agent")


# Not a command held for the worker: a code is the gateway's to bind, whichever process runs the
# call, and a phone call's worker has nothing to do with a code the agent heard said. So the call is
# asked of this gateway's live memory, and refused when it runs nowhere here.
@handles("call.claim")
async def claim_the_code(socket: Socket, command: Command) -> None:
    """The caller said the code the page shows: this call is bound to it, or `no_code`."""
    wanted = asked(command, CallClaim)
    if socket.registry.on(socket.env, command.agent, socket.id) is None:
        raise DeclarationRefused(f"agent {command.agent} is not registered on this socket")
    served = None if command.call is None else socket.live.served(command.call)
    if command.call is None or served is None or served.agent != command.agent:
        said = f"call.claim names call {command.call!r}, which is not running here"
        await socket.refuse(command.agent, NO_SESSION, said, command.model_dump())
        return
    if not await claimed(socket.codes, served, command.call, wanted.code, "agent"):
        said = NOBODY_ISSUED.format(code=wanted.code, agent=command.agent)
        await socket.refuse(command.agent, NO_CODE, said, command.model_dump())

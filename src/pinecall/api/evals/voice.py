"""POST /v1/evals/voice: one simulated caller put on a real line, and the turns they got out."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import Field

from pinecall.api._deps import EvalsKeyDep, LlmsDep, SettingsDep, StoreDep, TuningDep, VaultDep
from pinecall.api.agents.registry import RegistryDep
from pinecall.api.agents.tuned import tuned_for
from pinecall.api.evals.listening import the_call_is_over, until_the_answer_lands
from pinecall.auth.keys import held_by
from pinecall.evals.caller import (
    NO_MODEL,
    Asking,
    Persona,
    heard_in,
    what_they_say_next,
)
from pinecall.evals.calling import Line, a_simulated_call
from pinecall.evals.speech import Speaking
from pinecall.log.replay import whole
from pinecall.orgs.vault import keys_brought_by
from pinecall.providers.models import NoProvider
from pinecall.providers.tuning import the_llm, the_voice
from pinecall.types import DeclarationRefused
from pinecall_protocol import WireModel

router = APIRouter()

# A room nobody answered, no key for the voice the caller speaks with, no LiveKit pair: three ways
# a spoken call cannot happen, and all three are the operator's to fix rather than a verdict about
# the agent.
NO_LINE = "the simulated call could not be held: {broke}"


class Calling(WireModel):
    """What a `--voice` run asks for: whose call, who is calling, and how spoilt their line is."""

    # The caller mints it, because a room's name IS the call id (worker/entry.py:82) and the
    # terminal has to be able to watch the log while the call is still happening.
    call: str
    agent: str
    persona: Persona
    turns: int = 6
    # None is a clean line. A number is how many dB under the caller's own voice the interferer
    # sits — a television behind the caller, at the level the measurement is written down at.
    interferer_db: float | None = None
    packet_loss: float = Field(default=0.0, ge=0, le=1)


class Called(WireModel):
    """What the call left behind: where its log is, how much was said, and on what kind of line."""

    call: str
    turns: int
    line: str


# The whole call runs HERE, because everything it needs is here: the LiveKit pair that signs the
# caller's seat, the provider key the persona is played with — the org's own when it brought one,
# as `/v1/evals/caller` plays it — and the log the transcript is read back from. The terminal mints
# the call id and watches the log — see docs/decisions/simulate.md.
@router.post("/v1/evals/voice")
async def a_voice_call(
    said: Calling,
    key: EvalsKeyDep,
    llms: LlmsDep,
    store: StoreDep,
    settings: SettingsDep,
    vault: VaultDep,
    registry: RegistryDep,
    kept: TuningDep,
) -> Called:
    """Dispatch the agent into a room, put the persona on the line out loud, and hang up."""
    keys = await keys_brought_by(vault, key.org)
    # The model that plays the caller and the voice it speaks in are the persona's own three
    # knobs, read by the agent's own parser; a persona that set none is played as every caller
    # was. The door that wrote the persona refused a typo already, so a refusal here is a row
    # written before this build knew the word.
    try:
        llm = llms(the_llm(said.persona.llm), keys)
        declared = the_voice(said.persona.tts, said.persona.voice)
    except DeclarationRefused as refused:
        raise HTTPException(422, str(refused)) from refused
    except NoProvider as missing:
        raise HTTPException(503, NO_MODEL.format(missing=missing)) from missing
    line = Line(interferer_db=said.interferer_db, packet_loss=said.packet_loss)
    # The caller speaks the agent's language in a voice the agent does not have: both read off
    # the config the agent runs on, in the corner of the socket the call is dispatched to.
    speaking = Speaking(keys=keys, declared=declared)
    held = registry.of(key.env, said.agent, held_by(key))
    # A slug is one org's, so a socket holding it may be another tenant's: its declaration —
    # the voice, the language — is not this key's to read, and the call runs bare instead.
    if held is not None and held.org == key.org:
        running = await tuned_for(kept, key.org, key.env, held_by(key), said.agent, held.config)
        speaking = Speaking(
            language=running.config.language,
            agents_voice=None if running.config.voice is None else running.config.voice.voice_id,
            keys=keys,
            declared=declared,
        )

    # The conversation so far is read off the call's own log rather than kept a second time here:
    # the worker writes every turn through this gateway, so the log is the transcript, and it is
    # the same reason ring 4's judges read it back (docs/decisions/scoring.md).
    async def next_line(turns_left: int) -> tuple[str, bool]:
        """What this caller says next, given everything the log says has been said so far."""
        entries = await whole(store, said.call)
        # Somebody hung up while the caller was waiting for its answer: the console's Stop, the
        # app, the agent. This loop is the gateway's and the call is the worker's, so the log is
        # the only place it hears of it — and a caller that did not look went on saying its
        # remaining turns to an empty room. No line is the hangup (evals/calling.py:357).
        if the_call_is_over(entries):
            return "", True
        asking = Asking(
            persona=said.persona,
            heard=heard_in(entries),
            turns_left=turns_left,
        )
        improvised = await what_they_say_next(llm, asking)
        return improvised.say, improvised.hangup

    try:
        spoken = await a_simulated_call(
            said.call,
            said.agent,
            turns=said.turns,
            next_line=next_line,
            line=line,
            settings=settings,
            # The persona speaks again when the agent is listening again, never on a clock: the
            # same wait the golden runner makes, so a turn that runs a tool is not talked over.
            settled=lambda so_far: until_the_answer_lands(store, said.call, so_far),
            # Who is being played, so call.started says it: the worker writes that entry, and
            # this name is all that reaches it. Without it a spoken simulation was a call like
            # any other and the Personas screen could not find its own runs.
            persona=said.persona.name or None,
            # And the caller's own rule for the call, by the same road, for the judge at hang-up.
            accepts_when=said.persona.accepts_when or None,
            declines_when=said.persona.declines_when or None,
            org=key.org,
            env=key.env,
            holder=held_by(key),
            speaking=speaking,
        )
    except (TimeoutError, RuntimeError) as broke:
        raise HTTPException(503, NO_LINE.format(broke=broke)) from broke
    return Called(call=said.call, turns=spoken, line=line.said)

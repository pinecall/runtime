"""Tests for how a turn is taken: backchannels and false interruptions."""

from pinecall.domain.agent import Turn
from pinecall.session import text
from pinecall.session._hearing import policy_for
from pinecall.wire.commands import (
    TakeoverVerb,
)
from tests.conftest import postgres
from tests.session.conftest import (
    Box,
    a_session,
)
from tests.session.test_session import (
    A_SUPERVISOR,
    NOBODY,
    spoken_call,
    supervised,
)


@postgres
async def test_interruptions_are_judged_locally_and_a_cut_sentence_is_never_said_twice(
    box: Box,
) -> None:
    options = spoken_call(box, NOBODY).live.options
    assert options.interruption.get("mode") == "vad"
    assert options.interruption.get("resume_false_interruption") is False
    assert options.interruption.get("false_interruption_timeout") == 1.0
    assert options.preemptive_generation.get("enabled") is False


def test_agreement_alone_is_a_backchannel_and_anything_that_takes_the_floor_is_not() -> None:
    spanish = policy_for("es-ES")
    assert spanish.is_a_backchannel("sí, claro")
    assert spanish.is_a_backchannel("Mm, ok.")
    assert not spanish.is_a_backchannel("sí, pero el martes no puedo")
    assert not spanish.is_a_backchannel("")


def test_what_only_agrees_depends_on_the_agents_language() -> None:
    assert policy_for("en-US").is_a_backchannel("uh-huh, right")
    assert not policy_for("en-US").is_a_backchannel("vale")
    assert policy_for("pt-BR").is_a_backchannel("aham, certo")
    assert policy_for(None).is_a_backchannel("vale")
    assert policy_for(None).is_a_backchannel("yep")
    assert policy_for("es").min_words == 2
    assert policy_for("es", Turn(min_interruption_words=1)).min_words == 1
    assert policy_for("es").min_speech_s is None
    assert policy_for("es", Turn(min_interruption_ms=800)).min_speech_s == 0.8


@postgres
async def test_a_takeover_with_nothing_to_interrupt_still_takes_the_line(box: Box) -> None:
    session = a_session(box, NOBODY)
    await session.start()
    await session.apply(supervised(TakeoverVerb()))
    assert session.call.taken_by == A_SUPERVISOR
    await text.end(session, "caller_hung_up", "caller")

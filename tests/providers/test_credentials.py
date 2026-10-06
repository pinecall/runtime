"""The org's own key runs anything, the box's what it lends; neither is refused mid-call."""

import pytest

from pinecall.domain.agent import AgentConfig, Model, Voice
from pinecall.domain.errors import DeclarationRefused, NotAllowed, NotAvailable
from pinecall.domain.names import JsonObject
from pinecall.providers.build import Running, Vendor
from pinecall.providers.catalog import Providers, Stage
from pinecall.providers.credentials import (
    Keyring,
    Pipeline,
    lent,
    parse_lending,
    pipeline,
    readiness,
    refusal,
    running,
    thinking,
)

AGENT = AgentConfig(slug="clinica-norte", language="es-ES")
THE_BOX = {"anthropic": "box-a", "deepgram": "box-d", "cartesia": "box-c", "whatsapp": "box-w"}


def test_an_org_that_brought_its_own_key_runs_the_vendor_with_it() -> None:
    chosen = running(Keyring(own={"anthropic": "org-a"}, box=THE_BOX), "anthropic", None)
    assert (chosen.credentials, chosen.lent) == ("org-a", False)


def test_an_org_that_brought_none_runs_every_vendor_on_the_boxs() -> None:
    chosen = running(Keyring(box=THE_BOX), "deepgram", None)
    assert (chosen.credentials, chosen.lent) == ("box-d", True)


def test_a_key_the_org_brought_for_one_vendor_is_never_read_for_another() -> None:
    chosen = running(Keyring(own={"anthropic": "org-a"}, box=THE_BOX), "cartesia", None)
    assert chosen.credentials == "box-c"


def test_a_vendor_with_neither_key_is_refused_by_name_before_the_call_starts() -> None:
    with pytest.raises(NotAvailable, match="gladia has no key"):
        running(Keyring(box=THE_BOX), "gladia", None)


def test_the_whatsapp_token_is_read_through_the_very_same_question() -> None:
    assert running(
        Keyring(own={"whatsapp": "org-w"}, box=THE_BOX), "whatsapp", None
    ).credentials == ("org-w")


def test_credentials_of_more_than_a_key_travel_whole() -> None:
    azure: JsonObject = {"speech_key": "k", "speech_region": "westeurope"}
    assert running(Keyring(own={"azure": azure}), "azure", None).credentials == azure


def test_an_orgs_own_key_runs_any_model_whatever_the_box_lends() -> None:
    keys = Keyring(own={"anthropic": "org-a"}, box=THE_BOX, lends=frozenset())
    assert running(keys, "anthropic", "claude-opus-5").credentials == "org-a"


def test_the_box_refuses_a_model_it_does_not_lend_before_building_anything() -> None:
    keys = Keyring(box=THE_BOX, lends=frozenset({"anthropic/claude-haiku-4-5"}))
    with pytest.raises(NotAllowed, match="anthropic/claude-opus-5 is not lent to this org"):
        running(keys, "anthropic", "claude-opus-5")


def test_nothing_said_lends_everything_the_box_has() -> None:
    assert lent(None, "anthropic", "claude-opus-5")


def test_an_empty_lending_lends_nothing() -> None:
    assert not lent(frozenset(), "deepgram", None)


def test_a_whole_vendor_lends_every_model_of_it_and_its_default() -> None:
    lends = frozenset({"deepgram"})
    assert lent(lends, "deepgram", "nova-3")
    assert lent(lends, "deepgram", None)


def test_a_model_entry_lends_its_snapshots_and_nothing_dearer() -> None:
    lends = frozenset({"anthropic/claude-haiku-4-5"})
    assert lent(lends, "anthropic", "claude-haiku-4-5-20251001")
    assert not lent(lends, "anthropic", "claude-sonnet-5")


def test_a_model_entry_does_not_lend_the_plugins_unnamed_default() -> None:
    assert not lent(frozenset({"anthropic/claude-haiku-4-5"}), "anthropic", None)


def test_the_refusal_names_what_was_asked_what_may_run_and_the_other_way() -> None:
    text = refusal(frozenset({"deepgram", "anthropic/claude-haiku-4-5"}), "openai", "gpt-5")
    assert text == (
        "openai/gpt-5 is not lent to this org: it may run on anthropic/claude-haiku-4-5, "
        "deepgram, or on a key of its own"
    )
    assert "nothing of the box's" in refusal(frozenset(), "openai", None)


def test_a_door_takes_entries_spelled_once_and_refuses_what_names_nothing() -> None:
    assert parse_lending([" Deepgram ", "anthropic/claude-Haiku-4-5"]) == {
        "deepgram",
        "anthropic/claude-Haiku-4-5",
    }
    with pytest.raises(DeclarationRefused, match="lends no vendor installed here"):
        parse_lending(["nobody"])
    with pytest.raises(DeclarationRefused, match="no model after the slash"):
        parse_lending(["anthropic/"])


def test_an_agent_that_declares_nothing_runs_the_boxs_defaults(configured: Providers) -> None:
    stages = pipeline(AGENT, configured, Keyring(box=THE_BOX))
    assert (stages.llm.vendor, stages.llm.model) == ("anthropic", "claude-haiku-4-5")
    assert (stages.stt.vendor, stages.stt.model) == ("deepgram", "flux-general-multi")
    assert (stages.tts.vendor, stages.tts.model) == ("cartesia", "sonic-3")


def test_every_vendor_an_agent_names_is_the_one_it_gets(configured: Providers) -> None:
    agent = AgentConfig(
        slug="clinica-norte",
        llm=Model(provider="groq", model="llama-3.3-70b"),
        stt=Model(provider="elevenlabs", model="scribe_v2_realtime"),
        voice=Voice(provider="hume", voice_id="v-1"),
    )
    keys = Keyring(own={"groq": "g", "elevenlabs": "e", "hume": "h"})
    stages = pipeline(agent, configured, keys)
    assert (stages.llm.vendor, stages.llm.model) == ("groq", "llama-3.3-70b")
    assert (stages.stt.vendor, stages.stt.model) == ("elevenlabs", "scribe_v2_realtime")
    assert (stages.tts.vendor, stages.tts.voice) == ("hume", "v-1")


def test_a_vendor_named_alone_runs_the_model_the_row_sets_else_the_plugins(
    configured: Providers,
) -> None:
    agent = AgentConfig(slug="clinica-norte", voice=Voice(provider="elevenlabs"))
    keys = Keyring(own={"elevenlabs": "e"}, box=THE_BOX)
    assert pipeline(agent, configured, keys).tts.model == "eleven_flash_v2_5"
    named = AgentConfig(slug="clinica-norte", llm=Model(provider="openai", model=""))
    assert pipeline(named, configured, Keyring(own={"openai": "o"}, box=THE_BOX)).llm.model is None


def test_what_the_row_tells_a_vendor_reaches_its_stage_and_no_other(
    configured: Providers,
) -> None:
    stages = pipeline(AGENT, configured, Keyring(box=THE_BOX))
    assert (stages.stt.builds, stages.stt.options, stages.stt.ends_the_turn) == (
        "STTv2",
        {"eot_timeout_ms": 1000},
        True,
    )
    assert stages.llm.options == {"caching": "ephemeral"}
    assert (stages.tts.builds, stages.tts.options, stages.tts.ends_the_turn) == (None, {}, False)


def test_the_model_judged_for_lending_is_the_one_that_runs(configured: Providers) -> None:
    keys = Keyring(
        box=THE_BOX, lends=frozenset({"anthropic/claude-haiku-4-5", "deepgram", "cartesia"})
    )
    assert pipeline(AGENT, configured, keys).llm.lent
    stingy = Keyring(box=THE_BOX, lends=frozenset({"anthropic/claude-sonnet-5"}))
    with pytest.raises(NotAllowed, match="anthropic/claude-haiku-4-5 is not lent"):
        pipeline(AGENT, configured, stingy)


def test_a_declared_language_reaches_the_voice_and_the_ears_as_its_primary_subtag(
    configured: Providers,
) -> None:
    stages = pipeline(AGENT, configured, Keyring(box=THE_BOX))
    assert (stages.stt.language, stages.tts.language, stages.llm.language) == ("es", "es", None)


def test_the_ears_listen_for_the_calls_language_first_then_the_rows_and_never_twice(
    configured: Providers,
) -> None:
    english = AgentConfig(slug="clinica-norte", language="en")
    assert pipeline(english, configured, Keyring(box=THE_BOX)).stt.hints == ("en", "es")
    unsaid = AgentConfig(slug="clinica-norte")
    assert pipeline(unsaid, configured, Keyring(box=THE_BOX)).stt.hints == ("es", "en")


def test_an_agent_that_chose_no_voice_speaks_the_rows_voice_for_its_language(
    configured: Providers,
) -> None:
    assert pipeline(AGENT, configured, Keyring(box=THE_BOX)).tts.voice == "voice-marta"
    chosen = AgentConfig(slug="clinica-norte", language="es", voice=Voice("cartesia", None, "mine"))
    assert pipeline(chosen, configured, Keyring(box=THE_BOX)).tts.voice == "mine"


def test_the_temperature_an_agent_sets_reaches_its_model(configured: Providers) -> None:
    agent = AgentConfig(slug="clinica-norte", llm=Model("anthropic", "", temperature=0.3))
    llm = pipeline(agent, configured, Keyring(box=THE_BOX)).llm
    assert llm.options == {"caching": "ephemeral", "temperature": 0.3}


def test_a_written_call_thinks_on_its_model_without_a_voice_anybody_keyed(
    configured: Providers,
) -> None:
    agent = AgentConfig(slug="clinica-norte", llm=Model("anthropic", "", temperature=0.3))
    llm = thinking(agent, configured, Keyring(own={"anthropic": "mine"}))
    assert (llm.vendor, llm.credentials, llm.options["temperature"]) == ("anthropic", "mine", 0.3)
    with pytest.raises(NotAvailable, match="deepgram"):
        pipeline(agent, configured, Keyring(own={"anthropic": "mine"}))


def test_one_process_serves_two_orgs_and_neither_is_built_with_the_others_key(
    configured: Providers,
) -> None:
    mine = pipeline(AGENT, configured, Keyring(own={"anthropic": "mine"}, box=THE_BOX))
    theirs = pipeline(AGENT, configured, Keyring(own={"anthropic": "theirs"}, box=THE_BOX))
    assert (mine.llm.credentials, theirs.llm.credentials) == ("mine", "theirs")


def test_the_seal_is_told_which_vendors_ran_on_the_boxs_key_once_each(
    configured: Providers,
) -> None:
    mixed = Keyring(own={"anthropic": "mine"}, box=THE_BOX)
    assert pipeline(AGENT, configured, mixed).lent == ["deepgram", "cartesia"]
    assert pipeline(AGENT, configured, Keyring(own=THE_BOX)).lent == []
    same = Pipeline(
        llm=Running("acme", "k", lent=True),
        stt=Running("acme", "k", lent=True),
        tts=Running("hume", "k"),
    )
    assert same.lent == ["acme"]


def test_a_vendor_is_yours_offered_or_bring_your_own_and_broken_where_it_does_not_import() -> None:
    installed = {
        "deepgram": Vendor("deepgram", frozenset({"stt", "tts"})),
        "hume": Vendor("hume", frozenset({"tts"})),
        "openai": Vendor("openai", frozenset({"llm", "stt", "tts"})),
        "upliftai": Vendor("upliftai", frozenset(), broken="No module named 'socketio'"),
    }
    keys = Keyring(
        own={"hume": "h"}, box={"deepgram": "d", "openai": "o"}, lends=frozenset({"deepgram"})
    )
    shown = readiness(installed, keys)
    assert [(item.name, item.does, item.availability) for item in shown] == [
        ("deepgram", ("stt", "tts"), "offered"),
        ("hume", ("tts",), "yours"),
        ("openai", ("llm", "stt", "tts"), "bring your own"),
        ("upliftai", (), "broken"),
    ]
    assert shown[3].broken == "No module named 'socketio'"


def test_a_vendor_the_box_holds_but_does_not_lend_this_org_is_bring_your_own() -> None:
    installed = {"deepgram": Vendor("deepgram", frozenset({"stt"}))}
    assert readiness(installed, Keyring(box={"deepgram": "d"}, lends=frozenset()))[
        0
    ].availability == ("bring your own")
    assert readiness(installed, Keyring(box={"deepgram": "d"}))[0].availability == "offered"


def with_fallbacks(configured: Providers, **fallbacks: tuple[Stage, ...]) -> Providers:
    """The row with these stages' defaults followed by those fallbacks."""
    defaults = {
        modality: stage.model_copy(update={"fallbacks": fallbacks.get(modality, ())})
        for modality, stage in configured.defaults.items()
    }
    return configured.model_copy(update={"defaults": defaults})


def test_a_row_without_fallbacks_builds_the_stages_it_built_before(configured: Providers) -> None:
    stages = pipeline(AGENT, configured, Keyring(box=THE_BOX))
    assert (stages.llm.fallbacks, stages.stt.fallbacks, stages.tts.fallbacks) == ((), (), ())


def test_each_fallback_runs_on_its_own_key_found_as_the_defaults_is(
    configured: Providers,
) -> None:
    row = with_fallbacks(
        configured,
        llm=(Stage(vendor="openai", model="gpt-5.4-mini"), Stage(vendor="groq")),
    )
    keys = Keyring(own={"openai": "org-o"}, box={**THE_BOX, "groq": "box-g"})
    llm = pipeline(AGENT, row, keys).llm
    assert (llm.vendor, llm.credentials) == ("anthropic", "box-a")
    assert [(item.vendor, item.model, item.credentials, item.lent) for item in llm.fallbacks] == [
        ("openai", "gpt-5.4-mini", "org-o", False),
        ("groq", None, "box-g", True),
    ]


def test_a_fallback_this_org_has_no_key_for_is_left_out_and_the_call_still_runs(
    configured: Providers,
) -> None:
    row = with_fallbacks(configured, llm=(Stage(vendor="gladia"), Stage(vendor="openai")))
    stingy = Keyring(
        box={**THE_BOX, "openai": "box-o"}, lends=frozenset({"anthropic", "deepgram", "cartesia"})
    )
    assert pipeline(AGENT, row, stingy).llm.fallbacks == ()


def test_an_agent_that_names_its_own_vendor_runs_it_alone(configured: Providers) -> None:
    row = with_fallbacks(configured, llm=(Stage(vendor="openai"),))
    agent = AgentConfig(slug="clinica-norte", llm=Model(provider="groq", model="llama-3.3-70b"))
    keys = Keyring(own={"groq": "g", "openai": "o"}, box=THE_BOX)
    assert pipeline(agent, row, keys).llm.fallbacks == ()


def test_a_fallback_hears_the_calls_languages_and_speaks_its_own_vendors_voice(
    configured: Providers,
) -> None:
    row = with_fallbacks(
        configured,
        stt=(Stage(vendor="soniox"),),
        tts=(Stage(vendor="elevenlabs"), Stage(vendor="hume")),
    ).model_copy(update={"voices": {**configured.voices, "elevenlabs/es": "voice-lucia"}})
    keys = Keyring(box={**THE_BOX, "soniox": "s", "elevenlabs": "e", "hume": "h"})
    stages = pipeline(AGENT, row, keys)
    (ears,) = stages.stt.fallbacks
    assert (ears.language, ears.hints) == ("es", ("es", "en"))
    assert [(item.vendor, item.voice) for item in stages.tts.fallbacks] == [
        ("elevenlabs", "voice-lucia"),
        ("hume", None),
    ]
    assert stages.tts.fallbacks[0].model == "eleven_flash_v2_5"


def test_the_seal_is_told_of_every_lent_vendor_a_fallback_may_run_on(
    configured: Providers,
) -> None:
    row = with_fallbacks(configured, llm=(Stage(vendor="openai"),), tts=(Stage(vendor="hume"),))
    keys = Keyring(own={"anthropic": "mine", "hume": "h"}, box={**THE_BOX, "openai": "box-o"})
    assert pipeline(AGENT, row, keys).lent == ["openai", "deepgram", "cartesia"]


def test_a_vendor_over_its_error_line_goes_behind_the_ones_that_are_not(
    configured: Providers,
) -> None:
    row = with_fallbacks(
        configured,
        llm=(Stage(vendor="openai"), Stage(vendor="groq")),
        tts=(Stage(vendor="elevenlabs"),),
    )
    keys = Keyring(box={**THE_BOX, "openai": "o", "groq": "g", "elevenlabs": "e"})
    stages = pipeline(AGENT, row, keys).demoting(frozenset({"anthropic", "openai", "cartesia"}))
    assert [item.vendor for item in (stages.llm, *stages.llm.fallbacks)] == [
        "groq",
        "anthropic",
        "openai",
    ]
    assert [item.vendor for item in (stages.tts, *stages.tts.fallbacks)] == [
        "elevenlabs",
        "cartesia",
    ]


def test_with_nothing_failing_or_nothing_to_step_to_the_order_is_the_rows(
    configured: Providers,
) -> None:
    row = with_fallbacks(configured, llm=(Stage(vendor="openai"),))
    keys = Keyring(box={**THE_BOX, "openai": "o"})
    assert pipeline(AGENT, row, keys) == pipeline(AGENT, row, keys).demoting(frozenset())
    alone = pipeline(AGENT, configured, Keyring(box=THE_BOX)).demoting(frozenset({"deepgram"}))
    assert (alone.stt.vendor, alone.stt.fallbacks) == ("deepgram", ())
    agent = AgentConfig(slug="clinica-norte", llm=Model(provider="groq", model="llama-3.3-70b"))
    named = pipeline(agent, row, Keyring(own={"groq": "g"}, box=THE_BOX)).demoting(
        frozenset({"groq"})
    )
    assert (named.llm.vendor, named.llm.fallbacks) == ("groq", ())

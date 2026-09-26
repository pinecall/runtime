"""The ears, option by option, with no socket opened: 16 kHz, our endpointing, our hints."""

import inspect

import pytest
from livekit.agents.utils import is_given
from livekit.plugins import deepgram, soniox

from pinecall.providers.registry import Asked, NoProvider
from pinecall.providers.stt import DEFAULT_HINTS, MAX_SILENCE_MS, VENDORS, hints_for
from pinecall.providers.stt.deepgram import DEFAULT_MODEL as FLUX
from pinecall.providers.stt.soniox import LATENCY_LEVEL, SENSITIVITY
from pinecall.settings import Settings

pytestmark = pytest.mark.unit

A_KEY = "nobody-will-ever-deploy-this"


def an_ask(**asked: object) -> Asked:
    """A process that read a key for both vendors of this modality."""
    settings = Settings(world="production", soniox_api_key=A_KEY, deepgram_api_key=A_KEY)
    return Asked(settings=settings, **asked)  # pyright: ignore[reportArgumentType]


def test_the_tuned_vendors_are_found_by_their_file_names() -> None:
    """`tuned` is the files under providers/stt/; `names` is those plus the whole catalog."""
    assert VENDORS.tuned == ("deepgram", "livekit", "soniox")
    assert set(VENDORS.tuned) < set(VENDORS.names)
    assert "cartesia" in VENDORS.names


def test_soniox_states_every_option_the_plugin_does_not_already_get_right() -> None:
    """The whole configuration is readable without a network of any kind."""
    built = VENDORS.build("soniox", an_ask(language="es"))
    assert isinstance(built, soniox.STT)
    params = built._params  # pyright: ignore[reportPrivateUsage]
    assert params.language_hints == ["es", "en"]
    assert params.endpoint_latency_adjustment_level == LATENCY_LEVEL == 2
    assert params.endpoint_sensitivity == SENSITIVITY == 0.3
    assert params.max_endpoint_delay_ms == MAX_SILENCE_MS == 1000


def test_the_agents_own_endpointing_wins_over_ours() -> None:
    built = VENDORS.build("soniox", an_ask(endpointing_ms=700))
    assert isinstance(built, soniox.STT)
    assert built._params.max_endpoint_delay_ms == 700  # pyright: ignore[reportPrivateUsage]


def test_deepgram_is_the_flux_model_on_the_v2_socket() -> None:
    built = VENDORS.build("deepgram", an_ask(language="pt"))
    assert isinstance(built, deepgram.STTv2)
    options = built._opts  # pyright: ignore[reportPrivateUsage]
    assert options.model == FLUX == "flux-general-multi"
    assert options.language_hint == ["pt", "es", "en"]
    assert options.eot_timeout_ms == MAX_SILENCE_MS


# The timeout is only the silence Flux is UNSURE about. A caller it is confidently wrong about is
# cut whatever the clock says, and Deepgram's own measurement of its default confidence — 0.7 — is
# that as much as a fifth of the turns it ends were ended before the person had finished. The bar
# is the knob for that, and the eager one is how it is raised without paying for it in latency.
def test_the_two_confidences_flux_ends_a_turn_on_are_the_agents_to_set() -> None:
    built = VENDORS.build("deepgram", an_ask(eot_threshold=0.85, eager_eot_threshold=0.4))
    assert isinstance(built, deepgram.STTv2)
    options = built._opts  # pyright: ignore[reportPrivateUsage]
    assert options.eot_threshold == 0.85
    assert options.eager_eot_threshold == 0.4


def test_an_agent_that_set_neither_is_left_at_the_plugins_own() -> None:
    """None would go out as `null` on a socket that refuses it: the sentinel is livekit's own."""
    built = VENDORS.build("deepgram", an_ask())
    assert isinstance(built, deepgram.STTv2)
    options = built._opts  # pyright: ignore[reportPrivateUsage]
    assert not is_given(options.eot_threshold)
    assert not is_given(options.eager_eot_threshold)


def test_an_agent_that_named_no_model_gets_the_plugins_own_realtime_one() -> None:
    """A model name equal to the plugin's own would be a second place to change it."""
    built = VENDORS.build("soniox", an_ask())
    assert isinstance(built, soniox.STT)
    assert built._params.model == soniox.STTOptions.model == "stt-rt-v5"  # pyright: ignore[reportPrivateUsage]


# The milestone's rule — 16 kHz even on PSTN, so the turn detector and the model read what a
# browser call would have produced — is satisfied by both plugins without an argument. This is
# what says so, read off their own signatures rather than off ours.
def test_both_ears_already_listen_at_16_khz_without_being_told_to() -> None:
    assert soniox.STTOptions.sample_rate == 16000  # soniox/stt.py:119
    assert _default_of(deepgram.STTv2, "sample_rate") == 16000  # deepgram/stt_v2.py:74


def _default_of(built: object, option: str) -> object:
    """One argument's default, read off the plugin's own signature."""
    return inspect.signature(built).parameters[option].default  # pyright: ignore[reportArgumentType]


def test_a_vendor_with_no_key_is_refused_at_the_door_of_the_call() -> None:
    keyless = Asked(settings=Settings(world="production", soniox_api_key=""))
    with pytest.raises(NoProvider, match="soniox has no API key"):
        VENDORS.build("soniox", keyless)


def test_an_undeclared_language_is_the_two_everybody_here_speaks() -> None:
    assert hints_for(None) == list(DEFAULT_HINTS) == ["es", "en"]


def test_a_declared_language_goes_first_and_is_never_repeated() -> None:
    assert hints_for("en") == ["en", "es"]

"""Ring 0 runs with dead provider keys: everything constructs, and a real call dies in seconds."""

import pytest
from hypothesis import settings as hypothesis_settings

from pinecall.providers.llm import VENDORS as LLM_VENDORS
from pinecall.providers.stt import VENDORS as STT_VENDORS
from pinecall.providers.tts import VENDORS as TTS_VENDORS
from pinecall.settings import Settings, load_settings

# Vendors reject these instantly with a 401, and the LiveKit URL points at a port nothing listens
# on. Structural, not disciplinary: a unit test cannot reach a real service by accident. The
# judging budget is one of them: at zero, ring 4 judges every call by code and asks nobody, so a
# hang-up in ring 0 opens no socket at all. docs/decisions/scoring.md.
DEAD_SENTINEL_KEYS: dict[str, str] = {
    "PINECALL_JUDGE_CEILING_EUR": "0",
    "LIVEKIT_URL": "ws://127.0.0.1:1",
    "LIVEKIT_API_KEY": "dead-sentinel",
    "LIVEKIT_API_SECRET": "dead-sentinel-dead-sentinel-dead-sentinel",
    "ANTHROPIC_API_KEY": "sk-ant-dead-sentinel",
    "OPENAI_API_KEY": "sk-dead-sentinel",
    "SONIOX_API_KEY": "dead-sentinel",
    "DEEPGRAM_API_KEY": "dead-sentinel",
    "ELEVEN_API_KEY": "dead-sentinel",
    "CARTESIA_API_KEY": "dead-sentinel",
    # The embedder's two, for the same reason: an operator who has a live Perplexity key exported
    # must not get a suite that reaches Perplexity, and EMBED_PROVIDER stays `tei` in ring 0.
    "PERPLEXITY_API_KEY": "pplx-dead-sentinel",
    "OPENROUTER_API_KEY": "sk-or-dead-sentinel",
}

# hypothesis searches forty inputs per property, not its hundred: a property here is one the
# examples beside it already state, and forty finds what a hundred would at less than half the
# time a shared runner spends on it. No deadline: a slow runner is not a failing property.
hypothesis_settings.register_profile("ring0", max_examples=40, deadline=None)
hypothesis_settings.load_profile("ring0")

# A search over inputs, or a walk over every module, is held to a minute and not to the ten
# seconds a single example gets (pyproject.toml): under coverage on a shared runner the ten were
# hit once (2026-09-26), and pytest-timeout's thread method takes the whole xdist worker with it.
SEARCH_S = 60

# The two marks that ask for the real world. Everything else, marked or not, gets the sentinels:
# the safe case is the default, so forgetting a mark costs a failure and never a bill.
MARKS_THAT_KEEP_THE_REAL_ENVIRONMENT = ("needs_llm", "voice")

# The Postgres fixtures are one module, shared by every package that has a table: log, auth, orgs,
# routes, tokens, evals. A conftest under one of them would be invisible to the others. The
# fixtures about people are the same shape of thing: one module, wanted by the api harness and by
# the CLI suites that drive it, and a conftest at the ceiling could not hold them.
pytest_plugins = [
    "tests.support.postgres",
    "tests.api.people",
    "tests.api.signing_in",
    "tests.api.mailing",
    "tests.api.carriers",
    "tests.api.policy",
    "tests.api.peering",
    "tests.api.models",
]


# Where every sandbox instance a test builds asks who a person is: a name nothing answers at, since
# a test that follows a person there answers for production itself (tests/api/).
THE_IDENTITY = "https://box.example.test"


def pytest_configure() -> None:
    """The suite reads no .env: an operator's real keys must not give them a different suite."""
    # pydantic-settings reads `env_file` off model_config at every construction, so clearing it
    # here — before collection, which already builds a Settings — turns the dotenv source off for
    # load_settings() and for a direct Settings() alike. The tests that are ABOUT the file ask for
    # it back, one at a time (tests/test_settings.py).
    Settings.model_config["env_file"] = None


def a_sandbox(settings: Settings | None = None, **changed: object) -> Settings:
    """The same settings as the sandbox's instance: its world, the fleet it dispatches to, and
    the production it asks who a person is."""
    return (settings or load_settings()).model_copy(
        update={
            "world": "sandbox",
            "fleet": "pinecall-sandbox",
            "identity_url": THE_IDENTITY,
            **changed,
        }
    )


# livekit registers a plugin the first time a modality's vendor table is read, and refuses to do
# it anywhere but the main thread (livekit/agents/plugin.py, Plugin.register_plugin). A TestClient
# serves its requests in a portal thread, so the first test that opens a door would be the one
# paying that import — and under a shuffled order that is whichever test ran first. Read all three
# tables here, once, where pytest itself is.
@pytest.fixture(scope="session", autouse=True)
def vendor_tables_read_on_the_main_thread() -> None:
    """Every vendor this build has, imported before any test can ask for one off the main thread."""
    for vendors in (LLM_VENDORS, STT_VENDORS, TTS_VENDORS):
        assert vendors.names


@pytest.fixture(autouse=True)
def dead_sentinel_keys(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> None:
    """Every test sees the sentinels unless it is marked needs_llm or voice."""
    if any(mark in request.keywords for mark in MARKS_THAT_KEEP_THE_REAL_ENVIRONMENT):
        return
    for name, value in DEAD_SENTINEL_KEYS.items():
        monkeypatch.setenv(name, value)


# Exporting a real key is the consent: the hook above closed the dotenv source, so a file on the
# developer's disk can no longer give it. Its absence is a skip, never a failure.
def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """A needs_llm test has nothing to talk to without a real key, so it is skipped, not failed."""
    if _a_real_anthropic_key_is_present():
        return
    skip_it = pytest.mark.skip(reason="needs a real ANTHROPIC_API_KEY exported, not in a .env")
    for item in items:
        if "needs_llm" in item.keywords:
            item.add_marker(skip_it)


def _a_real_anthropic_key_is_present() -> bool:
    """A sentinel is not a key: it is this file saying nobody may spend money in ring 0."""
    key = load_settings().anthropic_api_key
    return bool(key) and key != DEAD_SENTINEL_KEYS["ANTHROPIC_API_KEY"]

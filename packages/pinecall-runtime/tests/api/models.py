"""The model a test's gateway answers with: scripted, and every model and key it was asked for."""

import pytest

from pinecall.providers.models import Chat, Models
from pinecall.types import Brought, Model, ProviderKeys
from tests.session.fake_llm import FakeLLM


@pytest.fixture
def llm() -> FakeLLM:
    """The model this gateway answers with: scripted, so a unit test never reaches a vendor."""
    return FakeLLM()


@pytest.fixture
def models_asked() -> list[Model | None]:
    """Every model the gateway asked the provider table for: what a session was built with."""
    return []


@pytest.fixture
def keys_asked() -> list[ProviderKeys]:
    """Whose keys the gateway asked each model to be built with: empty is the box's own."""
    return []


@pytest.fixture
def llms(llm: FakeLLM, models_asked: list[Model | None], keys_asked: list[ProviderKeys]) -> Models:
    """The provider table: what the agent declared is remembered, and the scripted model answers."""

    def ask(declared: Model | None, brought: Brought) -> Chat:
        models_asked.append(declared)
        keys_asked.append(brought.keys)
        return llm

    return ask

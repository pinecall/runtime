"""The doctor's first line: which keys open this gateway, and the combination that is a mistake."""

import pytest

from pinecall._settings import load_settings
from pinecall.cli.doctor import verbs as doctor
from tests.cli.doctor.reading import named, probes_that_answer

pytestmark = pytest.mark.unit


# The one combination nothing else here would notice: a dev key opens no database at all, so a box
# that sets one answers every call as org `default` and every tenant it has is invisible.
def test_a_dev_key_on_a_laptop_is_advice_and_says_what_it_costs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PINECALL_DEV_KEY", "pk_a_laptops_own")
    monkeypatch.delenv("PINECALL_OPS_KEY", raising=False)
    monkeypatch.setenv("PINECALL_ROLE", "all")

    results = doctor.run_checks(load_settings(), probes_that_answer())

    keys = named("api keys", results)
    assert not keys.ok
    assert keys.advisory
    assert "knowledge, memory and the vault answer 503" in keys.detail
    assert doctor.first_failure(results) is None


def test_a_dev_key_on_a_box_is_the_first_thing_down(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PINECALL_DEV_KEY", "pk_a_laptops_own")
    monkeypatch.setenv("PINECALL_ROLE", "hub")

    results = doctor.run_checks(load_settings(), probes_that_answer())

    down = doctor.first_failure(results)
    assert down is not None
    assert down.name == "api keys"
    assert "every tenant is invisible" in down.detail


def test_a_gateway_on_the_table_says_which_verb_mints_a_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("PINECALL_DEV_KEY", raising=False)

    keys = named("api keys", doctor.run_checks(load_settings(), probes_that_answer()))

    assert keys.ok
    assert "keys issue" in keys.detail

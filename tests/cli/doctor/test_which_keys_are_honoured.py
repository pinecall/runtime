"""The doctor's first line: which keys open this gateway. There is one answer, and one table."""

import pytest

from pinecall._settings import load_settings
from pinecall.cli.doctor import verbs as doctor
from tests.cli.doctor.reading import named, probes_that_answer

pytestmark = pytest.mark.unit


# This line used to have two answers, and the second was a trap: PINECALL_DEV_KEY opened no
# database at all, so a box that set one answered every call as org `default` with every tenant
# invisible — not a leak, a silence, and a silence nothing else here would have noticed. The
# variable is gone and so is the trap; what is left is the verb that puts a key in the table.
def test_a_gateway_says_which_verb_mints_a_key_and_nothing_else_opens_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PINECALL_DEV_KEY", "pk_a_variable_nothing_reads_any_more")

    results = doctor.run_checks(load_settings(), probes_that_answer())

    keys = named("api keys", results)
    assert keys.ok
    assert "keys issue" in keys.detail
    assert doctor.first_failure(results) is None

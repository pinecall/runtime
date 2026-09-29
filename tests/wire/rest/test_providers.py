"""Tests for the bodies of the providers doors."""

from pinecall.wire.rest.providers import ProviderKeyRequest, ProviderRow


def test_a_vendor_row_says_what_it_does_and_whose_key_runs_it() -> None:
    row = ProviderRow(
        name="acme",
        does=["tts"],
        aliases=[],
        note="",
        standing="ready",
        ready=True,
        env=None,
        extra="acme",
        voices_listed=False,
        availability="offered",
        broken=None,
    )
    assert (row.written()["availability"], row.written()["standing"]) == ("offered", "ready")


def test_a_key_request_carries_one_key_or_the_credentials_object() -> None:
    assert ProviderKeyRequest(key="k").credentials is None
    assert ProviderKeyRequest(credentials={"speech_key": "k"}).key is None

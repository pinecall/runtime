"""The SFU is one reading of three settings: a pair present is a client, a pair missing is None."""

import pytest

from pinecall._settings import load_settings
from pinecall.routes.dispatch import dispatches_for
from pinecall.routes.inbound_trunks import trunks_for
from pinecall.routes.live_rooms import rooms_for
from pinecall.routes.outbound_trunks import outbound_for
from pinecall.routes.sfu import Sfu

pytestmark = pytest.mark.unit


def test_a_box_with_the_pair_has_an_sfu_at_its_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LIVEKIT_URL", "ws://sfu.test:7880")
    monkeypatch.setenv("LIVEKIT_API_KEY", "devkey")
    monkeypatch.setenv("LIVEKIT_API_SECRET", "s" * 32)
    sfu = Sfu.of(load_settings())
    assert sfu == Sfu("ws://sfu.test:7880", "devkey", "s" * 32)


def test_a_box_with_no_pair_has_no_sfu_and_none_of_its_four_tables(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("LIVEKIT_API_KEY", raising=False)
    monkeypatch.delenv("LIVEKIT_API_SECRET", raising=False)
    settings = load_settings()
    assert Sfu.of(settings) is None
    assert (trunks_for(settings), outbound_for(settings)) == (None, None)
    assert (dispatches_for(settings), rooms_for(settings)) == (None, None)

"""The signature is the whole of this door's authentication: what it takes and what it refuses."""

from __future__ import annotations

import pytest

from pinecall.whatsapp.webhook_signature import is_signed
from tests.whatsapp.bodies import AN_APP_SECRET, SIGNATURE_HEADER, a_signature

pytestmark = pytest.mark.unit

A_BODY = b'{"object":"whatsapp_business_account","entry":[]}'


def test_the_body_meta_signed_is_taken() -> None:
    header = a_signature(A_BODY)[SIGNATURE_HEADER]
    assert is_signed(AN_APP_SECRET, A_BODY, header) is True


def test_no_header_at_all_is_refused() -> None:
    assert is_signed(AN_APP_SECRET, A_BODY, None) is False


def test_a_header_without_the_prefix_is_refused() -> None:
    """Meta always sends `sha256=`: a bare digest is somebody else's idea of the protocol."""
    bare = a_signature(A_BODY)[SIGNATURE_HEADER].removeprefix("sha256=")
    assert is_signed(AN_APP_SECRET, A_BODY, bare) is False


def test_a_body_signed_with_another_secret_is_refused() -> None:
    header = a_signature(A_BODY, secret="somebody-elses-app")[SIGNATURE_HEADER]
    assert is_signed(AN_APP_SECRET, A_BODY, header) is False


def test_a_body_changed_after_it_was_signed_is_refused() -> None:
    """The reason the raw bytes are hashed: one byte more and the digest is a different number."""
    header = a_signature(A_BODY)[SIGNATURE_HEADER]
    assert is_signed(AN_APP_SECRET, A_BODY + b" ", header) is False

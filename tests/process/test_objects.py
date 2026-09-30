"""Tests for the object store: AWS's version 4 signature, and a store named whole or not at all."""

from collections.abc import AsyncIterator
from datetime import UTC, datetime

import httpx
import pytest

from pinecall.process._objects import NO_BODY, ObjectStore, Signing, object_store_of, signature
from pinecall.process.settings import Settings
from tests.fakes import bucket as fake


@pytest.fixture
async def http() -> AsyncIterator[httpx.AsyncClient]:
    async with httpx.AsyncClient(transport=fake.Bucket().transport()) as client:
        yield client


def test_the_signature_is_the_one_aws_documents_for_its_own_example() -> None:
    signing = Signing("us-east-1", fake.AWS_EXAMPLE_KEY_ID, fake.AWS_EXAMPLE_SECRET)
    headers = signature(
        "GET",
        "https://examplebucket.s3.amazonaws.com/test.txt",
        {"Range": "bytes=0-9"},
        signing,
        (NO_BODY, datetime(2013, 5, 24, tzinfo=UTC)),
    )
    scope = "20130524/us-east-1/s3/aws4_request"
    assert headers["authorization"] == (
        f"AWS4-HMAC-SHA256 Credential={fake.AWS_EXAMPLE_KEY_ID}/{scope}, "
        f"SignedHeaders=host;range;x-amz-content-sha256;x-amz-date, "
        f"Signature={fake.AWS_EXAMPLE_SIGNATURE}"
    )
    assert headers["x-amz-date"] == "20130524T000000Z"


def test_an_object_is_addressed_by_path_with_its_name_encoded(http: httpx.AsyncClient) -> None:
    store = fake.Bucket().store_on(http)
    assert store.url_of("b", "org_1/CA 1/audio.ogg") == f"{fake.ENDPOINT}/b/org_1/CA%201/audio.ogg"


async def test_a_signed_request_is_taken_and_one_signed_with_another_secret_is_refused(
    http: httpx.AsyncClient,
) -> None:
    good = fake.Bucket().store_on(http)
    wrong = ObjectStore(
        fake.ENDPOINT, Signing(fake.REGION, fake.ACCESS_KEY_ID, "not-the-secret"), http
    )
    for store, status in ((good, 404), (wrong, 403)):
        url = store.url_of("pinecall-recordings", "org_1/CA_1/audio.ogg")
        answer = await http.get(url, headers=store.signed("GET", url, {"range": "bytes=0-9"}))
        assert answer.status_code == status


def test_a_store_is_its_four_settings_or_none(http: httpx.AsyncClient) -> None:
    whole = Settings.model_validate(fake.STORE_SETTINGS)
    assert object_store_of(whole, http) == fake.Bucket().store_on(http)
    half = {name: value for name, value in fake.STORE_SETTINGS.items() if "SECRET" not in name}
    assert object_store_of(Settings.model_validate(half), http) is None

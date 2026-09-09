"""A box's secrets, made once: every name, no value on any screen, and a second run keeps them."""

from io import StringIO
from pathlib import Path

import pytest

from pinecall.cli.box.verbs import KEPT, MADE, generated, keep_secret, make_secrets

pytestmark = pytest.mark.unit

# What a fresh box makes for itself, by name, in the order the verb makes them.
THE_SEVEN = [
    "LIVEKIT_API_KEY",
    "LIVEKIT_API_SECRET",
    "POSTGRES_PASSWORD",
    "DATABASE_URL",
    "PINECALL_OPS_KEY",
    "PINECALL_VAULT_KEY",
    "media.env",
]


# systemd-creds is stood in for by the one thing this suite may check about it: that it was
# handed the name, the value on stdin, and the file to write — named as the credential, no more.
def a_recording_encrypt(written: dict[str, str]):  # noqa: ANN201 — a test double
    def encrypt(name: str, value: str, into: Path) -> None:
        written[name] = value
        (into / name).write_text("ciphertext")

    return encrypt


def test_a_fresh_box_makes_the_seven_and_names_them_without_a_value(tmp_path: Path) -> None:
    written: dict[str, str] = {}
    said = StringIO()

    assert make_secrets(tmp_path, said, a_recording_encrypt(written)) == 0

    assert list(written) == THE_SEVEN
    assert said.getvalue().splitlines() == [MADE.format(name=name) for name in THE_SEVEN]
    for value in written.values():
        assert value not in said.getvalue(), "a secret reached the screen"


def test_a_second_run_keeps_every_secret_and_rotates_nothing(tmp_path: Path) -> None:
    first: dict[str, str] = {}
    make_secrets(tmp_path, StringIO(), a_recording_encrypt(first))
    second: dict[str, str] = {}
    said = StringIO()

    assert make_secrets(tmp_path, said, a_recording_encrypt(second)) == 0

    assert second == {}
    assert said.getvalue().splitlines() == [KEPT.format(name=name) for name in THE_SEVEN]


def test_the_derived_values_are_derived_from_the_same_draw() -> None:
    values = generated()
    assert values["POSTGRES_PASSWORD"] in values["DATABASE_URL"]
    assert values["DATABASE_URL"].startswith("postgresql://pinecall:")
    assert values["media.env"].splitlines() == [
        f"LIVEKIT_API_KEY={values['LIVEKIT_API_KEY']}",
        f"LIVEKIT_API_SECRET={values['LIVEKIT_API_SECRET']}",
        f"LIVEKIT_KEYS={values['LIVEKIT_API_KEY']}: {values['LIVEKIT_API_SECRET']}",
        f"POSTGRES_PASSWORD={values['POSTGRES_PASSWORD']}",
    ]


def test_the_vault_key_is_a_fernet_key() -> None:
    from cryptography.fernet import Fernet

    Fernet(generated()["PINECALL_VAULT_KEY"].encode("ascii"))


def test_two_draws_share_nothing() -> None:
    one, two = generated(), generated()
    assert all(one[name] != two[name] for name in THE_SEVEN)


def test_a_brought_secret_is_kept_under_its_name_and_an_empty_one_is_refused(
    tmp_path: Path,
) -> None:
    written: dict[str, str] = {}
    said = StringIO()

    assert (
        keep_secret(
            "ANTHROPIC_API_KEY", "sk-ant-something", tmp_path, said, a_recording_encrypt(written)
        )
        == 0
    )
    assert written == {"ANTHROPIC_API_KEY": "sk-ant-something"}
    assert said.getvalue() == MADE.format(name="ANTHROPIC_API_KEY") + "\n"

    assert (
        keep_secret("ANTHROPIC_API_KEY", "", tmp_path, StringIO(), a_recording_encrypt(written))
        == 2
    )

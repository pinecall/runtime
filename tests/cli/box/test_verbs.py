"""A box's secrets, made once: every name, no value on any screen, and a second run keeps them."""

from io import StringIO
from pathlib import Path

import pytest

from pinecall.cli import build_parser
from pinecall.cli.box.instance import InstanceRefused
from pinecall.cli.box.verbs import (
    KEPT,
    MADE,
    THE_BOXS_OWN,
    generated,
    instance_secrets,
    keep_secret,
    make_secrets,
    run_secrets,
)

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


# ── an instance's own ────────────────────────────────────────────────────────────

INSTANCES_OWN = ["DATABASE_URL", "PINECALL_OPS_KEY", "PINECALL_VAULT_KEY"]


def test_an_instance_draws_its_three_on_a_role_of_its_own_and_nothing_of_the_boxs() -> None:
    drawn = instance_secrets("staging-eu")
    assert list(drawn) == INSTANCES_OWN
    assert drawn["DATABASE_URL"].startswith("postgresql://pinecall_staging_eu:")
    assert drawn["DATABASE_URL"].endswith("@127.0.0.1:5432/pinecall_staging_eu")
    assert drawn["PINECALL_OPS_KEY"] != generated()["PINECALL_OPS_KEY"]


def test_an_instances_store_is_made_once_and_kept_after(tmp_path: Path) -> None:
    first: dict[str, str] = {}
    assert (
        make_secrets(tmp_path, StringIO(), a_recording_encrypt(first), instance_secrets("a")) == 0
    )
    assert list(first) == INSTANCES_OWN
    again: dict[str, str] = {}
    said = StringIO()
    make_secrets(tmp_path, said, a_recording_encrypt(again), instance_secrets("a"))
    assert again == {}
    assert said.getvalue().splitlines() == [KEPT.format(name=name) for name in INSTANCES_OWN]


def test_productions_are_never_drawn_they_are_the_boxs_own(tmp_path: Path) -> None:
    arguments = build_parser().parse_args(
        ["box", "secrets", "--instance", "production", "--into", str(tmp_path)]
    )
    with pytest.raises(InstanceRefused) as refused:
        run_secrets(arguments)
    assert str(refused.value) == THE_BOXS_OWN.format(store=tmp_path)
    assert list(tmp_path.iterdir()) == []


def test_an_instance_that_is_not_a_name_gets_no_store(tmp_path: Path) -> None:
    arguments = build_parser().parse_args(
        ["box", "secrets", "--instance", "../etc", "--into", str(tmp_path)]
    )
    with pytest.raises(InstanceRefused):
        run_secrets(arguments)

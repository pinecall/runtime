"""`box peer`: a fleet key of one instance, minted at its gateway, kept in the other's store."""

from io import StringIO
from pathlib import Path

import httpx
import pytest

from pinecall.cli import build_parser
from pinecall.cli.box.instance import credstore_of, declared, write_instance
from pinecall.cli.box.peer import (
    ALREADY,
    ONE_INSTANCE,
    PEER_KEY,
    SANDBOX_KEY,
    SCOPES,
    Hands,
    PeerRefused,
    Side,
    every_pair,
    minted_at,
    pairs_among,
    peer,
)
from pinecall.types import PRODUCTION, SANDBOX, THE_FLEET

pytestmark = pytest.mark.unit

BOX = "https://box.example.com"
SANDBOX_URL = "https://sandbox.example.com"


def a_box(instances: Path, *, names_its_sandbox: bool = True) -> Path:
    """Production and its sandbox on one box, each an env file as `box instance` writes it."""
    for instance in (
        declared(
            "production",
            PRODUCTION,
            "box.example.com",
            instances,
            elsewhere=SANDBOX_URL,
            sandbox=SANDBOX_URL if names_its_sandbox else None,
        ),
        declared("sandbox", SANDBOX, "sandbox.example.com", instances, identity=BOX, elsewhere=BOX),
    ):
        write_instance(instance, instances, out=StringIO(), keep_recordings=lambda _path: None)
    return instances


class Hand:
    """The verb's hands, recorded: what it minted where, and what it kept under which name."""

    def __init__(self, *, down: frozenset[str] = frozenset()) -> None:
        self.minted: list[tuple[str, str]] = []
        self.kept: dict[Path, str] = {}
        self._down = down

    async def mint(self, side: Side, into: str, decrypt: object) -> str:  # noqa: ARG002
        if side.name in self._down:
            raise httpx.ConnectError(f"{side.name}'s gateway is down")
        self.minted.append((side.name, into))
        return f"key-of-{side.name}-for-{into}"

    def encrypt(self, name: str, value: str, into: Path) -> None:
        into.mkdir(parents=True, exist_ok=True)
        (into / name).write_text("sealed")
        self.kept[into / name] = value

    @property
    def hands(self) -> Hands:
        return Hands(mint=self.mint, decrypt=lambda _name, _path: "", encrypt=self.encrypt)


# What a key is kept under says what it opens, read off the instance it is minted AT.
async def test_a_key_of_the_sandbox_is_kept_as_productions_sandbox_key(tmp_path: Path) -> None:
    hand = Hand()
    await peer("sandbox", "production", a_box(tmp_path), out=StringIO(), hands=hand.hands)
    assert hand.kept == {
        credstore_of("production", tmp_path) / SANDBOX_KEY: "key-of-sandbox-for-production"
    }


async def test_a_key_of_production_is_kept_as_the_sandboxs_peer_key(tmp_path: Path) -> None:
    hand = Hand()
    await peer("production", "sandbox", a_box(tmp_path), out=StringIO(), hands=hand.hands)
    assert list(hand.kept) == [credstore_of("sandbox", tmp_path) / PEER_KEY]


async def test_a_key_that_is_kept_is_never_minted_again_unasked(tmp_path: Path) -> None:
    hand = Hand()
    await peer("sandbox", "production", a_box(tmp_path), out=StringIO(), hands=hand.hands)
    with pytest.raises(PeerRefused) as refused:
        await peer("sandbox", "production", tmp_path, out=StringIO(), hands=hand.hands)
    assert str(refused.value) == ALREADY.format(
        path=credstore_of("production", tmp_path) / SANDBOX_KEY, source="sandbox"
    )
    await peer("sandbox", "production", tmp_path, force=True, out=StringIO(), hands=hand.hands)
    assert len(hand.minted) == 2


async def test_an_instance_is_not_its_own_peer(tmp_path: Path) -> None:
    with pytest.raises(PeerRefused, match=ONE_INSTANCE):
        await peer("sandbox", "sandbox", a_box(tmp_path), out=StringIO(), hands=Hand().hands)


def test_a_production_naming_a_sandbox_on_this_box_is_a_pair_both_ways(tmp_path: Path) -> None:
    assert pairs_among(["production", "sandbox"], a_box(tmp_path)) == [
        ("sandbox", "production"),
        ("production", "sandbox"),
    ]


def test_a_production_naming_no_sandbox_or_one_on_another_box_is_no_pair(tmp_path: Path) -> None:
    unnamed = a_box(tmp_path / "unnamed", names_its_sandbox=False)
    assert pairs_among(["production", "sandbox"], unnamed) == []
    assert pairs_among(["production"], a_box(tmp_path / "elsewhere")) == []


# What `make converge` runs every deploy: what is missing is minted, what is kept stays, and a
# gateway that is not up yet is said and skipped rather than failing the deploy.
async def test_every_pair_mints_what_is_missing_keeps_what_is_there_and_skips_a_gateway_down(
    tmp_path: Path,
) -> None:
    a_box(tmp_path)
    first = Hand(down=frozenset({"production"}))
    said = StringIO()
    await every_pair(["production", "sandbox"], tmp_path, out=said, hands=first.hands)
    assert first.minted == [("sandbox", "production")]
    assert "skipped PINECALL_PEER_KEY for sandbox: production's gateway did not answer" in (
        said.getvalue()
    )

    second = Hand()
    await every_pair(["production", "sandbox"], tmp_path, out=StringIO(), hands=second.hands)
    assert second.minted == [("production", "sandbox")]


# At the instance's own gateway, on its own ops key — which mints in its own world, the only one
# that honours it — with the scopes of the two doors a peer asks and nothing else.
async def test_the_key_is_minted_at_its_instances_gateway_on_its_ops_key(tmp_path: Path) -> None:
    asked: list[httpx.Request] = []

    def the_gateway(request: httpx.Request) -> httpx.Response:
        asked.append(request)
        return httpx.Response(200, json={"key": "pk_test_minted"})

    side = Side("sandbox", SANDBOX, "http://127.0.0.1:8180", None, None, tmp_path)
    opened: list[Path] = []

    def the_ops_key(name: str, path: Path) -> str:
        opened.append(path)
        return "the-ops-key" if name == "PINECALL_OPS_KEY" else ""

    transport = httpx.MockTransport(the_gateway)
    key = await minted_at(side, "production", the_ops_key, transport)

    assert opened == [tmp_path / "PINECALL_OPS_KEY"]

    assert key == "pk_test_minted"
    (request,) = asked
    assert str(request.url) == "http://127.0.0.1:8180/v1/ops/orgs/default/keys"
    assert request.headers["Authorization"] == "Bearer the-ops-key"
    assert httpx.Response(200, content=request.content).json() == {
        "label": "peer-for-production",
        "scopes": [THE_FLEET, "app"],
    }
    assert SCOPES == (THE_FLEET, "app")


def test_the_verb_is_one_pair_named_or_every_pair_listed() -> None:
    parsed = build_parser().parse_args(["box", "peer", "--from", "sandbox", "--into", "production"])
    assert (parsed.source, parsed.into) == ("sandbox", "production")
    listed = build_parser().parse_args(["box", "peer", "--among", "production", "sandbox"])
    assert listed.among == ["production", "sandbox"]

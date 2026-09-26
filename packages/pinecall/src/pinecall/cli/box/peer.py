"""`pinecall-runtime box peer`: a fleet key minted at one instance, kept in another's credstore."""

import argparse
import asyncio
import sys
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TextIO
from urllib.parse import urlsplit

import httpx

from pinecall.cli.box.credentials import (
    Decrypt,
    Encrypt,
    decrypt_with_systemd,
    encrypt_with_systemd,
)
from pinecall.cli.box.instance import (
    INSTANCES,
    check_instance_name,
    credstore_of,
    env_file,
    said_in,
)
from pinecall.cli.keys.verbs import OPS_ORGS
from pinecall.cli.operator import TIMEOUT_S, Operator, OperatorRefused
from pinecall.errors import PinecallError
from pinecall.settings import variable_of
from pinecall.types import DEFAULT_ORG, PRODUCTION, SANDBOX, THE_FLEET, Env, parse_env

# Two instances trust each other the way a worker box trusts its hub: with one fleet key each,
# minted at the other's gateway and kept in this one's store, where its gateway loads it. What a
# key is kept under says what it opens: a key of a sandbox is production's PINECALL_SANDBOX_KEY,
# a key of production is the sandbox's PINECALL_PEER_KEY. So the name is read off the instance the
# key is minted AT — the one side a pair on two boxes always has on the box it is minted on.
SANDBOX_KEY = variable_of("sandbox_key")
PEER_KEY = variable_of("peer_key")
# Every name a peer key is kept under: what the manifest writes a gateway's drop-in from (its
# PEER_SECRETS, which a test holds equal to this).
PEER_SECRETS = (SANDBOX_KEY, PEER_KEY)
OPS_KEY = variable_of("ops_key")

# `fleet`, because the doors a peer asks name the org they ask for, which only a fleet key may;
# `app`, because both of them — `GET /v1/agents/{slug}/rings-for` and `GET /v1/routes` — are app
# doors. Nothing else: a peer registers no agent and reads no call.
SCOPES = (THE_FLEET, "app")
LABEL = "peer-for-{into}"

NO_SUCH_INSTANCE = "no instance {name} on this box: {path} is missing"
ONE_INSTANCE = "an instance is not its own peer: --from and --into name the same one"
ALREADY = (
    "{path} is already there: --force mints another (the old key stays live at {source} until "
    "`pinecall-runtime keys revoke`)"
)
KEPT = "kept {name} in {store}: a fleet key of {source}, for {into}"
THERE = "{into} already holds {name}: nothing minted"
NOT_ANSWERING = "skipped {name} for {into}: {source}'s gateway did not answer ({said})"


class PeerRefused(PinecallError):
    """A peer key this verb will not mint or keep: an instance missing, or one already kept."""


@dataclass(frozen=True)
class Side:
    """One instance as its env file says it: where its gateway answers, and in which world."""

    name: str
    world: Env
    gateway_url: str
    domain: str | None
    sandbox_url: str | None
    store: Path

    @property
    def kept_as(self) -> str:
        """The credential a key minted HERE is kept under, in the other instance's store."""
        return SANDBOX_KEY if self.world == SANDBOX else PEER_KEY


def side_of(name: str, instances: Path = INSTANCES) -> Side:
    """The instance's file, read: the refusal when this box holds no such instance."""
    path = env_file(check_instance_name(name), instances)
    if not path.exists():
        raise PeerRefused(NO_SUCH_INSTANCE.format(name=name, path=path))
    return Side(
        name=name,
        world=parse_env(said_in(path, "world") or PRODUCTION),
        gateway_url=said_in(path, "gateway_url") or "",
        domain=said_in(path, "domain"),
        sandbox_url=said_in(path, "sandbox_url"),
        store=credstore_of(name, instances),
    )


type Mint = Callable[[Side, str, Decrypt], Awaitable[str]]


# At the instance's own gateway, on its own ops key, as pinecall-worker-key@ mints the worker's —
# and so in that instance's world, which is the one the door mints in (api/ops/orgs.py) and the only
# one whose doors honour it (auth/env.py). The key is read off the answer and never printed.
async def minted_at(
    side: Side, into: str, decrypt: Decrypt, transport: httpx.AsyncBaseTransport | None = None
) -> str:
    """A fleet key of this instance, labelled for the one that will hold it."""
    ops_key = decrypt(OPS_KEY, side.store / OPS_KEY)
    http = httpx.AsyncClient(
        base_url=side.gateway_url,
        headers={"Authorization": f"Bearer {ops_key}"},
        timeout=TIMEOUT_S,
        transport=transport,
    )
    async with Operator(http) as operator:
        said = {"label": LABEL.format(into=into), "scopes": list(SCOPES)}
        answer = await operator.post(f"{OPS_ORGS}/{DEFAULT_ORG}/keys", said)
    return str(answer["key"])


@dataclass(frozen=True)
class Hands:
    """What the verb touches besides files: the minting door and the two ends of systemd-creds."""

    mint: Mint = minted_at
    decrypt: Decrypt = decrypt_with_systemd
    encrypt: Encrypt = encrypt_with_systemd


async def peer(
    source: str,
    into: str,
    instances: Path = INSTANCES,
    *,
    force: bool = False,
    out: TextIO = sys.stdout,
    hands: Hands | None = None,
) -> int:
    """Mint at `source`, keep in `into`'s store under the name that says what it opens."""
    using = hands or Hands()
    if check_instance_name(source) == check_instance_name(into):
        raise PeerRefused(ONE_INSTANCE)
    side = side_of(source, instances)
    store = credstore_of(into, instances)
    if (store / side.kept_as).exists() and not force:
        raise PeerRefused(ALREADY.format(path=store / side.kept_as, source=source))
    key = await using.mint(side, into, using.decrypt)
    using.encrypt(side.kept_as, key, store)
    print(KEPT.format(name=side.kept_as, store=store, source=source, into=into), file=out)
    return 0


def pairs_among(listed: Sequence[str], instances: Path = INSTANCES) -> list[tuple[str, str]]:
    """Every (from, into) this box can mint for itself: a production naming its sandbox by the
    URL of an instance listed here, both ways. A sandbox on another box is not a pair here."""
    sides = [side_of(name, instances) for name in listed]
    found: list[tuple[str, str]] = []
    for production in sides:
        if production.world != PRODUCTION or not production.sandbox_url:
            continue
        host = urlsplit(production.sandbox_url).hostname
        for sandbox in sides:
            if sandbox.world == SANDBOX and sandbox.domain == host:
                found += [(sandbox.name, production.name), (production.name, sandbox.name)]
    return found


# What `make converge` runs, once per deploy: what is missing is minted, what is kept stays. A
# gateway not answering yet — the deploy that first brings an instance up — is said and skipped,
# and the manifest's own check refuses to start a production that names a sandbox it has no key of.
async def every_pair(
    listed: Sequence[str],
    instances: Path = INSTANCES,
    *,
    out: TextIO = sys.stdout,
    hands: Hands | None = None,
) -> int:
    """Each pair among these instances made whole where it is not; never a key rotated."""
    for source, into in pairs_among(listed, instances):
        name = side_of(source, instances).kept_as
        if (credstore_of(into, instances) / name).exists():
            print(THERE.format(into=into, name=name), file=out)
            continue
        try:
            await peer(source, into, instances, out=out, hands=hands)
        except (OperatorRefused, httpx.HTTPError) as failed:
            print(NOT_ANSWERING.format(name=name, into=into, source=source, said=failed), file=out)
    return 0


def configure(parser: argparse.ArgumentParser) -> None:
    """`box peer --from <instance> --into <instance>`, or `--among <every instance listed>`."""
    parser.add_argument("--from", dest="source", help="the instance the key is minted at")
    parser.add_argument("--into", help="the instance whose store keeps it")
    parser.add_argument("--among", nargs="+", help="every pair among these, minted where missing")
    parser.add_argument("--force", action="store_true", help="mint again over the key kept")
    parser.add_argument("--instances", type=Path, default=INSTANCES, help=f"default {INSTANCES}")
    parser.set_defaults(run=run_peer)


WHICH = "which? box peer --from sandbox --into production, or box peer --among production sandbox"


def run_peer(arguments: argparse.Namespace) -> int:
    """The one pair named, or every pair among the instances listed."""
    if arguments.among:
        return asyncio.run(every_pair(arguments.among, arguments.instances))
    if not arguments.source or not arguments.into:
        raise PeerRefused(WHICH)
    return asyncio.run(
        peer(arguments.source, arguments.into, arguments.instances, force=arguments.force)
    )

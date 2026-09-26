"""`pinecall-runtime box`: a box's secrets, its instances, their databases, and their peer keys."""

import argparse
import base64
import secrets
import sys
from pathlib import Path
from typing import TextIO

from pinecall.cli.box import database, instance, peer
from pinecall.cli.box.credentials import Encrypt, encrypt_with_systemd
from pinecall.cli.box.instance import THE_FIRST, InstanceRefused, a_name, credstore_of, database_of
from pinecall.cli.help import only_the_help

PURPOSE: str = "the box: its instances, and their secrets as encrypted systemd credentials"
VERBS: tuple[str, ...] = ("secrets", "secret", "instance", "database", "peer")

# Where `ImportCredential=` looks, and the one place a box's secrets live: one file per name,
# encrypted under the machine's own key (and its TPM, when it has one), decrypted by systemd into
# the private credentials directory of the unit that asked. Never a .env file, never a dump.
CREDSTORE = Path("/etc/credstore.encrypted")

MADE = "made {name}"
KEPT = "kept {name} — it was already there, and this verb never rewrites a secret"
# Production's database is the container's own, whose password is the box's (media.env): a second
# draw for it would be a DATABASE_URL that opens nothing. Its three are the box's first ones, which
# `make install` copies into its store — so its store is never drawn here.
THE_BOXS_OWN = (
    "production's secrets are the box's own first draw (`box secrets`), copied into "
    "{store} by `make install`: this verb draws them for another instance"
)


def configure(parser: argparse.ArgumentParser) -> None:
    """`secrets` makes what a box generates for itself; `secret <name>` keeps one you bring."""
    verbs = parser.add_subparsers(dest="verb", metavar="verb")
    making = verbs.add_parser("secrets", help="generate every secret a box makes for itself, once")
    making.add_argument(
        "--into", type=Path, help=f"the credstore (default {CREDSTORE}, or the instance's own)"
    )
    making.add_argument("--instance", help="an instance's own secrets instead of the box's")
    making.set_defaults(run=run_secrets)
    keeping = verbs.add_parser(
        "secret", help="keep one secret you bring, read from stdin: a provider key"
    )
    keeping.add_argument(
        "name", help="the credential's name, which is the variable's: ANTHROPIC_API_KEY"
    )
    keeping.add_argument(
        "--into", type=Path, default=CREDSTORE, help=f"the credstore (default {CREDSTORE})"
    )
    keeping.add_argument("--instance", help="into that instance's own store instead of the box's")
    keeping.set_defaults(run=run_secret)
    instance.configure(
        verbs.add_parser("instance", help="declare one instance of the runtime on this box")
    )
    database.configure(
        verbs.add_parser("database", help="make this instance's database, if it is missing")
    )
    peer.configure(
        verbs.add_parser("peer", help="a fleet key of one instance, kept in another's store")
    )
    parser.set_defaults(run=only_the_help(parser))


def run_secrets(arguments: argparse.Namespace) -> int:
    """Every secret a box, or one instance, makes for itself, made once. Only names are printed."""
    if arguments.instance is None:
        return make_secrets(arguments.into or CREDSTORE)
    name = a_name(arguments.instance)
    into = arguments.into or credstore_of(name)
    if name == THE_FIRST:
        raise InstanceRefused(THE_BOXS_OWN.format(store=into))
    return make_secrets(into, drawn=instance_secrets(name))


def run_secret(arguments: argparse.Namespace) -> int:
    """One secret a person brings, from stdin: never from argv, where a `ps` would read it."""
    into = credstore_of(a_name(arguments.instance)) if arguments.instance else arguments.into
    return keep_secret(arguments.name, sys.stdin.read().strip(), into)


# The database and role postgres.container makes at its first boot, which the first instance uses.
THE_FIRST_DATABASE = "pinecall"


# The LiveKit keypair, the database password and the two keys the runtime guards other things
# with. Everything a person brings — the vendors' keys, WhatsApp's — is `secret <name>` instead:
# a box generates what only it will ever hold, and never invents what somebody else issued.
def generated() -> dict[str, str]:
    """The seven values a fresh box is made of, each drawn once from the CSPRNG."""
    livekit_key = f"API{secrets.token_hex(6)}"
    livekit_secret = secrets.token_hex(32)
    password = secrets.token_hex(16)
    return {
        "LIVEKIT_API_KEY": livekit_key,
        "LIVEKIT_API_SECRET": livekit_secret,
        "POSTGRES_PASSWORD": password,
        # The first instance's three, on the container's own superuser: `make install` copies them
        # into production's store, where its units read them.
        **_guarding(THE_FIRST_DATABASE, password),
        # What the media plane's three containers read as an environment file: livekit-server
        # takes the pair as one `key: secret` line, livekit-sip as two variables, postgres as one.
        "media.env": "\n".join(
            [
                f"LIVEKIT_API_KEY={livekit_key}",
                f"LIVEKIT_API_SECRET={livekit_secret}",
                f"LIVEKIT_KEYS={livekit_key}: {livekit_secret}",
                f"POSTGRES_PASSWORD={password}",
            ]
        )
        + "\n",
    }


# No LiveKit pair and no media.env — the media plane is the box's, shared — and no worker key,
# which pinecall-worker-key@<name> mints from its gateway once it answers.
def instance_secrets(name: str) -> dict[str, str]:
    """What another instance holds alone: its DSN, on a role of its own, and its two guard keys."""
    return _guarding(database_of(name), secrets.token_hex(16))


def _guarding(database: str, password: str) -> dict[str, str]:
    """DATABASE_URL on a role named as its database, then the ops key and the vault key."""
    return {
        "DATABASE_URL": f"postgresql://{database}:{password}@127.0.0.1:5432/{database}",
        "PINECALL_OPS_KEY": secrets.token_hex(32),
        # Fernet: 32 bytes, urlsafe base64 — what `cryptography` will accept and nothing else.
        "PINECALL_VAULT_KEY": base64.urlsafe_b64encode(secrets.token_bytes(32)).decode("ascii"),
    }


def make_secrets(
    into: Path,
    out: TextIO = sys.stdout,
    encrypt: Encrypt | None = None,
    drawn: dict[str, str] | None = None,
) -> int:
    """Make what is missing and keep what is there: this verb run twice rotates nothing."""
    writing = encrypt or encrypt_with_systemd
    for name, value in (drawn or generated()).items():
        if (into / name).exists():
            print(KEPT.format(name=name), file=out)
            continue
        writing(name, value, into)
        print(MADE.format(name=name), file=out)
    return 0


def keep_secret(
    name: str, value: str, into: Path, out: TextIO = sys.stdout, encrypt: Encrypt | None = None
) -> int:
    """One brought secret, kept under its name. A second `secret` of the same name replaces it."""
    if not value:
        print(
            f"nothing on stdin: `printf '%s' <the key> | pinecall-runtime box secret {name}`",
            file=sys.stderr,
        )
        return 2
    (encrypt or encrypt_with_systemd)(name, value, into)
    print(MADE.format(name=name), file=out)
    return 0

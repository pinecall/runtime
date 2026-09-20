"""`pinecall-runtime box secrets | secret <name>`: a box's secrets, made once, kept by systemd."""

import argparse
import base64
import secrets
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any, TextIO

PURPOSE: str = "the box: its secrets, made once, as encrypted systemd credentials"
VERBS: tuple[str, ...] = ("secrets", "secret")

# Where `ImportCredential=` looks, and the one place a box's secrets live: one file per name,
# encrypted under the machine's own key (and its TPM, when it has one), decrypted by systemd into
# the private credentials directory of the unit that asked. Never a .env file, never a dump.
CREDSTORE = Path("/etc/credstore.encrypted")

# The file is named EXACTLY as the credential, with no extension: systemd refuses a credential
# whose embedded name is not its filename ("does not match filename 'X.cred', refusing"), which
# was measured on a box on 2026-09-09 and is not in the manual.
SYSTEMD_CREDS = "systemd-creds"

MADE = "made {name}"
KEPT = "kept {name} — it was already there, and this verb never rewrites a secret"


def configure(parser: argparse.ArgumentParser) -> None:
    """`secrets` makes what a box generates for itself; `secret <name>` keeps one you bring."""
    verbs = parser.add_subparsers(dest="verb", metavar="verb")
    making = verbs.add_parser("secrets", help="generate every secret a box makes for itself, once")
    making.add_argument(
        "--into", type=Path, default=CREDSTORE, help=f"the credstore (default {CREDSTORE})"
    )
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
    keeping.set_defaults(run=run_secret)
    parser.set_defaults(run=lambda arguments: _print_the_verbs(parser, arguments))  # pyright: ignore[reportUnknownLambdaType] — argparse's Namespace


def run_secrets(arguments: argparse.Namespace) -> int:
    """Every secret a box makes for itself, made once. Nothing is printed but the names."""
    return make_secrets(arguments.into)


def run_secret(arguments: argparse.Namespace) -> int:
    """One secret a person brings, from stdin: never from argv, where a `ps` would read it."""
    return keep_secret(arguments.name, sys.stdin.read().strip(), arguments.into)


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
        "DATABASE_URL": f"postgresql://pinecall:{password}@127.0.0.1:5432/pinecall",
        "PINECALL_OPS_KEY": secrets.token_hex(32),
        # Fernet: 32 bytes, urlsafe base64 — what `cryptography` will accept and nothing else.
        "PINECALL_VAULT_KEY": base64.urlsafe_b64encode(secrets.token_bytes(32)).decode("ascii"),
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


def make_secrets(into: Path, out: TextIO = sys.stdout, encrypt: "Encrypt | None" = None) -> int:
    """Make what is missing and keep what is there: this verb run twice rotates nothing."""
    writing = encrypt or encrypt_with_systemd
    for name, value in generated().items():
        if (into / name).exists():
            print(KEPT.format(name=name), file=out)
            continue
        writing(name, value, into)
        print(MADE.format(name=name), file=out)
    return 0


def keep_secret(
    name: str, value: str, into: Path, out: TextIO = sys.stdout, encrypt: "Encrypt | None" = None
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


type Encrypt = Callable[[str, str, Path], None]


# The value travels on systemd-creds' stdin and lands encrypted; it is on no command line and in
# no file in the clear, not even for the instant between the two. `--with-key=auto` is the host's
# key sealed to the TPM where the machine has one, and the host's key alone where it does not.
def encrypt_with_systemd(name: str, value: str, into: Path) -> None:
    """`systemd-creds encrypt --name=<name> - <into>/<name>`, the value on stdin."""
    into.mkdir(mode=0o700, parents=True, exist_ok=True)
    subprocess.run(  # noqa: S603 — every argument is ours, and the secret is on stdin
        [SYSTEMD_CREDS, "encrypt", "--with-key=auto", f"--name={name}", "-", str(into / name)],
        input=value.encode("utf-8"),
        check=True,
    )


def _print_the_verbs(parser: argparse.ArgumentParser, _arguments: Any) -> int:
    """`box` with no verb: say what there is, and exit as a help screen does."""
    parser.print_help()
    return 0

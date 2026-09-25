"""A systemd credential, both ways: a value encrypted into a store, and one decrypted back out."""

import subprocess
from collections.abc import Callable
from pathlib import Path

# The file is named EXACTLY as the credential, with no extension: systemd refuses a credential
# whose embedded name is not its filename ("does not match filename 'X.cred', refusing"), which
# was measured on a box on 2026-09-09 and is not in the manual.
SYSTEMD_CREDS = "systemd-creds"

type Encrypt = Callable[[str, str, Path], None]
"""A value into `<store>/<name>`, sealed under that name."""

type Decrypt = Callable[[str, Path], str]
"""The value a credential file holds, opened under the name sealed into it."""


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


# Root's alone: a verb that reads another instance's secret — its ops key to mint at its gateway,
# its DSN to copy its rows — runs where the host key is, and the value goes from systemd's stdout
# into this process and nowhere else: no file, no argument, no line of output.
def decrypt_with_systemd(name: str, path: Path) -> str:
    """`systemd-creds decrypt --name=<name> <path> -`, the value off its stdout."""
    opened = subprocess.run(  # noqa: S603 — every argument is ours; the secret comes back on stdout
        [SYSTEMD_CREDS, "decrypt", f"--name={name}", str(path), "-"],
        capture_output=True,
        check=True,
    )
    return opened.stdout.decode("utf-8").strip()

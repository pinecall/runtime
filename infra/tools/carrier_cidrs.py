"""The carrier's signalling networks, read off the fence itself: for the trunk, and for a person."""

import re
import sys
from pathlib import Path

# The list lives in the fence itself — the `carrier_signalling` set of infra/box/nftables.conf —
# so the firewall and the inbound trunk are physically incapable of disagreeing about who may
# ring the box. This reads that set; nothing renders it.
THE_FENCE = Path(__file__).resolve().parents[1] / "box" / "nftables.conf"

A_SET = re.compile(r"set carrier_signalling \{.*?elements = \{(?P<elements>.*?)\}", re.DOTALL)
A_CIDR = re.compile(r"\d{1,3}(?:\.\d{1,3}){3}/\d{1,2}")


def signalling_cidrs(path: Path = THE_FENCE) -> list[str]:
    """Every network the carrier signals from, in the order the fence lists them."""
    found = A_SET.search(path.read_text(encoding="utf-8"))
    if found is None:
        raise ValueError(f"{path} declares no `set carrier_signalling`")
    return A_CIDR.findall(found.group("elements"))


def main(argv: list[str]) -> int:
    """Print the networks one per line, for a person or a carrier's allow-list."""
    if argv:
        print(
            f"usage: carrier_cidrs.py  (it takes no arguments, and was given {argv[0]!r})",
            file=sys.stderr,
        )
        return 2
    print("\n".join(signalling_cidrs()))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

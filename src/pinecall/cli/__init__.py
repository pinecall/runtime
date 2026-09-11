"""`pinecall-runtime <group> <verb>`: the dispatcher. One module per group, one parser each."""

import argparse
import sys
from collections.abc import Callable, Sequence
from typing import NoReturn, Protocol, override

from pinecall.cli import (
    box,
    chat,
    doctor,
    fleet,
    gateway,
    keys,
    migrate,
    orgs,
    routes,
    sessions,
    worker,
)


class Group(Protocol):
    """What a group module owes the dispatcher: a one-line purpose, and its verbs."""

    PURPOSE: str

    def configure(self, parser: argparse.ArgumentParser) -> None:
        """Declare the group's verbs and flags, and name the function that runs them."""
        ...


# A new group is one import and one line here; this order is the order the help prints.
GROUP_MODULES: dict[str, Group] = {
    "gateway": gateway,
    "worker": worker,
    "sessions": sessions,
    "chat": chat,
    "orgs": orgs,
    "routes": routes,
    "keys": keys,
    "fleet": fleet,
    "migrate": migrate,
    "doctor": doctor,
    "box": box,
}

GROUPS: dict[str, str] = {name: module.PURPOSE for name, module in GROUP_MODULES.items()}


def main(argv: Sequence[str] | None = None) -> int:
    """The console-script entry. No group prints them all; otherwise the group answers."""
    parser = build_parser()
    arguments = parser.parse_args(argv)
    if not hasattr(arguments, "run"):
        parser.print_help()
        return 0
    # Every group's configure() left the function that does the work under `run`.
    run: Callable[[argparse.Namespace], int] = arguments.run
    return run(arguments)


def build_parser() -> argparse.ArgumentParser:
    """The whole CLI as one tree: the top parser, and one subparser per group module."""
    parser = _Parser(
        prog="pinecall-runtime",
        usage="pinecall-runtime <group> <verb>",
        description="The Pinecall runtime: the gateway, the worker, and the box they run on.",
    )
    # Without prog=, a group's own errors inherit the usage line above and read
    # "pinecall-runtime <group> <verb> worker: ...".
    groups = parser.add_subparsers(title="groups", metavar="<group>", prog=parser.prog)
    for name, module in GROUP_MODULES.items():
        module.configure(groups.add_parser(name, help=module.PURPOSE))
    return parser


class _Parser(argparse.ArgumentParser):
    """argparse answers a wrong argument with one usage line; a CLI this small can show it all."""

    @override
    def error(self, message: str) -> NoReturn:
        self.print_help(sys.stderr)
        print(f"\n{self.prog}: {message}", file=sys.stderr)
        raise SystemExit(2)

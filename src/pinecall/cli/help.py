"""A group typed with no verb: its help screen, and the exit code a help screen leaves with."""

from __future__ import annotations

import argparse
from collections.abc import Callable


def only_the_help(parser: argparse.ArgumentParser) -> Callable[[argparse.Namespace], int]:
    """What `run` is for a group with verbs, when none was typed."""

    def printed(_arguments: argparse.Namespace) -> int:
        parser.print_help()
        return 0

    return printed

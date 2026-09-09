"""Route: one door into one agent, in one org. A number is a route, never an agent."""

import re
from dataclasses import dataclass

from pinecall.types.channel import CHANNELS, CHANNELS_WITH_A_NUMBER, Channel
from pinecall.types.refused import DeclarationRefused

# E.164: a plus, then up to fifteen digits, the first of them never zero.
_E164 = re.compile(r"^\+[1-9]\d{1,14}$")


@dataclass(frozen=True)
class Route:
    """Which agent answers at this door, and whose agent it is. The gateway's table holds many."""

    org: str
    agent: str
    channel: Channel
    number: str | None = None
    label: str | None = None

    def __post_init__(self) -> None:
        if not self.org or not self.agent:
            raise DeclarationRefused(
                "a route names the org that owns it and the agent that answers"
            )
        if self.channel not in CHANNELS:
            raise DeclarationRefused(
                f"a route is a door: one of {sorted(CHANNELS)}, not {self.channel!r}"
            )
        if self.channel in CHANNELS_WITH_A_NUMBER:
            if self.number is None or not _E164.match(self.number):
                raise DeclarationRefused(
                    f"a {self.channel} route answers at a number in E.164 form, not {self.number!r}"
                )
        elif self.number is not None:
            raise DeclarationRefused(f"the {self.channel} widget answers at no number")

    # The registry keys on this: one agent per door at a time, whatever the org or the label.
    @property
    def door(self) -> tuple[str, str | None]:
        """What the public dials or opens: the channel and, when there is one, the number."""
        return (self.channel, self.number)

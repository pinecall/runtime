"""What an org sets per agent: how its widget looks, and its hold melody."""

import hashlib
import re
from dataclasses import asdict, dataclass
from typing import Literal

from pinecall.domain.errors import DeclarationRefused
from pinecall.domain.scope import Scope
from pinecall.postgres.pool import Pool
from pinecall.wire.parts import WidgetTheme

# No row is the box's own melody; `off` is silence; `custom` an uploaded clip, converted once.
type Played = Literal["off", "custom"]


LONGEST = {"title": 80, "tagline": 160, "greeting": 500, "accent": 40}


# The widget sets the accent as a CSS variable, so nothing that could end the declaration and
# open another passes.
A_COLOUR = re.compile(
    r"^(#[0-9a-fA-F]{3,8}|[a-zA-Z]+|(rgb|rgba|hsl|hsla|oklch)\([0-9.,%\s/a-z-]+\))$"
)


LOOK = """
SELECT title, tagline, greeting, accent, autostart, theme FROM agent_widgets
WHERE org = %(org)s AND env = %(env)s AND agent = %(agent)s
"""


PUT_LOOK = """
INSERT INTO agent_widgets (org, env, agent, title, tagline, greeting, accent, autostart, theme)
VALUES (%(org)s, %(env)s, %(agent)s, %(title)s, %(tagline)s, %(greeting)s, %(accent)s,
        %(autostart)s, %(theme)s)
ON CONFLICT (org, env, agent) DO UPDATE SET
    title = excluded.title, tagline = excluded.tagline, greeting = excluded.greeting,
    accent = excluded.accent, autostart = excluded.autostart, theme = excluded.theme,
    set_at = now()
"""


HOLD = """
SELECT played, sha256, seconds, name FROM hold_audio
WHERE org = %(org)s AND env = %(env)s AND agent = %(agent)s
"""


HOLD_AUDIO = (
    "SELECT audio FROM hold_audio WHERE org = %(org)s AND env = %(env)s AND agent = %(agent)s"
)


KEEP_HOLD = """
INSERT INTO hold_audio (org, env, agent, played, audio, sha256, seconds, name)
VALUES (%(org)s, %(env)s, %(agent)s, %(played)s, %(audio)s, %(sha256)s, %(seconds)s, %(name)s)
ON CONFLICT (org, env, agent) DO UPDATE SET
    played = excluded.played, audio = excluded.audio, sha256 = excluded.sha256,
    seconds = excluded.seconds, name = excluded.name, set_at = now()
"""


FORGET_HOLD = "DELETE FROM hold_audio WHERE org = %(org)s AND env = %(env)s AND agent = %(agent)s"


@dataclass(frozen=True)
class Look:
    """How the widget presents the agent; None is the widget's own default."""

    title: str | None = None
    tagline: str | None = None
    greeting: str | None = None
    accent: str | None = None
    autostart: bool = False
    theme: WidgetTheme | None = None

    def __post_init__(self) -> None:
        text = {
            "title": self.title,
            "tagline": self.tagline,
            "greeting": self.greeting,
            "accent": self.accent,
        }
        for name, value in text.items():
            longest = LONGEST[name]
            if value is not None and len(value) > longest:
                raise DeclarationRefused(f"a widget's {name} is {longest} characters at most")
        if self.accent is not None and not A_COLOUR.match(self.accent):
            raise DeclarationRefused(f"an accent is a CSS colour, not {self.accent!r}")


@dataclass(frozen=True)
class Chosen:
    """What the agent plays while a tool runs: nothing, or a clip of its own."""

    played: Played
    sha256: str | None = None
    seconds: float | None = None
    name: str | None = None


@dataclass(frozen=True)
class Clip:
    """An uploaded melody, already converted: its bytes, how long it lasts, the file's name."""

    audio: bytes
    seconds: float
    name: str


async def look_of(pool: Pool, scope: Scope, agent: str) -> Look:
    """How the agent's widget looks in the world; the widget's defaults when nobody said."""
    async with pool.connection() as connection:
        row = await (await connection.execute(LOOK, _of(scope, agent))).fetchone()
    return Look() if row is None else Look(**row)


async def put_look(pool: Pool, scope: Scope, agent: str, look: Look) -> None:
    """Replace how the agent's widget looks in the world."""
    values = {**_of(scope, agent), **asdict(look)}
    async with pool.connection() as connection:
        await connection.execute(PUT_LOOK, values)


async def hold_of(pool: Pool, scope: Scope, agent: str) -> Chosen | None:
    """What the agent plays while a tool runs in the world; None is the box's melody."""
    async with pool.connection() as connection:
        row = await (await connection.execute(HOLD, _of(scope, agent))).fetchone()
    return None if row is None else Chosen(**row)


async def hold_audio(pool: Pool, scope: Scope, agent: str) -> bytes | None:
    """The agent's own clip, when it plays one."""
    async with pool.connection() as connection:
        row = await (await connection.execute(HOLD_AUDIO, _of(scope, agent))).fetchone()
    return None if row is None or row["audio"] is None else bytes(row["audio"])


async def keep_hold(pool: Pool, scope: Scope, agent: str, clip: Clip) -> Chosen:
    """Play this clip while the agent's tools run in the world."""
    chosen = Chosen("custom", hashlib.sha256(clip.audio).hexdigest(), clip.seconds, clip.name)
    await _kept_hold(pool, {**_of(scope, agent), **asdict(chosen), "audio": clip.audio})
    return chosen


async def silence_hold(pool: Pool, scope: Scope, agent: str) -> None:
    """Play nothing while the agent's tools run; any clip is forgotten."""
    await _kept_hold(pool, {**_of(scope, agent), **asdict(Chosen("off")), "audio": None})


async def forget_hold(pool: Pool, scope: Scope, agent: str) -> None:
    """Play the box's own melody again; any clip is forgotten."""
    async with pool.connection() as connection:
        await connection.execute(FORGET_HOLD, _of(scope, agent))


async def _kept_hold(pool: Pool, values: dict[str, object]) -> None:
    async with pool.connection() as connection:
        await connection.execute(KEEP_HOLD, values)


def _of(scope: Scope, agent: str) -> dict[str, object]:
    return {"org": scope.org, "env": scope.env, "agent": agent}

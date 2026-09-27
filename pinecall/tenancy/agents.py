"""What an org sets per agent: how its widget looks, its hold melody, its personas, caller codes."""

import asyncio
import contextlib
import hashlib
import re
import secrets
import time
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime
from typing import Literal

from psycopg.rows import DictRow
from psycopg.types.json import Jsonb

from pinecall.domain.errors import Conflict, DeclarationRefused, NotFound, QuotaExhausted
from pinecall.domain.types import Corner, Env, Json, JsonObject
from pinecall.log.log import Logs
from pinecall.postgres.pool import Pool
from pinecall.wire.events import CodeClaimed, CodeIssued
from pinecall.wire.parts import Projection, WidgetTheme

# ── the widget ──

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

# ── the hold melody ──

# No row is the box's own melody; `off` is silence; `custom` an uploaded clip, converted once.
type Played = Literal["off", "custom"]

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

# ── personas ──

NOBODY = "no persona called {name} in this org"
TAKEN = "this org has a persona called {name} already"

PERSONAS = """
SELECT name, about, goal, style, facts, state, llm, tts, voice, accepts_when, declines_when,
       author, set_at
FROM agent_personas WHERE org = %(org)s ORDER BY name
"""
PERSONA = """
SELECT name, about, goal, style, facts, state, llm, tts, voice, accepts_when, declines_when,
       author, set_at
FROM agent_personas WHERE org = %(org)s AND name = %(name)s
"""
# One statement, so a rename never leaves both names or neither. A `was` that is the same name,
# or none, deletes nothing.
PUT_PERSONA = """
WITH gone AS (
    DELETE FROM agent_personas WHERE org = %(org)s AND name = %(was)s AND %(was)s <> %(name)s
    RETURNING name
)
INSERT INTO agent_personas (org, name, about, goal, style, facts, state, author, llm, tts,
                            voice, accepts_when, declines_when)
VALUES (%(org)s, %(name)s, %(about)s, %(goal)s, %(style)s, %(facts)s, %(state)s, %(author)s,
        %(llm)s, %(tts)s, %(voice)s, %(accepts_when)s, %(declines_when)s)
ON CONFLICT (org, name) DO UPDATE SET
    about = excluded.about, goal = excluded.goal, style = excluded.style,
    facts = excluded.facts, state = excluded.state, llm = excluded.llm, tts = excluded.tts,
    voice = excluded.voice, accepts_when = excluded.accepts_when,
    declines_when = excluded.declines_when, author = excluded.author, set_at = now()
"""
DROP_PERSONA = "DELETE FROM agent_personas WHERE org = %(org)s AND name = %(name)s RETURNING name"

# ── caller codes ──

ISSUED = "code.issued"
CLAIMED = "code.claimed"
# Four digits to key on a phone; the cap per agent stops a page minting them by the thousand.
DIGITS = 4
LIVE_PER_AGENT = 50
TOO_MANY = (
    "agent {agent} already has {live} codes waiting for a call: let one be claimed or expire "
    "before issuing another"
)


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
        said = {
            "title": self.title,
            "tagline": self.tagline,
            "greeting": self.greeting,
            "accent": self.accent,
        }
        for name, value in said.items():
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


@dataclass(frozen=True)
class Persona:
    """A caller an eval plays: who they are, what they want, how they talk, what they know."""

    name: str
    goal: str
    style: str
    about: str = ""
    facts: dict[str, str] = field(default_factory=dict[str, str])
    state: JsonObject = field(default_factory=dict[str, Json])
    # The vendors the caller is played on; None is the runtime's.
    llm: str | None = None
    tts: str | None = None
    voice: str | None = None
    accepts_when: str = ""
    declines_when: str = ""


@dataclass(frozen=True)
class Kept:
    """A persona as the org keeps it: who wrote it last, and when."""

    persona: Persona
    author: str
    set_at: datetime


@dataclass(frozen=True)
class Code:
    """A code a page shows, waiting for the call that keys it, and the call once one did."""

    code: str
    env: Env
    agent: str
    expires_at: float
    log: Projection
    claimed: str | None = None


async def look_of(pool: Pool, corner: Corner, agent: str) -> Look:
    """How the agent's widget looks in the world; the widget's defaults when nobody said."""
    async with pool.connection() as connection:
        row = await (await connection.execute(LOOK, _of(corner, agent))).fetchone()
    return Look() if row is None else Look(**row)


async def put_look(pool: Pool, corner: Corner, agent: str, look: Look) -> None:
    """Replace how the agent's widget looks in the world."""
    values = {**_of(corner, agent), **asdict(look)}
    async with pool.connection() as connection:
        await connection.execute(PUT_LOOK, values)


async def hold_of(pool: Pool, corner: Corner, agent: str) -> Chosen | None:
    """What the agent plays while a tool runs in the world; None is the box's melody."""
    async with pool.connection() as connection:
        row = await (await connection.execute(HOLD, _of(corner, agent))).fetchone()
    return None if row is None else Chosen(**row)


async def hold_audio(pool: Pool, corner: Corner, agent: str) -> bytes | None:
    """The agent's own clip, when it plays one."""
    async with pool.connection() as connection:
        row = await (await connection.execute(HOLD_AUDIO, _of(corner, agent))).fetchone()
    return None if row is None or row["audio"] is None else bytes(row["audio"])


async def keep_hold(pool: Pool, corner: Corner, agent: str, clip: Clip) -> Chosen:
    """Play this clip while the agent's tools run in the world."""
    chosen = Chosen("custom", hashlib.sha256(clip.audio).hexdigest(), clip.seconds, clip.name)
    await _kept_hold(pool, corner, agent, chosen, clip.audio)
    return chosen


async def silence_hold(pool: Pool, corner: Corner, agent: str) -> None:
    """Play nothing while the agent's tools run; any clip is forgotten."""
    await _kept_hold(pool, corner, agent, Chosen("off"), None)


async def forget_hold(pool: Pool, corner: Corner, agent: str) -> None:
    """Play the box's own melody again; any clip is forgotten."""
    async with pool.connection() as connection:
        await connection.execute(FORGET_HOLD, _of(corner, agent))


async def personas_of(pool: Pool, org: str) -> list[Kept]:
    """Every caller of the org, by name; every agent of the org reads the same ones."""
    async with pool.connection() as connection:
        rows = await (await connection.execute(PERSONAS, {"org": org})).fetchall()
    return [_persona(row) for row in rows]


async def persona(pool: Pool, org: str, name: str) -> Kept | None:
    """One caller of the org, by name."""
    async with pool.connection() as connection:
        row = await (await connection.execute(PERSONA, {"org": org, "name": name})).fetchone()
    return None if row is None else _persona(row)


async def put_persona(
    pool: Pool, org: str, written: Persona, *, author: str, was: str | None = None
) -> None:
    """Write a caller whole: a new one, the same name again, or a rename from `was`."""
    if was is not None and was != written.name:
        if await persona(pool, org, was) is None:
            raise NotFound(NOBODY.format(name=was))
        if await persona(pool, org, written.name) is not None:
            raise Conflict(TAKEN.format(name=written.name))
    values = {
        **asdict(written),
        "facts": Jsonb(written.facts),
        "state": Jsonb(written.state),
        "org": org,
        "author": author,
        "was": was,
    }
    async with pool.connection() as connection:
        await connection.execute(PUT_PERSONA, values)


async def drop_persona(pool: Pool, org: str, name: str) -> None:
    """Forget a caller of the org."""
    async with pool.connection() as connection:
        dropped = await connection.execute(DROP_PERSONA, {"org": org, "name": name})
        if await dropped.fetchone() is None:
            raise NotFound(NOBODY.format(name=name))


# Kept on each agent's log as code.issued and code.claimed (no call: it expired), and held here
# so a page asking again costs no read. A code is the agent's, whatever the world.
class Codes:
    """Every agent's live caller codes, and a waiting page's wake-up for each."""

    def __init__(self, logs: Logs) -> None:
        """No code issued yet; `loaded` reads the ones a previous process left open."""
        self.logs = logs
        self.issued: dict[tuple[str, str], Code] = {}
        self.taken: dict[tuple[str, str], asyncio.Event] = {}

    async def issue(self, env: Env, agent: str, ttl_s: float, log: Projection) -> Code:
        """A new code for the agent, written on its log; at most fifty live at once."""
        await self._swept(time.time())
        held = {code for holder, code in self.issued if holder == agent}
        if len(held) >= LIVE_PER_AGENT:
            raise QuotaExhausted(TOO_MANY.format(agent=agent, live=len(held)))
        drawn = _drawn()
        while drawn in held:
            drawn = _drawn()
        issued = Code(drawn, env, agent, time.time() + ttl_s, log)
        self._kept(issued)
        said = CodeIssued(code=drawn, env=env, expires_at=issued.expires_at, log=log)
        await self.logs.agent(agent).append(ISSUED, said.written())
        return issued

    async def standing(self, env: Env, agent: str, code: str) -> Code | None:
        """The code as it stands, once more after it expired; None for one nobody issued here."""
        issued = self.issued.get((agent, code))
        await self._swept(time.time())
        return issued if issued is not None and issued.env == env else None

    async def waited(self, issued: Code, within_s: float) -> Code:
        """Wait up to that long for a call to key the code, and say how it stands."""
        taken = self.taken.get((issued.agent, issued.code))
        if taken is not None and within_s > 0:
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(taken.wait(), within_s)
        return self.issued.get((issued.agent, issued.code), issued)

    # Marked before the log is written, so two calls keying the same code cannot both have it.
    async def claim(self, env: Env, agent: str, code: str, call: str) -> Code | None:
        """Give the code to the call that keyed it; None when it is nobody's, dead or taken."""
        await self._swept(time.time())
        issued = self.issued.get((agent, code))
        if issued is None or issued.env != env or issued.claimed is not None:
            return None
        claimed = replace(issued, claimed=call)
        self.issued[(agent, code)] = claimed
        await self.logs.agent(agent).append(CLAIMED, CodeClaimed(code=code, call=call).written())
        taken = self.taken.get((agent, code))
        if taken is not None:
            taken.set()
        return claimed

    async def loaded(self) -> None:
        """Read back every code still open on the agents' logs, as a new process starts."""
        after = 0
        while page := await self.logs.store.across([ISSUED, CLAIMED], after=after):
            for metered in page:
                entry = metered.entry
                if entry.type == ISSUED:
                    said = CodeIssued.model_validate(entry.data)
                    self._kept(Code(said.code, said.env, entry.agent, said.expires_at, said.log))
                else:
                    self._closed(entry.agent, CodeClaimed.model_validate(entry.data))
            after = page[-1].position

    # Swept as codes are asked for, not by a loop of its own.
    async def _swept(self, now: float) -> None:
        # Taken out before the first await, so a sweep running beside this one logs none twice.
        expired = [one for one in self.issued.values() if now >= one.expires_at]
        for issued in expired:
            self.issued.pop((issued.agent, issued.code), None)
            self.taken.pop((issued.agent, issued.code), None)
        for issued in expired:
            if issued.claimed is None:
                closed = CodeClaimed(code=issued.code, call=None).written()
                await self.logs.agent(issued.agent).append(CLAIMED, closed)

    def _kept(self, issued: Code) -> None:
        self.issued[(issued.agent, issued.code)] = issued
        self.taken[(issued.agent, issued.code)] = asyncio.Event()

    def _closed(self, agent: str, said: CodeClaimed) -> None:
        issued = self.issued.get((agent, said.code))
        if issued is None:
            return
        if said.call is None:
            self.issued.pop((agent, said.code), None)
            self.taken.pop((agent, said.code), None)
            return
        self.issued[(agent, said.code)] = replace(issued, claimed=said.call)


async def _kept_hold(
    pool: Pool, corner: Corner, agent: str, chosen: Chosen, clip: bytes | None
) -> None:
    values = {**_of(corner, agent), **asdict(chosen), "audio": clip}
    async with pool.connection() as connection:
        await connection.execute(KEEP_HOLD, values)


def _persona(row: DictRow) -> Kept:
    author = row.pop("author")
    set_at = row.pop("set_at")
    return Kept(persona=Persona(**row), author=author, set_at=set_at)


def _of(corner: Corner, agent: str) -> dict[str, object]:
    return {"org": corner.org, "env": corner.env, "agent": agent}


def _drawn() -> str:
    return f"{secrets.randbelow(10**DIGITS):0{DIGITS}d}"

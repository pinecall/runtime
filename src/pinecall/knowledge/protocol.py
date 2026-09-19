"""Knowledge, the Protocol: a tenant's bases pushed whole, listed, dropped, searched two ways."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from pinecall.knowledge.files import File
from pinecall.knowledge.store import Base
from pinecall.types import Chunk, Env, KnowledgeFile
from pinecall.types.knowledge import DEFAULT_CHUNKS_PER_TURN


# What the doors and a lookup hold: the verbs, never the table. PgKnowledge is the one
# implementation in the tree; a test hands the service a fake of this shape and no Postgres.
class Knowledge(Protocol):
    """The knowledge base as a turn and a push see it: put, bases, drop, search."""

    # A base is one WORLD's, as the agent that answers from it is: a push with a laptop's key
    # replaces the laptop's base and never the one the telephone answers from. Promoting is the
    # same push made with the key the box runs on. 0018 is where the column went in.
    #
    # And one CORNER's, for the same reason the agent is (0021): three developers of one tenant
    # push their own folders, and before this the second one replaced what the first was testing
    # against. `holder` is the member the key names, or None for the org's own — production's
    # always, and CI's. A READ falls back to the org's for a name this corner has not pushed:
    # nobody joins a team to an empty knowledge base. A push and a drop never do — they are about
    # one copy, and the fallback would make a laptop's drop take the telephone's base.
    async def put(
        self, org: str, env: Env, holder: str | None, base: str, files: Sequence[KnowledgeFile]
    ) -> int:
        """Replace THIS corner's base with these files; how many chunks it became."""
        ...

    async def bases(self, org: str, env: Env, holder: str | None = None) -> list[Base]:
        """Every base this corner can read in this world: its own, and the org's for a name it has
        not pushed."""
        ...

    async def drop(self, org: str, env: Env, holder: str | None, base: str) -> bool:
        """Forget THIS corner's base and its chunks. False when it pushed none by that name."""
        ...

    # The files of a base one at a time (0041): what a person at the console reads, adds, edits
    # and takes out without a folder on a laptop. A read falls back to the org's copy as `bases`
    # does; a put and a drop are about this corner's own, as a push is.
    async def files(self, org: str, env: Env, holder: str | None, base: str) -> list[File]:
        """Every file of the base this corner reads, by path, without their text."""
        ...

    async def file(
        self, org: str, env: Env, holder: str | None, base: str, path: str
    ) -> File | None:
        """One file of the base this corner reads, text and all; None when there is none."""
        ...

    async def freed_by(self, org: str, env: Env, holder: str | None, base: str, path: str) -> int:
        """How many chunks this corner's own copy of the file holds: what putting it frees."""
        ...

    async def put_file(
        self, org: str, env: Env, holder: str | None, base: str, file: KnowledgeFile
    ) -> int:
        """This corner's copy of one file replaced, or the base begun with it; how many chunks."""
        ...

    async def drop_file(self, org: str, env: Env, holder: str | None, base: str, path: str) -> bool:
        """This corner's copy of one file gone, and the base with it when it was the last."""
        ...

    # What the knowledge_chunks quota is measured against, and the one read of both worlds at
    # once: a chunk a laptop pushed is a row on the same disk as one the box pushed, and a plan
    # that capped only production would cap nothing. What a push about to replace a base frees is
    # the door's arithmetic — it is holding that base's row already.
    async def kept(self, org: str) -> int:
        """How many chunks this org holds across its bases, in both worlds."""
        ...

    # The other half of the same question, and the only one that can be asked before a row is
    # written: a push is judged whole, so the door asks how big it would be and never guesses.
    def how_many_chunks(self, files: Sequence[KnowledgeFile]) -> int:
        """How many chunks these files would become, cut as a push would cut them."""
        ...

    async def search(
        self,
        org: str,
        env: Env,
        holder: str | None,
        base: str,
        query: str,
        *,
        k: int = DEFAULT_CHUNKS_PER_TURN,
        min_score: float | None = None,
    ) -> list[Chunk]:
        """The best k chunks for the query, by meaning and by words, fused; under min_score, cut."""
        ...

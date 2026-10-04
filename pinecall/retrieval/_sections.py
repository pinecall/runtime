"""A found chunk handed back as its section, and which sections a search hands back."""

from collections.abc import Mapping, Sequence

from pinecall.domain.scope import Scope
from pinecall.postgres.pool import Connection

# A found chunk comes back as its section: the chunks of its heading this many either side of it.
SECTION_SPAN = 2


# The best pages give every section that was found in them; every page after them gives one.
PAGES_GIVING_ALL = 2


# The chunks around each found one, under the same heading of the same file, in the file's order.
SECTIONS = """
SELECT wanted.at, chunk.text
FROM unnest(%(ats)s::integer[], %(bases)s::text[], %(holders)s::text[], %(paths)s::text[],
    %(headings)s::text[], %(firsts)s::integer[], %(lasts)s::integer[])
    AS wanted (at, base, holder, path, heading, first, last)
JOIN knowledge_chunks AS chunk ON chunk.org = %(org)s AND chunk.env = %(env)s
    AND chunk.base = wanted.base AND chunk.holder = wanted.holder AND chunk.path = wanted.path
    AND chunk.heading IS NOT DISTINCT FROM wanted.heading
    AND chunk.ordinal BETWEEN wanted.first AND wanted.last
ORDER BY wanted.at, chunk.ordinal
"""


def sections_chosen(rows: Sequence[Mapping[str, object]], k: int) -> list[int]:
    """Which hits come back, best first: one per section, the best pages all theirs, k at most."""
    chosen: list[int] = []
    sections: set[tuple[object, object, object]] = set()
    pages: list[tuple[object, object]] = []
    for at, row in enumerate(rows):
        page = (row["base"], row["path"])
        section = (row["base"], row["path"], row["heading"])
        if section in sections or (page in pages and pages.index(page) >= PAGES_GIVING_ALL):
            continue
        if page not in pages:
            pages.append(page)
        sections.add(section)
        chosen.append(at)
        if len(chosen) == k:
            break
    return chosen


async def section_texts(
    connection: Connection, scope: Scope, rows: Sequence[Mapping[str, object]]
) -> dict[int, list[str]]:
    """Each row's section as its chunks' indexed texts in the file's order, by the row's place."""
    if not rows:
        return {}
    ordinals = [int(str(row["ordinal"])) for row in rows]
    named = {
        "org": scope.org,
        "env": scope.env,
        "ats": list(range(len(rows))),
        "bases": [row["base"] for row in rows],
        "holders": [row["holder"] for row in rows],
        "paths": [row["path"] for row in rows],
        "headings": [row["heading"] for row in rows],
        "firsts": [ordinal - SECTION_SPAN for ordinal in ordinals],
        "lasts": [ordinal + SECTION_SPAN for ordinal in ordinals],
    }
    texts: dict[int, list[str]] = {}
    for row in await (await connection.execute(SECTIONS, named)).fetchall():
        texts.setdefault(int(row["at"]), []).append(str(row["text"]))
    return texts

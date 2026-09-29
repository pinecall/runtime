"""An org's world as JSON Lines: calls and logs, memories, settings, words, documents, consent."""

import json
import time
from collections.abc import AsyncIterator

from pinecall.domain.names import Env
from pinecall.postgres.pool import Pool

# One page of calls at a time, each with its whole log, so an org of any size streams in bounded
# memory. Postgres writes the JSON: timestamps, jsonb and nulls come out as they are stored.
CALLS = """
SELECT jsonb_build_object(
    'kind', 'call', 'call', head.call, 'agent', head.agent, 'holder', head.holder,
    'started_at', head.started_at, 'sealed', head.sealed,
    'facts', to_jsonb(facts) - 'call',
    'entries', (
        SELECT coalesce(jsonb_agg(jsonb_build_object(
            'seq', entry.seq, 'ts', entry.ts, 'type', entry.type, 'data', entry.data
        ) ORDER BY entry.seq), '[]'::jsonb)
        FROM call_log entry WHERE entry.log = head.log
    )
)::text AS line, coalesce(head.started_at, 0) AS at, head.call
FROM call_log_head head
LEFT JOIN call_facts facts ON facts.call = head.call
WHERE head.org = %(org)s AND head.env = %(env)s AND head.call IS NOT NULL
  AND (coalesce(head.started_at, 0), head.call) > (%(at)s, %(call)s)
ORDER BY coalesce(head.started_at, 0), head.call
LIMIT %(page)s
"""

# The embedding is the box's to compute again; the words are the org's.
MEMORIES = """
SELECT jsonb_build_object(
    'kind', 'memory', 'contact', contact, 'holder', holder, 'text', text, 'category', category,
    'valid_from', valid_from, 'invalidated_at', invalidated_at, 'source_call', source_call
)::text AS line
FROM contact_memories WHERE org = %(org)s AND env = %(env)s
ORDER BY contact, valid_from
"""

SETTINGS = """
SELECT jsonb_build_object(
    'kind', 'agent_config', 'agent', agent, 'holder', holder, 'version', version,
    'config', config, 'author', author, 'note', note, 'set_at', set_at
)::text AS line
FROM agent_config WHERE org = %(org)s AND env = %(env)s
ORDER BY agent, holder, version
"""

WORDS = """
SELECT jsonb_build_object(
    'kind', 'lexicon', 'agent', agent, 'holder', holder, 'version', version,
    'said', said, 'heard', heard, 'author', author, 'note', note, 'set_at', set_at
)::text AS line
FROM lexicon WHERE org = %(org)s AND env = %(env)s
ORDER BY agent, holder, version
"""

DOCUMENTS = """
SELECT jsonb_build_object(
    'kind', 'knowledge_file', 'base', base, 'holder', holder, 'path', path, 'text', text,
    'pushed_at', pushed_at
)::text AS line
FROM knowledge_files WHERE org = %(org)s AND env = %(env)s
ORDER BY base, path
"""

CONSENTS = """
SELECT jsonb_build_object(
    'kind', 'consent', 'number', number, 'consent', kind, 'source', source, 'text', text,
    'evidence', evidence, 'given_by', given_by, 'call', call, 'given_at', given_at
)::text AS line
FROM contact_consents WHERE org = %(org)s AND env = %(env)s
ORDER BY number, given_at, id
"""

A_PAGE_OF_CALLS = 100


async def lines(pool: Pool, org: str, env: Env) -> AsyncIterator[str]:
    """Every line: the header, calls, memories, settings, words, documents, consent."""
    yield json.dumps({"kind": "export", "org": org, "env": env, "exported_at": time.time()})
    at, call = -1.0, ""
    while True:
        async with pool.connection() as connection:
            params = {"org": org, "env": env, "at": at, "call": call, "page": A_PAGE_OF_CALLS}
            rows = await (await connection.execute(CALLS, params)).fetchall()
        for row in rows:
            yield str(row["line"])
        if len(rows) < A_PAGE_OF_CALLS:
            break
        at, call = float(rows[-1]["at"]), str(rows[-1]["call"])
    for query in (MEMORIES, SETTINGS, WORDS, DOCUMENTS, CONSENTS):
        async with pool.connection() as connection:
            rows = await (await connection.execute(query, {"org": org, "env": env})).fetchall()
        for row in rows:
            yield str(row["line"])

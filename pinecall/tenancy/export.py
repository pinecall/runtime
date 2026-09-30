"""An org's world as JSON Lines: calls, memories, settings, words, documents, consent, drift."""

import json
import time
from collections.abc import AsyncIterator

from pinecall.domain.names import Env
from pinecall.postgres.pool import Pool, unbounded

# One page of calls at a time, each with its whole log, so an org of any size streams in bounded
# memory. Every read is unbounded: an org's list of anything has no size. Postgres writes the
# JSON: timestamps, jsonb and nulls come out as they are stored.
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

# Every canary set and cleared, as the settings are: nothing is ever updated.
CANARIES = """
SELECT jsonb_build_object(
    'kind', 'canary', 'agent', agent, 'holder', holder, 'version', version, 'share', share,
    'author', author, 'note', note, 'set_at', set_at
)::text AS line
FROM agent_canaries WHERE org = %(org)s AND env = %(env)s
ORDER BY agent, holder, set_at, id
"""

# The dataset is the org's in both worlds: every export carries it whole, with the world its
# call ran in.
CASES = """
SELECT jsonb_build_object(
    'kind', 'eval_case', 'id', id, 'agent', agent, 'name', name, 'golden', golden,
    'source_call', source_call, 'source_env', source_env, 'held_out', held_out,
    'author', author, 'created_at', created_at
)::text AS line
FROM eval_cases WHERE org = %(org)s
ORDER BY agent, name
"""

# Each block of prompt the org's calls were told, once per text, the same in both worlds.
PROMPTS = """
SELECT jsonb_build_object(
    'kind', 'prompt', 'hash', hash, 'text', text, 'first_used_at', first_used_at
)::text AS line
FROM prompts WHERE org = %(org)s
ORDER BY first_used_at, hash
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

# What the seal counted of each day, by agent and version: numbers and the judges' names.
STAGE_DAYS = """
SELECT jsonb_build_object(
    'kind', 'stage_day', 'holder', holder, 'agent', agent, 'day', day,
    'config_version', config_version, 'stage', stage, 'vendor', vendor, 'model', model,
    'turns', turns, 'buckets', buckets, 'confidence_sum', confidence_sum,
    'confidence_turns', confidence_turns
)::text AS line
FROM stage_days WHERE org = %(org)s AND env = %(env)s
ORDER BY day, agent, config_version, stage, vendor, model
"""

JUDGE_DAYS = """
SELECT jsonb_build_object(
    'kind', 'judge_day', 'holder', holder, 'agent', agent, 'day', day,
    'config_version', config_version, 'judge', judge, 'criteria', criteria, 'held', held,
    'broken', broken
)::text AS line
FROM judge_days WHERE org = %(org)s AND env = %(env)s
ORDER BY day, agent, config_version, judge, criteria
"""

A_PAGE_OF_CALLS = 100


# What follows the calls, in this order.
AFTER_THE_CALLS = (
    MEMORIES,
    SETTINGS,
    CANARIES,
    WORDS,
    DOCUMENTS,
    CONSENTS,
    CASES,
    PROMPTS,
    STAGE_DAYS,
    JUDGE_DAYS,
)


async def lines(pool: Pool, org: str, env: Env) -> AsyncIterator[str]:
    """Every line: the header, calls, memories, settings, words, documents, consent, drift."""
    yield json.dumps({"kind": "export", "org": org, "env": env, "exported_at": time.time()})
    at, call = -1.0, ""
    while True:
        async with unbounded(pool) as connection:
            params = {"org": org, "env": env, "at": at, "call": call, "page": A_PAGE_OF_CALLS}
            rows = await (await connection.execute(CALLS, params)).fetchall()
        for row in rows:
            yield str(row["line"])
        if len(rows) < A_PAGE_OF_CALLS:
            break
        at, call = float(rows[-1]["at"]), str(rows[-1]["call"])
    for query in AFTER_THE_CALLS:
        async with unbounded(pool) as connection:
            rows = await (await connection.execute(query, {"org": org, "env": env})).fetchall()
        for row in rows:
            yield str(row["line"])

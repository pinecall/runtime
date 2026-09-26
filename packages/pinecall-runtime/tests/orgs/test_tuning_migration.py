"""0037 on a box with turned knobs: every set becomes v1 of both worlds; the floor opens words."""

import json
from collections.abc import AsyncIterator

import pytest

from pinecall.db import apply_migrations
from tests.orgs.boxes import Box, a_box_before, applied_by_hand, migrations_before
from tests.support.postgres import Dev

pytestmark = pytest.mark.postgres

THE_ORG = "clinica"
THE_AGENT = "clinica-norte"


@pytest.fixture
async def a_box_from_before(postgres: Dev) -> AsyncIterator[Box]:
    """Every migration up to 0036 applied by hand, one org, one turned set, two keys."""
    async with a_box_before(postgres, "0037", named="tuning") as box:
        await box.connection.execute(
            "insert into orgs (id, slug, name) values ($1, $1, $1)", THE_ORG
        )
        await box.connection.execute(
            "insert into pipeline_overrides (org, agent, voice, llm, greeting) "
            "values ($1, $2, $3, $4, $5)",
            THE_ORG,
            THE_AGENT,
            "carolina",
            "anthropic/claude-haiku-4-5",
            "Buenas.",
        )
        keyed = (
            "insert into api_keys (id, hash, org, label, env, scopes) "
            "values ($1, $2, $3, $4, $5, $6)"
        )
        await box.connection.execute(
            keyed,
            "k_floor",
            "hash-of-the-floors-key",
            THE_ORG,
            "supervisor",
            "sandbox",
            ["calls", "evals", "supervise", "talk", "memory"],
        )
        await box.connection.execute(
            keyed,
            "k_qa",
            "hash-of-the-qa-key",
            THE_ORG,
            "qa",
            "sandbox",
            ["calls", "evals"],
        )
        yield box


async def test_0037_copies_every_turned_set_as_v1_of_both_worlds(a_box_from_before: Box) -> None:
    """The old row had no world and was read by both: the copy is what it already meant."""
    box = a_box_from_before
    applied = (await apply_migrations(box.dsn, schema=box.schema)).applied
    assert applied[0] == "0037_agent_tuning.sql"
    rows = await box.connection.fetch(
        "select env, holder, version, config, author, note from agent_config "
        "where org = $1 and agent = $2 order by env",
        THE_ORG,
        THE_AGENT,
    )
    assert [(row["env"], row["holder"], row["version"], row["author"]) for row in rows] == [
        ("production", "", 1, "migration"),
        ("sandbox", "", 1, "migration"),
    ]
    # Nulls stripped, the greeting as the words it was, and nothing the old row did not say.
    assert json.loads(rows[0]["config"]) == {
        "voice": "carolina",
        "llm": "anthropic/claude-haiku-4-5",
        "greeting": {"say": "Buenas."},
    }
    assert rows[0]["note"] == "pipeline_overrides, 0012"


async def test_0037_hands_words_to_the_keys_that_hold_the_floor_and_not_to_qa(
    a_box_from_before: Box,
) -> None:
    box = a_box_from_before
    await apply_migrations(box.dsn, schema=box.schema)
    scopes = {
        row["id"]: set(row["scopes"])
        for row in await box.connection.fetch("select id, scopes from api_keys")
    }
    assert "words" in scopes["k_floor"]
    assert "words" not in scopes["k_qa"]


async def test_0040_takes_the_old_table_one_release_after_0037_stopped_reading_it(
    a_box_from_before: Box,
) -> None:
    """A table is dropped in two migrations, the code first: 0037 read it, 0040 drops it."""
    box = a_box_from_before
    await apply_migrations(box.dsn, schema=box.schema)
    gone = await box.connection.fetchval(
        "select to_regclass($1)", f"{box.schema}.pipeline_overrides"
    )
    assert gone is None


async def test_0040_renames_the_bases_a_world_attached_and_leaves_a_row_with_none_alone(
    a_box_from_before: Box,
) -> None:
    """0037 kept the bases under `knowledge`; that word is the business text now, the RAG bases."""
    box = a_box_from_before
    # Up to 0039 by hand, as the fixture applied the ones before 0037: the row is written in the
    # shape 0037 kept it, and 0040 is what is under test.
    between = [name for name in migrations_before("0040") if name >= "0037"]
    await applied_by_hand(box.connection, between)
    await box.connection.execute(
        "insert into agent_config (org, env, holder, agent, version, config, author) "
        "values ($1, 'sandbox', '', $2, 2, $3, 'k_1')",
        THE_ORG,
        THE_AGENT,
        json.dumps({"voice": "carolina", "knowledge": [{"base": "clinica", "k": 4}]}),
    )
    await apply_migrations(box.dsn, schema=box.schema)
    rows = await box.connection.fetch(
        "select version, config from agent_config "
        "where org = $1 and env = 'sandbox' order by version",
        THE_ORG,
    )
    assert json.loads(rows[0]["config"]) == {
        "voice": "carolina",
        "llm": "anthropic/claude-haiku-4-5",
        "greeting": {"say": "Buenas."},
    }
    assert json.loads(rows[1]["config"]) == {
        "voice": "carolina",
        "bases": [{"base": "clinica", "k": 4}],
    }

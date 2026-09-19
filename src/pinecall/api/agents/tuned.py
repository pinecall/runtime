"""What one session is built on: the corner's tuning and lexicon, read now, over the declaration."""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass

from pinecall.knowledge import Knowledge
from pinecall.orgs.tuning import TuningStore
from pinecall.providers.tuning import tuned
from pinecall.types import AgentConfig, Env, KnowledgeFile, Lexicon, Tuning, Versions

# What the whole files of several bases are joined with, in the one block the model reads.
BETWEEN_FILES = "\n\n"


@dataclass(frozen=True)
class Tuned:
    """The config the session runs, and which versions it was built on."""

    config: AgentConfig
    versions: Versions


# Read PER SESSION and never held: the store is one indexed row away, calls are seconds apart,
# and what an operator sets on one gateway is on the next call of every other one without any
# gateway being told. The worker reaches this twice for one call — the config hop that builds the
# session, and POST /v1/calls that records the versions — so a set landing in between records n+1
# for a session built on n. Said here, and not cached: a cache is a second place the truth lives.
async def tuned_for(
    kept: TuningStore,
    org: str,
    env: Env,
    holder: str | None,
    slug: str,
    declared: AgentConfig,
    knowledge: Knowledge | None = None,
) -> Tuned:
    """The corner's newest tuning and lexicon, else the org's own, laid over what the app said."""
    row = await kept.newest(org, env, holder, slug)
    words = await kept.newest_lexicon(org, env, holder)
    config = tuned(
        declared,
        Tuning() if row is None else row.value,
        Lexicon() if words is None else words.value,
    )
    config = await with_the_whole_files(config, org, env, holder, knowledge)
    return Tuned(
        config,
        Versions(
            config=None if row is None else row.version,
            lexicon=None if words is None else words.version,
        ),
    )


# The file the class used to carry by heart is a document of the base now, kept whole (0038):
# the whole files of every attached base go where that file went, joined, and the class's own
# stands only while nothing is attached that has one. The world wins here too.
async def with_the_whole_files(
    config: AgentConfig, org: str, env: Env, holder: str | None, knowledge: Knowledge | None
) -> AgentConfig:
    """The config with its knowledge block read off the attached bases' whole files, when any."""
    if knowledge is None or not config.bases:
        return config
    files: list[KnowledgeFile] = []
    for docs in config.bases:
        files.extend(await knowledge.whole_texts(org, env, holder, docs.base))
    if not files:
        return config
    joined = BETWEEN_FILES.join(file.text for file in files)
    return dataclasses.replace(config, knowledge=KnowledgeFile(files[0].path, joined, "whole"))

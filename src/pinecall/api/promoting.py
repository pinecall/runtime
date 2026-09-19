"""Promote: a corner's newest to the team's, and the team's sandbox to production, goldens first."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import Field

from pinecall.api._deps import (
    LlmsDep,
    LogsDep,
    LookupsDep,
    PipelineKeyDep,
    RunsDep,
    SettingsDep,
    StoreDep,
    TuningDep,
    VaultDep,
)
from pinecall.api._live import LiveDep
from pinecall.api.agents.registry import RegistryDep
from pinecall.api.evals.runner import (
    AlreadyRunning,
    NobodyServing,
    Process,
    RunnerDep,
    Wanted,
    a_run,
)
from pinecall.api.tuning import TuningKeyDep, differing
from pinecall.auth.corner import author_of
from pinecall.auth.keys import KeyRecord, held_by
from pinecall.evals.goldens import Golden
from pinecall.orgs.tuning import TuningStore
from pinecall.providers.models import NoProvider
from pinecall.types import PRODUCTION, SANDBOX, THE_ORGS_OWN, DeclarationRefused, is_a_deployment
from pinecall_protocol import WireModel
from pinecall_protocol.rest import Promoted

router = APIRouter()

NO_TEAM_IN_PRODUCTION = "production has one corner, the org's own: there is no team to promote to"
THE_ORGS_OWN_ALREADY = "this key writes the org's own corner already: what it sets is the team's"
NOTHING_OF_YOURS = "your corner set nothing for {slug}: there is nothing to promote to the team"
FROM_THE_SANDBOX = "production is promoted from the sandbox, and this key's world is production"
NOTHING_TO_PROMOTE = (
    "the team set nothing for {slug} in the sandbox: promote yours to the team first"
)
NO_GOLDENS = (
    "promoting to production runs the agent's goldens first, and none were sent: "
    "`pinecall test` finds them beside the agent"
)
YOURS_DIFFERS = (
    "your own corner sets {slug} differently from the team's ({fields}): promote yours to the "
    "team first, or clear it, so the goldens test what production will run"
)
DID_NOT_HOLD = (
    "{failed} of {total} goldens did not hold under the team's sandbox settings ({names}): "
    "production stays as it is; eval run {run} has the detail"
)
RUN_BROKE = (
    "the eval run did not finish: {why}. Production stays as it is; run {run} has the detail"
)


class Promotion(WireModel):
    """What a promote asks: which hop, the goldens that gate the production one, and why."""

    to: Literal["team", "production"]
    # The goldens travel in the body, as POST /v1/evals/run takes them: they are the project's and
    # the gateway keeps none. `pinecall agent promote --prod` reads them where `pinecall test` does.
    goldens: list[Golden] = Field(default_factory=list[Golden])
    note: str | None = None


class LexiconPromotion(WireModel):
    """A lexicon promote: which hop, and why. No goldens: a word said wrong breaks nothing."""

    to: Literal["team", "production"]
    note: str | None = None


# Two hops, one verb. To the team: the corner's newest becomes the org's own next version, and
# every colleague's next call reads it. To production: the goldens are driven first — against the
# app serving this key's sandbox corner, under the team's settings — and only when every one holds
# does the team's newest become production's next version. Nobody writes production any other way.
@router.post("/v1/agents/{slug}/settings/promote")
async def promote(
    slug: str,
    said: Promotion,
    key: PipelineKeyDep,
    registry: RegistryDep,
    kept: TuningDep,
    runner: RunnerDep,
    llms: LlmsDep,
    logs: LogsDep,
    live: LiveDep,
    store: StoreDep,
    runs: RunsDep,
    vault: VaultDep,
    lookups: LookupsDep,
    settings: SettingsDep,
) -> Promoted:
    """Your corner to the team's, or the team's sandbox to production once the goldens hold."""
    if said.to == "team":
        return await _to_the_team(slug, said.note, key, kept)
    if key.env != SANDBOX:
        raise HTTPException(409, FROM_THE_SANDBOX)
    team = await kept.own(key.org, SANDBOX, THE_ORGS_OWN, slug)
    if team is None:
        raise HTTPException(404, NOTHING_TO_PROMOTE.format(slug=slug))
    if not said.goldens:
        raise HTTPException(400, NO_GOLDENS)
    mine = held_by(key)
    own = None if mine is None else await kept.own(key.org, SANDBOX, mine, slug)
    if own is not None and (fields := differing(own, team)):
        raise HTTPException(409, YOURS_DIFFERS.format(slug=slug, fields=", ".join(fields)))
    process = Process(
        registry=registry,
        tuning=kept,
        llms=llms,
        logs=logs,
        live=live,
        store=store,
        runs=runs,
        env=SANDBOX,
        holder=mine,
        vault=vault,
        lookups=lookups,
        budgets=settings.budgets,
        settings=settings,
    )
    try:
        run = await a_run(Wanted(agent=slug, goldens=said.goldens), runner, process)
    except AlreadyRunning as running:
        raise HTTPException(409, str(running)) from running
    except NobodyServing as nobody:
        raise HTTPException(404, str(nobody)) from nobody
    except (DeclarationRefused, NoProvider) as refused:
        raise HTTPException(400, str(refused)) from refused
    if run.status != "done":
        raise HTTPException(409, RUN_BROKE.format(why=run.error or run.status, run=run.id))
    failures = [] if run.matrix is None else list(run.matrix.get("failures", []))
    if failures:
        names = sorted({str(one["golden"]) for one in failures})
        raise HTTPException(
            409,
            DID_NOT_HOLD.format(
                failed=len(names), total=len(said.goldens), names=", ".join(names), run=run.id
            ),
        )
    version = await kept.put(
        key.org,
        PRODUCTION,
        THE_ORGS_OWN,
        slug,
        team.value,
        author=author_of(key),
        note=said.note or f"promoted from sandbox v{team.version}",
        if_version=None,
    )
    return Promoted(world=PRODUCTION, holder=THE_ORGS_OWN, version=version, run=run.id)


async def _to_the_team(slug: str, note: str | None, key: KeyRecord, kept: TuningStore) -> Promoted:
    """This key's own corner's newest, as the org's own corner's next version."""
    if is_a_deployment(key.env):
        raise HTTPException(409, NO_TEAM_IN_PRODUCTION)
    mine = held_by(key)
    if mine is None:
        raise HTTPException(409, THE_ORGS_OWN_ALREADY)
    row = await kept.own(key.org, key.env, mine, slug)
    if row is None:
        raise HTTPException(404, NOTHING_OF_YOURS.format(slug=slug))
    version = await kept.put(
        key.org,
        key.env,
        THE_ORGS_OWN,
        slug,
        row.value,
        author=author_of(key),
        note=note or f"promoted from {mine} v{row.version}",
        if_version=None,
    )
    return Promoted(world=key.env, holder=THE_ORGS_OWN, version=version, run=None)


# The lexicon's two hops, with no goldens between them: a word said wrong is what the agent
# already does, and the person who hears it is the one this door is for.
@router.post("/v1/lexicon/promote")
async def promote_lexicon(said: LexiconPromotion, key: TuningKeyDep, kept: TuningDep) -> Promoted:
    """Your corner's lexicon to the team's, or the team's sandbox lexicon to production."""
    if said.to == "team":
        if is_a_deployment(key.env):
            raise HTTPException(409, NO_TEAM_IN_PRODUCTION)
        mine = held_by(key)
        if mine is None:
            raise HTTPException(409, THE_ORGS_OWN_ALREADY)
        row = await kept.own_lexicon(key.org, key.env, mine)
        if row is None:
            raise HTTPException(404, NOTHING_OF_YOURS.format(slug="the lexicon"))
        world, note = key.env, said.note or f"promoted from {mine} v{row.version}"
    else:
        if key.env != SANDBOX:
            raise HTTPException(409, FROM_THE_SANDBOX)
        row = await kept.own_lexicon(key.org, SANDBOX, THE_ORGS_OWN)
        if row is None:
            raise HTTPException(404, NOTHING_TO_PROMOTE.format(slug="the lexicon"))
        world, note = PRODUCTION, said.note or f"promoted from sandbox v{row.version}"
    version = await kept.put_lexicon(
        key.org, world, THE_ORGS_OWN, row.value, author=author_of(key), note=note, if_version=None
    )
    return Promoted(world=world, holder=THE_ORGS_OWN, version=version, run=None)

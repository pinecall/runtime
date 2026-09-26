"""/v1/apps: the processes holding an org's agents right now — where each runs — and a stop."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from pinecall.api.agents.processes import Process, ProcessesDep
from pinecall.api.agents.registry_reads import named_holder
from pinecall.api.deps import AppKeyDep, CallsKeyDep, MembersDep, RegistryDep
from pinecall.auth.keys import KeyRecord, is_held_by, is_operator_key
from pinecall_protocol.rest import AppList, AppProcess, AppStopped

router = APIRouter()

# Nothing of THIS org, in this world, that this key may see answers to that id: the same 404
# whether the socket is another org's, another world's, a colleague's corner, or long gone.
NO_SUCH_APP = "no app {app} is connected here that this key may stop"


def _visible(key: KeyRecord, process: Process) -> bool:
    """A key sees its own corner's apps and the org's; one that opens `team` sees every corner's."""
    return is_operator_key(key) or process.holder in (None, is_held_by(key))


# Where an org's agents are running is the question nobody could answer: the agent listing says
# which slugs are held, and not by how many processes, on which machine, on which SDK. This is
# that answer, one row a socket — a laptop left running in production shows up here beside the
# server, and a stop makes it exit. A process a supervisor keeps up (systemd, pm2, a container)
# is started again by it: that one is stopped where it runs.
@router.get("/v1/apps")
async def apps(
    key: CallsKeyDep, processes: ProcessesDep, registry: RegistryDep, members: MembersDep
) -> AppList:
    """Every app connected in the request's world that this key may see, oldest first."""
    listed: list[AppProcess] = []
    for one in processes.of_org(key.org, key.env):
        if not _visible(key, one):
            continue
        held = registry.owned_by(one.app)
        listed.append(
            AppProcess(
                app=one.app,
                agents=[registration.slug for registration in held],
                env=one.env,
                host=one.host,
                address=one.address,
                sdk=next((r.sdk for r in held if r.sdk is not None), None),
                holder=None
                if one.holder is None
                else await named_holder(key.org, one.holder, members),
                connected_at=one.connected_at,
            )
        )
    return AppList(apps=listed)


# `app` and not `calls`: stopping a process is the act of whoever may hold one. Production is a
# request that named it, which the world check already let through only for somebody their org
# lets act there (auth/env.py) — or for a production server's own token.
@router.post("/v1/apps/{app}/stop")
async def stop(app: str, key: AppKeyDep, processes: ProcessesDep) -> AppStopped:
    """Tell that app it was stopped, and close its socket: it exits instead of reconnecting."""
    found = processes.of(app)
    if found is None or found.org != key.org or found.env != key.env or not _visible(key, found):
        raise HTTPException(404, NO_SUCH_APP.format(app=app))
    await found.stop(f"stopped by {key.name or key.label or 'a server of the org'}")
    return AppStopped(app=app, stopped=True)

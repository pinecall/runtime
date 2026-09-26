"""The agent asked into a simulated call's room: one dispatch, held for the call, then hung up."""

from __future__ import annotations

import json
import logging

from livekit import api
from livekit.protocol.agent_dispatch import CreateAgentDispatchRequest
from livekit.protocol.room import DeleteRoomRequest

from pinecall.settings import Settings
from pinecall.types import PRODUCTION, Env
from pinecall.types.dispatch import (
    ACCEPTS_KEY,
    AGENT_KEY,
    APP_KEY,
    CALLER_KEY,
    DECLINES_KEY,
    ENV_KEY,
    HOLDER_KEY,
    ORG_KEY,
    PERSONA_KEY,
    RUN_KEY,
)

logger = logging.getLogger(__name__)


class Dispatch:
    """One `create_dispatch`, held open for the call and closed with the API client after it."""

    def __init__(
        self,
        call: str,
        agent: str,
        settings: Settings,
        caller: str | None,
        run: str | None,
        persona: str | None,
        app: str | None,
        org: str,
        env: Env,
        holder: str | None,
        *,
        accepts_when: str | None = None,
        declines_when: str | None = None,
    ) -> None:
        self._call = call
        self._agent = agent
        self._settings = settings
        self._caller = caller
        self._run = run
        self._persona = persona
        self._rule = (accepts_when, declines_when)
        self._app = app
        self._whose = (org, env, holder)
        self._api: api.LiveKitAPI | None = None

    # The dispatch is what puts the agent in the room: a room job whose metadata names the agent,
    # which is the door `worker/job_target.py:arrival_of` reads first and the one an outbound call
    # already arrives through. Nothing here dials anything and no SIP leg is waited for.
    async def __aenter__(self) -> None:
        self._api = api.LiveKitAPI(
            self._settings.livekit_url,
            self._settings.livekit_api_key,
            self._settings.livekit_api_secret,
        )
        await self._api.agent_dispatch.create_dispatch(
            CreateAgentDispatchRequest(
                room=self._call,
                agent_name=self._settings.fleet,
                metadata=json.dumps(self._metadata()),
            )
        )

    # A caller, a run and a persona are named only when there is a reason to. Ring 2 says which run
    # opened the call, so the worker and the app treat it as a written eval call: no greeting, the
    # golden's state seeded. A `simulate --voice` names the persona it is playing, and nothing
    # else; a room somebody made by hand names none of the three, and the router falls back to it.
    def _metadata(self) -> dict[str, str]:
        """What the dispatch tells the worker: the agent, the caller, and which run opened it."""
        # Whose call it is, the same three words `POST /v1/tokens` writes: the one worker every
        # org shares resolves the agent, the org's provider keys and the log in THIS corner. A
        # dispatch that named only the agent sent the worker looking in its own org — the box's,
        # which holds nobody's agents — and every simulated call but org `default`'s died with
        # NoRoute (2026-09-16, the first spoken call of another org's agent).
        org, env, holder = self._whose
        said = {AGENT_KEY: self._agent, ORG_KEY: org, ENV_KEY: env}
        if holder is not None:
            said[HOLDER_KEY] = holder
        if self._caller is not None:
            said[CALLER_KEY] = self._caller
        if self._run is not None:
            said[RUN_KEY] = self._run
        # The one road the persona's name has to the worker, which is what writes call.started —
        # and the caller's own rule for the call takes it too, for the judge that reads it there.
        if self._persona is not None:
            said[PERSONA_KEY] = self._persona
        accepts_when, declines_when = self._rule
        if accepts_when:
            said[ACCEPTS_KEY] = accepts_when
        if declines_when:
            said[DECLINES_KEY] = declines_when
        if self._app is not None:
            said[APP_KEY] = self._app
        return said

    # A caller leaving is not a hangup. The agent is still seated and its job still running, so
    # `add_shutdown_callback` never fires — and that callback is where the log is SEALED and the
    # recording's path is stated (worker/job.py:95,191). The pointer lives in `call.summary` and
    # nowhere else (api/calls/recording.py:17), so every simulated call left its audio on disk and
    # out of reach: 1.6 MB of ogg on the box, answered with "has no call.summary yet". A phone call
    # ends itself when the leg hangs up and livekit closes the room; a simulation has to hang up.
    async def __aexit__(self, *_closed: object) -> None:
        if self._api is None:
            return
        try:
            await self._api.room.delete_room(DeleteRoomRequest(room=self._call))
        except api.TwirpError:
            # Already gone: the agent hung up first, which seals the call by the same door.
            logger.debug("room %s was closed before the caller hung up", self._call)
        finally:
            await self._api.aclose()


def dispatch_agent(
    call: str,
    agent: str,
    settings: Settings,
    caller: str | None = None,
    run: str | None = None,
    persona: str | None = None,
    app: str | None = None,
    org: str = "",
    env: Env = PRODUCTION,
    holder: str | None = None,
    *,
    accepts_when: str | None = None,
    declines_when: str | None = None,
) -> Dispatch:
    """The agent asked into this room for the length of the call."""
    return Dispatch(
        call,
        agent,
        settings,
        caller,
        run,
        persona,
        app,
        org,
        env,
        holder,
        accepts_when=accepts_when,
        declines_when=declines_when,
    )

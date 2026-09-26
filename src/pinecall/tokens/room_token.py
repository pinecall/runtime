"""What a token tells the room: dispatch our worker to one agent, with what the tenant sealed."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from google.protobuf.json_format import ParseDict, ParseError
from livekit.protocol.agent_dispatch import RoomAgentDispatch
from livekit.protocol.room import RoomConfiguration

from pinecall.types import DeclarationRefused, Env
from pinecall.types.dispatch import (
    AGENT_KEY,
    CALLER_KEY,
    ENV_KEY,
    HOLDER_KEY,
    METADATA_KEY,
    ORG_KEY,
    SCOPE_KEY,
)

# A room_config that is not one, in the parser's own words: the door answers 400 with them.
NOT_A_ROOM_CONFIG = "room_config is not a LiveKit RoomConfiguration: {reason}"


# The room config rides inside the JWT (`roomConfig`), signed with the rest, and livekit creates
# the dispatch from it when the participant's join creates the room (agent-dispatch docs, "Dispatch
# via token"). So the agent, the scope, the visitor and the sealed JSON reach the worker's router
# through the one field a dispatch already has, and a browser can alter none of them. Whose the
# call is rides the same way: the org and the world the minting key opens, and the corner it
# holds — so the one worker every org shares resolves the agent, the keys and the log of THIS
# org, and a sandbox visit lands in the developer's own corner and not the org's. The fleet is the
# instance's own (`settings.fleet`): the SFU is shared, and the name is what keeps a call here.
def build_dispatch(
    fleet: str,
    agent: str,
    scope: str,
    caller: str,
    metadata: Mapping[str, Any],
    org: str,
    env: Env,
    holder: str | None = None,
) -> RoomConfiguration:
    """The room config the token carries: one dispatch, to this fleet, naming this agent."""
    said: dict[str, Any] = {
        AGENT_KEY: agent,
        SCOPE_KEY: scope,
        CALLER_KEY: caller,
        METADATA_KEY: dict(metadata),
        ORG_KEY: org,
        ENV_KEY: env,
    }
    if holder is not None:
        said[HOLDER_KEY] = holder
    dispatch = RoomAgentDispatch(agent_name=fleet, metadata=json.dumps(said, separators=(",", ":")))
    return RoomConfiguration(agents=[dispatch])


# A stock livekit-client names the agent as `agentName` on TokenSource.fetch, and packages it
# into room_config.agents[0].agent_name before the request leaves (frontends/build/authentication/
# endpoint: "The client SDKs automatically package agent information … into room_config"). This
# is how a client that knows nothing of Pinecall still says which agent it came for. protobuf's
# own parser reads either spelling of the key, so neither is spelled here.
def client_named_agent(room_config: Mapping[str, Any] | None) -> str | None:
    """`agentName` as the client SDKs send it, or None when the body named no agent that way."""
    if not room_config:
        return None
    try:
        parsed = ParseDict(dict(room_config), RoomConfiguration(), ignore_unknown_fields=True)
    except ParseError as malformed:
        raise DeclarationRefused(NOT_A_ROOM_CONFIG.format(reason=malformed)) from malformed
    agents = list(parsed.agents)
    return (agents[0].agent_name or None) if agents else None

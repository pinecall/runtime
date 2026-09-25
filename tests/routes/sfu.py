"""One SFU's SIP lists in memory, shared by every LiveKitAPI the code under test opens on it."""

from __future__ import annotations

from typing import Any, Self

from livekit import api


class TheSfu:
    """Stands in for `api.LiveKitAPI`: every client constructed is this one, and it keeps what it
    was asked to make — the trunks both ways, the dispatch rules, the dispatches — by name."""

    def __init__(self) -> None:
        self.inbound: list[api.SIPInboundTrunkInfo] = []
        self.outbound: list[api.SIPOutboundTrunkInfo] = []
        self.rules: list[api.SIPDispatchRuleInfo] = []
        self.dispatched: list[api.CreateAgentDispatchRequest] = []
        # The two services the runtime reaches through, both answered by this one object.
        self.sip = self
        self.agent_dispatch = self

    def __call__(self, *_args: object, **_kwargs: object) -> Self:
        return self

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *_exc: object) -> None:
        return None

    def inbound_named(self, name: str) -> api.SIPInboundTrunkInfo | None:
        """The inbound trunk by that name, as a person reading the SFU's list would find it."""
        return next((trunk for trunk in self.inbound if trunk.name == name), None)

    async def list_inbound_trunk(self, _asked: Any) -> api.ListSIPInboundTrunkResponse:
        return api.ListSIPInboundTrunkResponse(items=self.inbound)

    async def create_inbound_trunk(
        self, asked: api.CreateSIPInboundTrunkRequest
    ) -> api.SIPInboundTrunkInfo:
        made = api.SIPInboundTrunkInfo()
        made.CopyFrom(asked.trunk)
        made.sip_trunk_id = f"ST_in_{len(self.inbound)}"
        self.inbound.append(made)
        return made

    async def update_inbound_trunk(self, trunk_id: str, info: api.SIPInboundTrunkInfo) -> None:
        standing = next(trunk for trunk in self.inbound if trunk.sip_trunk_id == trunk_id)
        standing.CopyFrom(info)
        standing.sip_trunk_id = trunk_id

    async def list_dispatch_rule(self, _asked: Any) -> api.ListSIPDispatchRuleResponse:
        return api.ListSIPDispatchRuleResponse(items=self.rules)

    async def create_dispatch_rule(self, asked: api.CreateSIPDispatchRuleRequest) -> None:
        self.rules.append(
            api.SIPDispatchRuleInfo(
                name=asked.name, trunk_ids=asked.trunk_ids, room_config=asked.room_config
            )
        )

    async def list_outbound_trunk(self, _asked: Any) -> api.ListSIPOutboundTrunkResponse:
        return api.ListSIPOutboundTrunkResponse(items=self.outbound)

    async def create_outbound_trunk(
        self, asked: api.CreateSIPOutboundTrunkRequest
    ) -> api.SIPOutboundTrunkInfo:
        made = api.SIPOutboundTrunkInfo()
        made.CopyFrom(asked.trunk)
        made.sip_trunk_id = f"ST_out_{len(self.outbound)}"
        self.outbound.append(made)
        return made

    async def create_dispatch(self, asked: api.CreateAgentDispatchRequest) -> None:
        self.dispatched.append(asked)

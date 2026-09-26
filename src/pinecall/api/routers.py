"""Every door of the gateway, in the order the app includes them."""

from __future__ import annotations

from fastapi import APIRouter

from pinecall.api import discovery
from pinecall.api.accounts import (
    api_keys,
    google_login,
    login,
    members,
    membership,
    org_sso,
    org_switch,
    pairing,
    password_reset,
    signup,
    sso_login,
    whoami,
)
from pinecall.api.agents import (
    apps,
    dev,
    hangup_judging,
    hold_melody,
    lexicon,
    pipeline,
    registry_reads,
    socket,
    tuning,
    voices,
    widget,
)
from pinecall.api.agents import provider_keys as agents_provider_keys
from pinecall.api.calls import (
    chat,
    codes,
    commands,
    events,
    inbox,
    listen,
    listing,
    live_calls,
    lookup,
    recording,
    room_token,
    state,
    tools,
    worker_writes,
)
from pinecall.api.calls.supervise import seat, verbs
from pinecall.api.evals import caller, judge, persona_runs, personas, replay, runs, voice
from pinecall.api.knowledge import bases
from pinecall.api.memory import agent_wide, contacts, extraction
from pinecall.api.ops import box_brand, box_mail, box_signin, fleet, orgs, providers, routes
from pinecall.api.ops import live_calls as ops_live_calls
from pinecall.api.ops import members as ops_members
from pinecall.api.org import insights, limits, mail, provider_keys, usage
from pinecall.api.telephony import dial_out, managed_numbers, numbers, outbound_trunk
from pinecall.api.whatsapp import webhook

# One door per line, in the order a reader meets them: the app's socket and the calls it answers,
# the desk and the suites, the tenant's own tables, the operator's under /v1/ops, and last the
# org's people — who they are, how they sign in, and whose key just knocked.
DOORS: tuple[APIRouter, ...] = (
    socket.router,
    registry_reads.router,
    agents_provider_keys.router,
    dev.router,
    personas.router,
    persona_runs.router,
    events.router,
    worker_writes.router,
    state.router,
    listing.router,
    recording.router,
    chat.router,
    tools.router,
    commands.router,
    lookup.router,
    verbs.router,
    replay.router,
    judge.router,
    runs.router,
    caller.router,
    voice.router,
    routes.router,
    routes.operator,
    api_keys.router,
    apps.router,
    provider_keys.router,
    providers.router,
    voices.router,
    orgs.operator,
    provider_keys.operator,
    usage.operator,
    usage.router,
    fleet.router,
    fleet.operator,
    pipeline.router,
    hold_melody.router,
    tuning.router,
    lexicon.router,
    widget.router,
    room_token.router,
    codes.router,
    listen.router,
    seat.router,
    webhook.router,
    bases.router,
    contacts.router,
    agent_wide.router,
    extraction.router,
    members.router,
    membership.router,
    ops_members.operator,
    login.router,
    org_switch.router,
    password_reset.router,
    sso_login.router,
    google_login.router,
    org_sso.router,
    org_sso.operator,
    mail.router,
    box_mail.operator,
    box_brand.operator,
    box_signin.operator,
    pairing.router,
    live_calls.router,
    ops_live_calls.operator,
    inbox.router,
    insights.router,
    limits.router,
    hangup_judging.router,
    numbers.router,
    managed_numbers.router,
    outbound_trunk.router,
    dial_out.router,
    signup.router,
    whoami.router,
    whoami.operator,
    discovery.router,
)

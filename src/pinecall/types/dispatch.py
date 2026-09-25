"""What a dispatch says to a worker: the name the fleet answers to, and the keys of its metadata."""

from dataclasses import dataclass

# The name a fleet's workers register with the media plane under when the instance names none
# (`PINECALL_FLEET`, _settings.py). Every reader takes the instance's `settings.fleet`, never this:
# the gateway WRITES the fleet into a token's room config, a trunk's rule and a dispatch, and the
# worker REGISTERS under it, so the two instances on one SFU — production and the sandbox — each
# dispatch to their own workers only. In livekit's words it is the `agent_name`; in ours it is a
# fleet's worker pool, never a tenant's agent — those are named inside the metadata below.
DEFAULT_FLEET = "pinecall"
# How a fleet is spelled: as a slug is, because the name ends up where only that alphabet is
# allowed — a Twilio credential username and termination label, an SFU trunk name — and because an
# EMPTY agent_name is implicit dispatch to every room in the deployment (livekit worker.py:219):
# somebody else's call, answered by us. Held by Settings, so a process that spelled it wrong never
# starts.
A_FLEET_NAME = r"^[a-z0-9][a-z0-9-]{0,62}$"

# The keys of a dispatch's metadata: written by whoever creates the dispatch — the token door for a
# web call, the outbound verb for a call we place — and read by the worker's router, in this order
# of certainty. A dispatch names the agent on purpose, so the router has nothing to wait for.
AGENT_KEY = "agent"
DIRECTION_KEY = "direction"
CALLER_KEY = "caller"
# Which eval run opened this call, when one did: the worker greets nobody on it and the tenant's
# app seeds the golden's state into it. The same fact rides the call's first entry on the wire.
RUN_KEY = "run"
# Which synthetic caller a model is playing on this call, by name (GET /v1/personas), when a
# spoken simulation asked for it. The gateway holds the whole call (`POST /v1/evals/voice`) but
# the WORKER writes call.started, so the name travels the only road there is between them. The
# same fact rides the call's first entry on the wire, exactly as `run` does.
PERSONA_KEY = "persona"
# When that caller accepts the call and when it declines it, in the persona's own words, by the
# same road and for the same reason: the `persona` judge reads them off call.started at hang-up
# (evals/judges/persona.py), and the worker writes that entry. Carried on the call rather than
# read off the row when it ends, so a persona edited mid-run judges the call it made, not the
# next one — LiveKit's own simulations carry `agent_expectations` on the dispatch the same way.
ACCEPTS_KEY = "accepts_when"
DECLINES_KEY = "declines_when"
# Which app socket is to serve this call, when the dispatch has a reason to name one: a spoken
# eval run does, because the goldens and their seeded state live in the terminal that asked for
# the run, and that socket takes no unclaimed calls. Absent, the gateway picks as it always has.
APP_KEY = "app"
# Whose call this is: the org, the world and the corner the dispatch was made for. The token door
# writes all three from the key that minted the token (a sandbox person's key names their corner);
# a tenant's SIP rule writes the org, because a trunk is one org's. The worker holds ONE key for
# every org, so these are how its doors learn whose agent, whose keys and whose log a call is —
# never its own key's org. A dispatch that carries none is the box's own trunk, and the number
# dialled says whose it is instead.
ORG_KEY = "org"
ENV_KEY = "env"
HOLDER_KEY = "holder"

# Ours, beside livekit's three: which scope minted the token that opened this call, and the JSON
# the tenant's backend sealed into it. A dispatch that carries a scope was minted by POST
# /v1/tokens and is spent once; a phone call and a console carry none and are never checked.
SCOPE_KEY = "scope"
METADATA_KEY = "metadata"

# The scope a visit token carried, as POST /v1/tokens wrote it into the dispatch (SCOPE_KEY). The
# one value the worker reads: a `chat` visit is a written call — no ears, no voice, no audio in
# the room — and every other scope is spoken. The word is types/token.py's own row.
WRITTEN_SCOPE = "chat"

# What the box is to DIAL, on a call the gateway placed: the outbound trunk, the number, the one
# to show as the caller, and how long the call may run. Only the gateway writes a dispatch, so
# this is the worker's whole instruction — it asks no door for a trunk and invents no ceiling.
DIAL_KEY = "dial"

# Written on a production phone call the worker handed to a developer's sandbox copy, because the
# developer said the phone dialling is theirs (`pinecall line from`). The world it rang in: the
# log of the call says it was the real number, not a sandbox one.
DIVERTED_KEY = "diverted_from"


# Where a production ring from a developer's own phone goes instead: into their corner, built by
# the fleet that serves it. The fleet travels in the answer because only the instance that holds
# the corner knows its own name — production is never told what the sandbox calls its workers.
@dataclass(frozen=True)
class Handover:
    """A ring that is a developer's: whose corner takes it, and the fleet that answers there."""

    holder: str
    fleet: str

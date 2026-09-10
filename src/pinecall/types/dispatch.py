"""What a dispatch says to a worker: the name the fleet answers to, and the keys of its metadata."""

# The one name every worker of this runtime registers with the media plane under, and the one a
# dispatch asks for. It is spelled here, in the package both processes hold, because the gateway
# WRITES it into a token's room config and the worker REGISTERS under it: two spellings would be
# a token that dispatches to nobody. In livekit's words it is the `agent_name`; in ours it is the
# fleet's worker pool, never a tenant's agent — those are named inside the metadata below.
WORKER_NAME = "pinecall"

# The keys of a dispatch's metadata: written by whoever creates the dispatch — the token door for a
# web call, the outbound verb for a call we place — and read by the worker's router, in this order
# of certainty. A dispatch names the agent on purpose, so the router has nothing to wait for.
AGENT_KEY = "agent"
DIRECTION_KEY = "direction"
CALLER_KEY = "caller"
# Which app socket is to serve this call, when the dispatch has a reason to name one: a spoken
# eval run does, because the goldens and their seeded state live in the terminal that asked for
# the run, and that socket takes no unclaimed calls. Absent, the gateway picks as it always has.
APP_KEY = "app"

# Ours, beside livekit's three: which scope minted the token that opened this call, and the JSON
# the tenant's backend sealed into it. A dispatch that carries a scope was minted by POST
# /v1/tokens and is spent once; a phone call and a console carry none and are never checked.
SCOPE_KEY = "scope"
METADATA_KEY = "metadata"

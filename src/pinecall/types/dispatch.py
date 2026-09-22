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

"""The agent asking for a person: the refusal, and what the log says when nobody came."""

# A tool that asks for a person is running while the caller waits, so the app's own timeout for
# that tool has to outlast the wait it asked for. The runtime does not lengthen it: a wait nobody
# can sit through is the app's to shorten.
NOBODY_TOOK_IT = "nobody took the line within {wait_s:g}s"

# A second ask while one is open is the same ask: the caller is already waiting.
ALREADY_WAITING = "call.attention: this caller is already waiting for somebody to take the line"

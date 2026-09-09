# The confirmation gate — removed for now

**2026-09-06.** There was a server-side gate: `src/pinecall/confirm/` minted a
one-shot token bound to `sha(tool + canonical args)`, `session/text/gate.py` held the
call, read the template back to the caller, decided the answer with the word lists in
`lang/yesno.py` before falling back to a two-tool model call, and only then let the tool
through. It shipped in `9bb37cd` and `8c51c7a`, moved with the text session in `3a4119f`
and `e04e558`, and is gone as of this commit.

Bernardo, on reading `lang/yesno.py`: "esto no quiero hacerlo parte, ni lo del confirm, es
raro para lo que se ve actualmente, y no lo necesitamos, agrega complicaciones y hacks
como estos que no los deseo; prefiero eliminar toda la lógica del confirm, por ahora es
overwhelming."

So an irreversible tool now runs the way every other tool runs: the model calls it, the
platform emits `tool.call`, the app's own process answers, `tool.result` is written, and
the model reads the text back. Nothing is held, no turn is stopped, no token is spent.

**The wire never lost it.** `ToolSpec.confirm` is still a field an app declares and
`api/agents/declaration.py` still carries it into the domain, `domain/tool.py` still
refuses an irreversible tool with no confirm template, the three `confirm.*` events are
still in `protocol/`, `log/reduce.py` still folds them into `Confirm` rows,
`log/projection.py` still masks them, and `@tool({ confirm })` is still the option a
tenant writes. Nothing emits them today.

Bringing it back is therefore a runtime job only: a holder for the pending call, a token
with an audience and a TTL, and the read-back put into the model's history behind the
call's output. The contract it would speak is already there, unchanged.

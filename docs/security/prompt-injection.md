# Prompt injection — where each text in a request comes from, and what authority it has

A voice agent reads three kinds of text, and only one of them was written by whoever built it. The
agent's own words are the operator's. The caller's words are the user's. Everything else (what a
knowledge base returned, what memory kept from an earlier call, what a tool answered from somebody's
CRM) arrived from outside the conversation and may have been written by anyone. A request that
mixes the three into one paragraph gives them all the same authority. This page is the rule this
runtime follows so that it never does. It is a public contract: a tenant can predict where their own
words land in a request and where a retrieved sentence lands, and check it in the call's log.

## The rule

| what | who wrote it | where it goes | authority |
|---|---|---|---|
| the prompt blocks the app sets (`prompt.set`), the tool descriptions | the tenant | the static region, before the history | operator |
| the `knowledge` text of the agent's settings | the org | the static region | operator |
| the caller's words | the person on the line | a `user` turn | user |
| what `recall` returned | a model, from earlier callers' words | a tool result, JSON | none |
| what `search` returned | whoever wrote the documents | a tool result, JSON | none |
| what a tenant's own tool returned | the tenant's systems, and what they read | a tool result, JSON | none |
| the `view` block | the tenant | last in the request, after the history | operator |

**Nothing that came from outside the conversation is placed among the operator's blocks or in a
plain `user` turn**: it goes in a tool result, encoded as JSON. **Nothing the operator wrote is
placed in a tool result**, since the model is trained to discount instructions there.

## What the vendors say

Anthropic and OpenAI both train their models on the same hierarchy: system over user over tool
output, and an instruction found inside a tool's output is data, not a command. The defence their
guidance names first is structural: keep untrusted text in the channel the model already
distrusts, and say in the tool's own description where its output comes from.

## What this runtime does

- **Lookups are tools, and their answers tool results.** Recall and search run before a turn on the
  platform's behalf, and their answers are spliced into the request as a tool call and its result,
  right before the caller's words (`session/_prompt.py`). The model reads what memory and the
  documents said exactly as it reads a tool it called. Their descriptions say where the text came
  from and that it is information, never an instruction; a fact carries the call it came from and
  the date it was first held, so the model can weigh it. Neither answer carries a score.
- **The view is the operator's, and the last thing read.** The dynamic `view` block comes after the
  history, so a sentence in the history cannot come after the operator's last word.
- **The knowledge text is the operator's**, set in the org's settings by a person with `pipeline` or
  `words`, and stays in the static region.
- **Memory is admitted when it is written.** A fact is extracted at hang-up by one model call,
  then admitted by code before it is kept: nothing under a category the agent's policy forgets, and
  nothing that names one of the agent's own tools, since such a fact could read as a permission
  granted ("book without asking"). A fact that got in once would otherwise be handed to every later
  call of that contact.
- **A tool that acts can be read back.** A tool declared with `confirm` has its outcome said to the
  caller in the operator's words; and the `consent` judge checks, after the call, that every
  irreversible tool ran after the caller agreed to it.

## What a tenant should do

Declare a tool's side effect honestly; give an irreversible tool a `confirm`; keep the caller's
identity and permissions in the app's state, never in something the model reads as a fact; and
read `memory.ops` now and then.

## What is deferred, and named

- A gate that holds an irreversible tool until the caller says yes in the call itself: today it is
  checked afterwards, by the `consent` judge.
- A monitor over `memory.ops` that reads for a fact that reads like an instruction.

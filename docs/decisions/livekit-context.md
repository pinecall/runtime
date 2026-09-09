# livekit-agents 1.8.0 — the chat context on its way to the model

Invariants 1 to 6 of [livekit-1.8.md](livekit-1.8.md), which holds the verdict table, the
version they were read in and the rest of the chapters. Nothing here was reworded: it was moved.

## 1 · 2 · 3 — the chat context on its way to Anthropic

`ChatContext.to_provider_format("anthropic")` (`agents/llm/chat_context.py:720`) runs
`to_chat_ctx` (`agents/llm/_provider_format/anthropic.py:22`), which does three things in
this order, and every one of them matters to us.

**`convert_mid_conversation_instructions` first** (`anthropic.py:28`, defined at
`_provider_format/utils.py:49`). The **first** system or developer message is kept as the
preamble; **every later one is rewritten with `role="user"`**, wrapped in
`<instructions>…</instructions>` (`utils.py:10`). This is the reason for the milestone's
rule: **the date reaches the model as a paired tool call + result, never a system message.**
A system message appended mid-conversation does not arrive as a system message at all — it
arrives as a user turn, in the model's own transcript, and it drags the cache breakpoint
with it. In 1.7.1 this lived in the Google/AWS formatters; in 1.8 it is the shared helper
every JSON-object provider calls.

**`group_tool_calls` second** (`anthropic.py:36`, defined at `utils.py:93`). Items are
grouped by `id` (or `FunctionCall.group_id` for a parallel batch, `utils.py:117`); an output
whose `call_id` matches no call is dropped with a warning (`utils.py:134`), and
`remove_invalid_tool_calls` (`utils.py:167`) drops a call whose output never arrived and an
output whose call was itself dropped. **We never sanitise a chat context ourselves.** The
framework already refuses to send an orphan half of a pair, which is exactly the Anthropic
400 our v1 used to hit.

**The system messages are collected out of the item list** (`anthropic.py:40`) and sent as
`extra["system"]`, a list of text blocks in order (`plugins/anthropic/llm.py:226`). With
`caching="ephemeral"` the breakpoint lands on the **last** block of that list
(`llm.py:234`), on the last tool (`:238`), on the last assistant message and on the last
user message before it (`:247,:251`). Hence the agents repo's `docs/decisions/prompt-regions.md`: anything
that moves every turn must sit **after** the cached prefix, never inside it.

## 4 · 5 · 6 — instructions, tools, and the two new history items

The static region is one pinned item: `INSTRUCTIONS_MESSAGE_ID = "lk.agent_task.instructions"`
(`agents/voice/generation.py:1225`, commented *value must not change*). `update_instructions`
(`:1232`) replaces that item **in place** when it exists (`:1249`) and otherwise inserts it at
index 0 (`:1262`) — so the system prefix is always the first item and always the same item.
`Instructions` are rendered to a plain string with `modality="audio"` by default (`:1237`),
which is what a voice session gets.

`Agent.update_instructions` (`agents/voice/agent.py:185`) and `update_tools` (`:205`) mutate
the agent when no activity is running and otherwise delegate to
`AgentActivity.update_instructions` (`agent_activity.py:597`) / `update_tools` (`:614`).
**New in 1.8:** both now insert an `llm.AgentConfigUpdate` item into the agent's *and* the
session's chat context (`:604`, `:631`), and a third one records the initial configuration at
activity start (`:1155`). `update_tools` additionally re-copies the whole chat context
through `update_chat_ctx` (`:639`), which re-runs the tool-validity filter.

Cache impact is unchanged and worth restating: **rewriting the instructions rewrites the
cached prefix, so the next request pays a cache write.** `render(state)` must therefore go
to the dynamic region at the end of the request, not into `update_instructions`, unless the
static region genuinely changed.

`AgentConfigUpdate` (`agents/llm/chat_context.py:395`) and `AgentHandoff` (`:387`) are members
of the `ChatItem` union (`:409`) — so they are in `session.history` and in anything we
serialise — but `_ChatItemGroup.add` has no branch for them (`_provider_format/utils.py:157`)
and `flatten` returns nothing for such a group (`:199`). **They are recorded and never sent.**
Our log may carry them; the model never sees them.

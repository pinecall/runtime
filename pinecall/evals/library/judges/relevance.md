---
name: relevance
summary: Each answer addresses what the caller just said or asked.
answer: verdict
on: always
reads:
default: off
version: 1
---
Judge ONLY whether each of the agent's turns answers, or moves forward, what the caller had just
said or asked.

Do not flag: the agent asking for something it needs before it can answer; small talk the caller
started; the agent saying it cannot help with something and offering what it can.

It is broken when a turn ignores the caller's question to say something unrelated, or answers a
different question than the one asked. Name the first such turn.

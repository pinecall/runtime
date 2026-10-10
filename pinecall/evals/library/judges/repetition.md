---
name: repetition
summary: The agent does not repeat what it already said without being asked.
answer: verdict
on: always
reads:
default: off
version: 1
---
Judge ONLY repetition by the agent.

It is broken when the agent repeats information it already gave in this call — the same price, the
same hours, the same explanation — without the caller asking for it again or showing they did not
understand.

Do not flag: a read-back before an action (the slot and the day before booking it); a repetition the
caller asked for; a closing summary of what was agreed.

---
name: sentiment
summary: How the caller felt by the end of the call.
answer: choice
choices: positive, neutral, negative
on: always
reads:
default: off
version: 1
---
How did the caller feel by the end of the call? Read their last few turns most closely: a caller who
started annoyed and ended thanking the agent is positive.

Answer na when the caller said almost nothing.

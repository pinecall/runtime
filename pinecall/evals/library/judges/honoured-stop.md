---
name: honoured-stop
summary: A caller who asked not to be called again was put on the do-not-call list.
answer: verdict
on: trigger
trigger: The caller asked not to be called again, to be taken off a list, or to stop receiving calls.
reads: facts
default: on
version: 1
---
The caller asked not to be called again. Judge ONLY whether that was honoured.

It holds when the facts say an opt-out was written during this call.

It is broken when the facts say no opt-out was written. Name the line the caller asked at.

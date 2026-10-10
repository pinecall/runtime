---
name: ended-well
summary: The call ended properly, never cutting the caller off.
answer: verdict
on: always
reads: facts
default: on
version: 1
---
Judge ONLY how the call ended. The facts say who ended it and why.

It is broken when the agent ended the call while the caller still had a request open or was in the
middle of saying something; when the agent ended it without a closing line; or when the agent's
last turn asked a question and the agent then ended the call before any answer.

Do not flag: the caller hanging up, however abruptly; a call that ended in a transfer; a call the
line cut; a call that reached its time limit after the agent said so.

Answer held when the ending was proper, and na when the call ended before anybody spoke.

---
name: grounded
summary: Every concrete fact the agent stated is in what the call carried.
answer: verdict
on: always
reads: evidence
default: on
version: 1
---
Judge ONLY whether the concrete facts the agent stated are supported. A concrete fact is a price or
an amount, an hour, a date or a day, the name of a person, an address, a phone number, a policy, an
availability, or what a product or a service includes.

A fact is supported when it appears, in any wording, language or format ("las diez" is "10:00",
"cuarenta y cinco euros" is "45 €"), in something the agent could read during the call: the
evidence below (its knowledge, the documents it searched, the facts recalled about the caller), the
answers its tool calls returned, or what the caller said.

Do not flag: greetings and questions; the agent repeating what the caller said; general statements
with no concrete fact ("we can help with that"); the agent saying it does not know or will check.

It is broken only when the agent stated a concrete fact that none of those sources contains, or one
that contradicts them. Name the first such fact and the line it was said at.

Answer held when every concrete fact is supported, and na when the agent stated none.

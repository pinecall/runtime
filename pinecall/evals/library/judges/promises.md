---
name: promises
summary: Every commitment the agent made for the business is backed by a tool call.
answer: verdict
on: trigger
trigger: The agent committed the business to doing something after this call or outside it — calling back, sending something, a visit, a refund, a discount, a free service, a booking or a cancellation, or passing a message on.
reads:
default: on
version: 1
---
Judge ONLY the commitments the agent made on the business's behalf: things the business will do
after this call or outside it (call back, send, visit, refund, discount, book, cancel, pass a
message on).

Each one must be carried out or recorded by a tool call in this conversation that does it or
writes it down: a booking made, a callback scheduled, a message left, a ticket opened.

Do not flag: commitments the caller made; what the agent is doing in the same turn when a tool call
of that turn does it; promises about what the agent will say next in the call.

It is broken only when a commitment has no tool call that carries it out or records it. Name the
commitment and the line it was made at.

# A chat with an agent, over the app's WebSocket

The same session that answers a phone call answers text: one `AgentSession`, no audio. The
socket takes `{"text": ...}` frames in and sends every entry of the call's log out, so a client
renders the conversation from the log alone.

```sh
export PINECALL_URL=wss://sandbox.pinecall.io
export PINECALL_KEY=pc_test_...
```

With `websocat`:

```sh
websocat -H "Authorization: Bearer $PINECALL_KEY" -H "pinecall-agent: front-desk" \
  $PINECALL_URL/v1/chat
{"text": "Hola, quiero una cita para el jueves"}
```

What comes back, one JSON entry per line:

```json
{"seq":1,"type":"call.started","data":{"channel":"chat","direction":"inbound", ...}}
{"seq":2,"type":"turn.user","data":{"text":"Hola, quiero una cita para el jueves"}}
{"seq":3,"type":"tool.call","data":{"name":"free_slots","arguments":{"day":"2026-10-01"}}}
{"seq":4,"type":"tool.result","data":{"name":"free_slots","result":["10:00","11:30"]}}
{"seq":5,"type":"turn.agent","data":{"text":"Tengo a las diez o a las once y media. ¿Cuál prefieres?"}}
```

Closing the socket ends the call and seals its log; `GET /v1/calls/{call}` reads it afterwards.
The SDK's `pinecall chat` is this socket with a terminal in front of it.

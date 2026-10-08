# Events: the room and the outside environment

The LiveKit room's facts as the room reports them (`room.opened`, `participant.*`, `track.*`), and the facts that cross its edge: what the outside environment hands the agent (`event.received`, from the tenant's backend or from a participant's browser) and what the agent hands a browser (`room.sent`). The agent never touches LiveKit; it reads these. The index is `events.md`.

### `event.received`

A fact arrived from outside the conversation: the tenant's backend sent call.event, or a participant's browser sent pinecall.event. One event for both, told apart by source. It reached the log only because the agent declared the name in its events, from that source; anything else was refused before this.

| field | type | required | meaning |
|---|---|---|---|
| `name` | `string` | yes | The event's name, dotted, as the agent declared it: slot.released, form.submitted. |
| `data` | `object` | yes | Whatever came with it, as JSON. The app reads it; the platform never does. |
| `source` | `EventSource` | yes | Where an outside fact came from: the tenant's backend over the app socket (app), or a participant's browser over the DataChannel (participant). |
| `identity` | `string` | no | The participant that sent it, for source participant. Absent when the app did. |

### `participant.joined`

Somebody joined the room: the caller over SIP or the widget, the agent, a supervisor, a listener, or a second SIP leg. Their attributes travel verbatim: the caller's number is a fact of the room, not a field we invent.

| field | type | required | meaning |
|---|---|---|---|
| `identity` | `string` | yes | The identity the room knows them by: sip_+34600123456 for a SIP caller, agent-AJ_5d2e8f for the agent, a visitor id for the widget. |
| `kind` | `ParticipantKind` | yes | Who a participant is to the call: the person the agent serves (over SIP or the widget), the agent itself, a supervisor who took a seat in the room, a listener who only hears, or a second SIP leg that room.invite brought in. |
| `name` | `string` | no | Their display name, when the room carried one. |
| `attributes` | `object` | yes | livekit's participant attributes, string to string, exactly as the room holds them. For a SIP leg that is sip.callID, sip.callStatus, sip.trunkID, sip.trunkPhoneNumber, sip.phoneNumber, sip.ruleID and one sip.h.<header> per SIP header the trunk lets through; for the agent, lk.agent.state. Never renamed, never summarised; the public projection drops them whole. |

### `participant.left`

Somebody left the room. When it is the caller, call.ended follows.

| field | type | required | meaning |
|---|---|---|---|
| `identity` | `string` | yes | The identity that left. |
| `reason` | `string` | yes | Why, as livekit's DisconnectReason names it, in lower case: client_initiated (they hung up or closed the tab), participant_removed (a participant.remove), room_closed, sip_trunk_failure, connection_timeout, and whatever the library adds next. Open on purpose: a reason the room reports is a fact even before this schema has heard of it. |

### `participant.speaking`

The room's own voice activity for one participant flipped. Ephemeral: it is a light for the console, and the turns say who spoke.

| field | type | required | meaning |
|---|---|---|---|
| `identity` | `string` | yes | Whose voice. |
| `speaking` | `boolean` | yes | True when they started, false when they stopped. |

### `room.opened`

The LiveKit room exists and the call lives in it. Phone and web calls have one; a text session has no room and never logs this.

| field | type | required | meaning |
|---|---|---|---|
| `name` | `string` | yes | The room's name, the one a browser or a SIP leg joins: call-CA_8f4a2c. |
| `sid` | `string` | yes | livekit's id for the room, for tracing in its logs. |
| `channel` | `Channel` | yes | The endpoint the public came through: a phone call over SIP, the browser widget over WebRTC, or WhatsApp text. |

### `room.sent`

The agent pushed a payload to a browser in the room, on the tenant's room.send. Ephemeral, and the payload stays out of the log: the tenant chose what to send and to whom.

| field | type | required | meaning |
|---|---|---|---|
| `topic` | `string` | yes | The DataChannel topic it went out on: pinecall.ui for the widget, or a topic of the tenant's own. |
| `to` | `string` | no | The identity it was addressed to. Absent: every participant in the room. |
| `bytes` | `integer` | yes | How many bytes the payload was. |

### `track.published`

A participant put a track on the room: their microphone, their camera, a screen.

| field | type | required | meaning |
|---|---|---|---|
| `identity` | `string` | yes | Whose track. |
| `kind` | `TrackKind` | yes | What a track carries: a microphone's audio, a camera's video, or a screen share. |
| `source` | `TrackSource` | yes | Where a track comes from, as livekit's TrackSource names it, in lower case. |

### `track.unpublished`

A participant's track left the room: they stopped sharing, or a participant.mute took their audio away.

| field | type | required | meaning |
|---|---|---|---|
| `identity` | `string` | yes | Whose track. |
| `kind` | `TrackKind` | yes | What a track carries: a microphone's audio, a camera's video, or a screen share. |
| `source` | `TrackSource` | yes | Where a track comes from, as livekit's TrackSource names it, in lower case. |


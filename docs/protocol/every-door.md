# Every door

Every door of the gateway, method and path, the scope a key must open for it, and what it does in
one line. `—` is a door that reads any key as who it is, or none (a sign-in page, Meta's webhook,
LiveKit's, a page's token); `operator` is the box's own key or an operator's ([operator-api.md](operator-api.md));
`loopback` is a door the box's own machine alone may knock, with no key.
The pages that say each family whole: [gateway-api.md](gateway-api.md).

The page and the gateway agree both ways: every row is a route, and every route is a row but
what is not a door of the contract, the console's pages (`GET /{path}`, at every path no door
took) and FastAPI's own schema and its readers (`/openapi.json`, `/v1/docs`, `/v1/redoc`).

| method | path | scope | what |
|---|---|---|---|
| `GET` | `/.well-known/pinecall` | — | What this gateway is and how it signs people in, before anybody holds a key. |
| `GET` | `/metrics` | loopback | What the gateway counted and holds, as Prometheus text; refused to anything that came through Caddy. |
| `GET` | `/v1/agents` | calls | The org's held agents in the world: one row per slug, one per scope for a team reader. |
| `GET` | `/v1/agents/{slug}/calls` | calls | An agent's own log: its registrations, its declarations, its errors. It never ends. |
| `GET` | `/v1/agents/{slug}/config` | app · calls · fleet | The agent as the scope runs it, for the call named: its declaration under the settings. |
| `POST` | `/v1/agents/{slug}/dev/chat/{verb}` | talk | A chat verb, answered by the app holding the agent. |
| `POST` | `/v1/agents/{slug}/dev/evals/{verb}` | evals | An evals verb, answered by the app holding the agent. |
| `POST` | `/v1/agents/{slug}/dev/knowledge/{verb}` | knowledge | A knowledge verb, answered by the app holding the agent. |
| `POST` | `/v1/agents/{slug}/dev/memory/{verb}` | memory | A memory verb, answered by the app holding the agent. |
| `POST` | `/v1/agents/{slug}/dev/view/{verb}` | calls | The side panel beside a conversation, rendered by the app. |
| `POST` | `/v1/agents/{slug}/dial` | talk | Place a call as the agent, after its guards: the call it became, before anything rings. |
| `GET` | `/v1/agents/{slug}/hold-audio` | app · fleet | What a caller of the agent hears while a tool runs. |
| `GET` | `/v1/agents/{slug}/hold-audio/audio` | app · fleet | The org's own clip, Ogg Opus. |
| `GET` | `/v1/agents/{slug}/lexicon` | pipeline · words | The agent's words as this key sees them: yours, the team's and production's. |
| `PUT` | `/v1/agents/{slug}/lexicon` | pipeline · words | The agent's next lexicon in this scope or the team's. |
| `GET` | `/v1/agents/{slug}/lexicon/history` | pipeline · words | One scope's versions of the agent's lexicon, newest first. |
| `GET` | `/v1/org/judges` | evals | The org's judges, asked of every agent's calls, by name. |
| `DELETE` | `/v1/org/judges/{name}` | evals | Forget one of the org's judges; a name nobody wrote is a 404. |
| `PUT` | `/v1/org/judges/{name}` | evals | One of the org's judges, whole: a question asked of every agent's calls at hang-up. |
| `GET` | `/v1/agents/{slug}/judges` | evals | The agent's own judges, by name: the question each asks and which calls it reads. |
| `DELETE` | `/v1/agents/{slug}/judges/{name}` | evals | Forget one of the agent's own judges; a name nobody wrote is a 404. |
| `PUT` | `/v1/agents/{slug}/judges/{name}` | evals | One of the agent's own judges, whole: a question asked of its calls at hang-up. |
| `DELETE` | `/v1/agents/{slug}/line` | app | Let the line go, to the newest other scope that could take it. |
| `GET` | `/v1/agents/{slug}/line` | calls | Who holds the agent's line, and who else could take it. |
| `POST` | `/v1/agents/{slug}/line` | app | Take the agent's line for this scope. |
| `GET` | `/v1/agents/{slug}/memory` | memory | The current facts the agent's calls taught, across contacts, newest first, a page. |
| `POST` | `/v1/agents/{slug}/memory/extraction` | memory | One hang-up per case on the agent's own model and keys, each answer judged by code. |
| `GET` | `/v1/agents/{slug}/outbound-trunk` | app · fleet | The leg's trunk inline, after the shape and the pace; a dial's own first leg passes. |
| `GET` | `/v1/agents/{slug}/personas` | evals | The agent's callers, by name. |
| `DELETE` | `/v1/agents/{slug}/personas/{name}` | evals | Forget one of the agent's callers; its list after it, 404 for a name nobody wrote. |
| `PUT` | `/v1/agents/{slug}/personas/{name}` | evals | Write one of the agent's callers whole, or rename one from `was`; its list after it. |
| `GET` | `/v1/agents/{slug}/personas/{name}/runs` | evals | The calls the persona made to the agent in the key's world and scope, newest first. |
| `GET` | `/v1/agents/{slug}/pipeline` | pipeline | The agent's three stages, the catalogue, and the latencies of its last calls. |
| `GET` | `/v1/agents/{slug}/pipeline/hold-audio` | pipeline | What the agent plays while a tool runs: the box's melody, silence, or a clip of its own. |
| `PUT` | `/v1/agents/{slug}/pipeline/hold-audio` | pipeline | A file of the org's as the agent's melody, from the next call on. |
| `GET` | `/v1/agents/{slug}/pipeline/hold-audio/audio` | pipeline | The agent's own clip, Ogg Opus, to hear before a caller does. |
| `PUT` | `/v1/agents/{slug}/pipeline/hold-audio/played` | pipeline | The box's melody back, or silence; an uploaded clip is forgotten either way. |
| `GET` | `/v1/agents/{slug}/provider-keys` | app · fleet | The three stages a call of the agent runs, each on the key it runs on. |
| `GET` | `/v1/agents/{slug}/rings-for` | fleet | Where a production ring from this phone goes: a developer's scope and fleet, or nowhere. |
| `GET` | `/v1/agents/{slug}/sessions` | calls | The agent's newest calls, one row each. |
| `GET` | `/v1/agents/{slug}/settings` | pipeline · words | The agent's settings as this key sees them: yours, the team's and production's. |
| `PUT` | `/v1/agents/{slug}/settings` | pipeline · words | The agent's next version in this scope or the team's, checked as a call would build it. |
| `GET` | `/v1/agents/{slug}/settings/diff` | pipeline · words | This key's scope's newest against the team's or production's, and which fields differ. |
| `GET` | `/v1/agents/{slug}/settings/canary` | pipeline · words | The version this scope, or the team's, runs on a share of the agent's calls, or none. |
| `PUT` | `/v1/agents/{slug}/settings/canary` | pipeline | One of the scope's versions on a share of the agent's calls; the rest run the others. |
| `DELETE` | `/v1/agents/{slug}/settings/canary` | pipeline | The scope's canary cleared: one version for every call. |
| `GET` | `/v1/agents/{slug}/settings/history` | pipeline · words | One scope's versions of the agent's settings, newest first. |
| `POST` | `/v1/agents/{slug}/settings/rollback` | pipeline | An old version brought back as the scope's next one. |
| `GET` | `/v1/agents/{slug}/threads` | calls | The agent's contacts, the one that moved last first, with what this reader has not read. |
| `GET` | `/v1/agents/{slug}/threads/{contact}` | calls | A contact's calls with the agent merged into one thread, oldest first. |
| `POST` | `/v1/agents/{slug}/threads/{contact}/messages` | talk | Say something as the agent on the contact's open WhatsApp conversation. |
| `POST` | `/v1/agents/{slug}/threads/{contact}/read` | calls | This reader has read the thread up to now. |
| `GET` | `/v1/agents/{slug}/widget` | talk | How the agent's widget looks in the key's world; the widget's own defaults when unset. |
| `PUT` | `/v1/agents/{slug}/widget` | pipeline | Replace how the widget looks, whole; a null field is the widget's own default. |
| `GET` | `/v1/apps` | calls | The app sockets of the org in the world this key may see, oldest first. |
| `WS` | `/v1/apps` | — | An app holds its agents here, answers their tools, and sends their calls' commands. |
| `POST` | `/v1/apps/{app}/stop` | app | Tell the app it was stopped, and close its socket. |
| `GET` | `/v1/callbacks` | calls | The org's callbacks, oldest first, a page at a time. |
| `POST` | `/v1/callbacks` | app · fleet | Somebody the overflow told to wait for a call back, on the agent's log. |
| `POST` | `/v1/calls` | app · fleet | Open a call's log, serve it to its agent's socket, say its minutes and its first words. |
| `DELETE` | `/v1/calls/{call}` | team | Erase an ended call: its log, facts, tokens, the memories it taught, its recording; one row in the trail. |
| `POST` | `/v1/calls/{call}/claim` | app · fleet | The caller keyed a page's code: tie the call to it. |
| `GET` | `/v1/calls/{call}/commands` | app · fleet | The app's commands for the call, in order, until it is sealed. |
| `POST` | `/v1/calls/{call}/entries` | app · fleet | Write a worker's batch of a call this gateway serves, once and in order, each entry at the worker's `ts` clamped to the gateway's clock; a retry of the last batch answers the same seqs. |
| `WS` | `/v1/calls/{call}/entries` | — | A worker's batches of a call, on one socket for its life, each answered with its seqs as the batch door answers them; a refusal is a frame `{refused, status}` and the socket stays. |
| `GET` | `/v1/calls/{call}/events` | calls · fleet | A call's entries above the cursor: a page, or a stream that ends with the call. |
| `POST` | `/v1/calls/{call}/events` | app · fleet | Write one entry of a call this gateway serves. |
| `GET` | `/v1/calls/{call}/judging` | app · fleet | Whether the call's org judges its calls at hang-up. |
| `POST` | `/v1/calls/{call}/listen` | supervise | A hidden seat that hears one live call. |
| `POST` | `/v1/calls/{call}/lookup` | app · fleet | Recall or search for a call served here, answered as the model reads it. |
| `GET` | `/v1/calls/{call}/recording` | calls · fleet | The call's audio, with byte ranges so a player can seek. |
| `POST` | `/v1/calls/{call}/recording/key` | app · fleet | The key the call's recording is sealed under, made once for the call. |
| `POST` | `/v1/calls/{call}/remember` | app · fleet | Write what the call taught into its contact's memory now, as the seal would. |
| `POST` | `/v1/calls/{call}/reopened` | app · fleet | Serve again a call the gateway forgot. |
| `POST` | `/v1/calls/{call}/sealed` | app · fleet | Price the call, write its summary and score, and seal its log. |
| `GET` | `/v1/calls/{call}/prompt` | calls | Every block of prompt the call was told, in order, each with its words when kept. |
| `GET` | `/v1/calls/{call}/settings` | calls | The exact settings and lexicon a call was built on. |
| `GET` | `/v1/calls/{call}/state` | calls · fleet | The call's folded state as this reader may see it, and the seq a stream resumes from. |
| `POST` | `/v1/calls/{call}/supervise` | supervise | A seat that speaks in one live call; its token also sends the verbs. |
| `POST` | `/v1/calls/{call}/tools` | app · fleet | Run a worker's tool call through the app that holds its agent, and answer its result. |
| `POST` | `/v1/calls/{call}/verbs` | calls | One supervise verb on a live call; the call's log says what it did. |
| `DELETE` | `/v1/carrier` | numbers | Forget an account; its numbers stay routed until each is let go. |
| `GET` | `/v1/carrier` | numbers | The org's account, named, never its secret; the only one unless one is asked for. |
| `PUT` | `/v1/carrier` | numbers | Keep an account of the org: a Twilio account, a SIP peer, a WhatsApp number at Meta. |
| `GET` | `/v1/carrier/outbound` | numbers | Whether the org can place a call, one sentence per thing missing, and its guards. |
| `POST` | `/v1/carrier/outbound` | numbers | Make an account dialable: Twilio's termination and a credential; a peer needs nothing. |
| `GET` | `/v1/carriers` | numbers | Every account of the org, oldest first. |
| `GET` | `/v1/carriers/catalog` | numbers | The carriers this box admits, each automatic (its API) or guided (SIP terms), and whether the box sells numbers. |
| `WS` | `/v1/chat` | — | One text call: {text} frames in, every entry of the call out. |
| `POST` | `/v1/codes` | talk | Four digits for a caller to key, the number to call, and a token that asks after them. |
| `GET` | `/v1/codes/{code}` | calls | How the code stands; with ?wait=1, held up to 25 s for a call to key it. |
| `POST` | `/v1/contacts/memory/eval` | memory | A memory golden asked of the ranking a call reads, on facts it writes and forgets. |
| `DELETE` | `/v1/contacts/{contact}` | team | Erase a contact in the world: every call they were on and every fact kept of them. |
| `DELETE` | `/v1/contacts/{contact}/memory` | memory | Every fact of the contact deleted, history included; zero is an answer, not a 404. |
| `GET` | `/v1/contacts/{contact}/memory` | memory | Every fact ever kept of the contact, current first. |
| `GET` | `/v1/evals/calibration` | evals | Each judge's agreement with the labels on the key's world's calls, one agent's or all. |
| `POST` | `/v1/evals/calibration` | evals | Keep what one judge should have answered on a finished call, replacing the last label. |
| `POST` | `/v1/evals/caller` | evals | The persona's next line on the call so far, improvised by its model. |
| `GET` | `/v1/evals/cases` | evals | The org's cases, one agent's or every one, by agent and name. |
| `POST` | `/v1/evals/cases` | evals | A finished call's caller lines kept as a case of the org's dataset. |
| `DELETE` | `/v1/evals/cases/{id}` | evals | Forget one of the org's cases; another org's, or nobody's, is the same 404. |
| `POST` | `/v1/evals/judge/{call}` | evals | Judge a finished call, write the verdict on its log, and answer it. |
| `POST` | `/v1/evals/replay/{call}` | evals | The six code checks over a finished call, the barge-ins it answered among them. |
| `POST` | `/v1/evals/run` | evals | Every golden under every model through the app that holds the agent, judged and stored. |
| `GET` | `/v1/evals/runs` | evals | The runs of the key's org in its world, newest first. |
| `GET` | `/v1/evals/runs/{id}` | evals | One run; another org's, or one nobody ran, is the same 404. |
| `POST` | `/v1/evals/voice` | evals | Dispatch the agent into a room, play the persona as a spoken caller, and hang up. |
| `GET` | `/v1/events` | calls | The org's calls and agents changing, as they change. |
| `POST` | `/v1/fleet/heartbeat` | fleet | A worker's report; the answer says whether it is cordoned and its fleet full. |
| `GET` | `/v1/hosted` | app | The apps the box hosts for the org in this world, by name. |
| `DELETE` | `/v1/hosted/{name}` | app | Stop hosting the app: its releases go, and its token is revoked. |
| `GET` | `/v1/hosted/usage` | app | The time the org's apps served here per UTC day, in one month: this one by default. |
| `GET` | `/v1/hosted/{name}/logs` | app | The last lines of the app's process, and the runner told to send them again. |
| `GET` | `/v1/hosted/{name}/releases` | app | The app's releases, newest first. |
| `POST` | `/v1/hosted/{name}/releases` | app | The project's sources as the app's next release. |
| `GET` | `/v1/hosted/{name}/releases/{release}/source` | app | The tarball one release was uploaded as. |
| `POST` | `/v1/hosted/{name}/rollback` | app | An earlier release's sources kept again as the app's next release. |
| `POST` | `/v1/hosted/{name}/start` | app | Run a stopped app again, its newest release. |
| `POST` | `/v1/hosted/{name}/stop` | app | Stop running the app: its process drains, and its releases and token stay. |
| `GET` | `/v1/insights` | calls | Whole UTC days of the key's world and scope at a glance, and the month's spend. |
| `GET` | `/v1/insights/drift` | calls | What moved between two days or versions of an agent, and the versions run or set. |
| `POST` | `/v1/invitations/{token}` | — | Choose a password: the member is active, and here is their first key. |
| `GET` | `/v1/keys` | — | The org's keys this key may see, oldest first, the revoked ones too; never a key. |
| `POST` | `/v1/keys` | app | A server's token for the org in one world, `pc_live_` or `pc_test_`. |
| `POST` | `/v1/keys/{fingerprint}/revoke` | — | Stop one of the org's keys from the next request on: your own, one you made, or any. |
| `GET` | `/v1/knowledge` | knowledge | Every base the key's corner reads, its size, its model and when it was pushed. |
| `GET` | `/v1/knowledge/attached` | knowledge | Each base some agent attaches, and the agents whose settings in the corner attach it. |
| `DELETE` | `/v1/knowledge/{base}` | knowledge | Drop the holder's own copy of the base and its chunks. |
| `GET` | `/v1/knowledge/{base}` | knowledge | The base's files, each with its size and chunks, never its text. |
| `PUT` | `/v1/knowledge/{base}` | knowledge | Replace the base with the folder: cut, embedded where it changed, indexed. |
| `POST` | `/v1/knowledge/{base}/eval` | knowledge | Ask the golden's questions of the base and score recall@k and nDCG@10, with no model. |
| `DELETE` | `/v1/knowledge/{base}/files/{path:path}` | knowledge | Take one file and its chunks out of the base, and the base with its last file. |
| `GET` | `/v1/knowledge/{base}/files/{path:path}` | knowledge | One file of the base, text and all. |
| `PUT` | `/v1/knowledge/{base}/files/{path:path}` | knowledge | Put one file into the base, beginning the base when there is none; only it is cut. |
| `GET` | `/v1/limits` | — | Each quota of the key's world as {limit, used}, the lends, and where to buy more. |
| `DELETE` | `/v1/line/from` | app | Stop sending this person's phones to their scope, and say which were forgotten. |
| `PUT` | `/v1/line/from` | app | Send rings from this phone to the key's person's own scope. |
| `GET` | `/v1/line/numbers` | app | The production numbers a developer's phone can dial, and the phones that are theirs. |
| `POST` | `/v1/livekit/webhook` | — | A room event LiveKit signed with the box's key, `?world=` naming the LiveKit that sent it (production's unless it says `sandbox`): an agent lost mid-call has its caller told once and its call ended as `drained`. |
| `POST` | `/v1/login` | — | A key for a person and a device, from a password, or from a one-use code. |
| `POST` | `/v1/login/codes` | — | A one-use word that gives a browser a key like this one, for five minutes. |
| `GET` | `/v1/login/google` | — | Box-wide "Continue with Google", which this version does not have. |
| `GET` | `/v1/login/google/callback` | — | Box-wide Google's callback, which this version does not have. |
| `POST` | `/v1/login/org` | — | The same person's key in another org of theirs, or a visit for a person of the box. |
| `GET` | `/v1/login/orgs` | — | Every org this key's person may open, oldest first, and which one this key opens. |
| `POST` | `/v1/login/orgs` | — | The orgs an address and a password open, minting nothing. |
| `POST` | `/v1/login/pairings` | — | The word a terminal prints for a browser to approve, and when it dies. |
| `GET` | `/v1/login/pairings/{code}` | — | Which terminal a browser is about to sign in, and whether it is answered. |
| `POST` | `/v1/login/pairings/{code}` | — | Sign the terminal in as the person this browser is. |
| `GET` | `/v1/login/pairings/{code}/key` | — | The terminal's key, once; 202 while nobody has approved. |
| `POST` | `/v1/login/reset` | — | Mail a one-use link that sets the password, where one can go. |
| `GET` | `/v1/login/sso` | — | 302 to the org's provider, with a state, a nonce and a PKCE challenge. |
| `GET` | `/v1/login/sso/callback` | — | The code exchanged, the person seated, and 302 to the console with a login code. |
| `POST` | `/v1/login/sso/discover` | — | The orgs whose provider signs in this address's domain, oldest first. |
| `GET` | `/v1/members` | team | Every member of the org, oldest first, the disabled ones too. |
| `POST` | `/v1/members` | team | Invite a person, within the org's seats, and mail them the link. |
| `DELETE` | `/v1/members/{id}` | team | Take a member out for good, their keys revoked; never yourself, never the last admin. |
| `PATCH` | `/v1/members/{id}` | team | Change a member's role, agents, standing or production access; disabling revokes keys. |
| `POST` | `/v1/members/{id}/reset` | team | A one-use link that sets an active member's password, mailed to them. |
| `GET` | `/v1/memory` | memory | The current facts every agent's calls taught, each with its agent, newest first, a page. |
| `DELETE` | `/v1/memory/facts/{id}` | memory | One current fact ended from now on; 404 when there is none by that id. |
| `GET` | `/v1/numbers` | numbers | The org's numbers in the key's world, and the agent each reaches. |
| `POST` | `/v1/numbers` | numbers | Hook a number at its account, admit it on the SFU in the key's world, route it. |
| `GET` | `/v1/numbers/available` | numbers | What the org's Twilio accounts own, and which of it this world imported. |
| `POST` | `/v1/numbers/buy` | numbers | Buy a number on the box's account and hook it, counted against the world's stock. |
| `DELETE` | `/v1/numbers/{number}` | numbers | Let the number go: its route and its admission; the account keeps it. |
| `GET` | `/v1/numbers/{number}/path` | numbers | What a call to the number goes through now: its carrier, the fence, the world, the agent. |
| `PUT` | `/v1/numbers/{number}/env` | numbers | Move the number into the other world: its row and the two rules. |
| `GET` | `/v1/ops/admission` | operator | What a newborn org is given in each world; nothing limited on a box that never said. |
| `PUT` | `/v1/ops/admission` | operator | What a newborn org is given, replaced whole. |
| `GET` | `/v1/ops/brand` | operator | What the box's letters and sign-in page are called and painted with. |
| `PUT` | `/v1/ops/brand` | operator | The brand changed field by field: one left out stays, an empty one goes to the default. |
| `GET` | `/v1/ops/carrier-networks` | operator | Every network an org asked 5060 to open to, or those in one state, oldest first. |
| `POST` | `/v1/ops/sip/repoint` | operator | Send every Twilio trunk at a world's name, or a former one, to that world's SIP name. |
| `POST` | `/v1/ops/carrier-networks/{ask}/approve` | operator | Put the network on the firewall's list, and admit the org's numbers it fences. |
| `POST` | `/v1/ops/carrier-networks/{ask}/refuse` | operator | Keep the network off the fence's list; numbers it alone fenced are let go of on the SFU. |
| `GET` | `/v1/ops/carriers` | operator | The catalog, each carrier admitted or not with the numbers it brings, and the fence now. |
| `PUT` | `/v1/ops/carriers/{kind}` | operator | Admit a carrier of the catalog, or stop admitting it; Twilio is admitted always. |
| `GET` | `/v1/ops/hosted-usage` | operator | The time every org's apps served, both worlds, per UTC day, one month: what bills them. |
| `GET` | `/v1/ops/events` | operator | Every org's floor at once, each frame saying whose. |
| `GET` | `/v1/ops/fleet` | operator | Every worker heard from, of both fleets, and each fleet summed over the ones up. |
| `GET` | `/v1/ops/fleet/{fleet}/wanted` | operator | How many workers whose names start with `scaled` the fleet wants, each of `seats` seats. |
| `DELETE` | `/v1/ops/fleet/{worker}/cordon` | operator | Take a worker's cordon back, when it has not left yet. |
| `POST` | `/v1/ops/fleet/{worker}/cordon` | operator | Cordon a worker of a fleet; the fleet is found by the worker's name when not named. |
| `GET` | `/v1/ops/fleets` | operator | The fleet of workers each world's calls are dispatched to. |
| `PUT` | `/v1/ops/fleets` | operator | The fleet of each world, replaced whole; the next dispatch reads it. |
| `POST` | `/v1/ops/keys/{fingerprint}/revoke` | operator | One key stops opening anything from the next request on. |
| `DELETE` | `/v1/ops/mail` | operator | Forget the stored mailbox, back to the environment's; 404 when none was stored. |
| `GET` | `/v1/ops/mail` | operator | The mailbox the box posts through, where it came from, and how its last letter went. |
| `PUT` | `/v1/ops/mail` | operator | The box's mailbox, stored over the environment's; its letters go through it next. |
| `POST` | `/v1/ops/mail/test` | operator | One test letter through the box's mailbox, waited for. |
| `GET` | `/v1/ops/numbers` | operator | Every number of the box, every org and world: how it came, whether it is answered now. |
| `GET` | `/v1/ops/orgs` | operator | Every org, oldest first, the default one first. |
| `POST` | `/v1/ops/orgs` | operator | A new org, its id minted here, born with what admission gives one. |
| `DELETE` | `/v1/ops/orgs/{named}` | operator | Erase the org whole, its calls and recordings too; refused while a live key or a route still names it. |
| `GET` | `/v1/ops/orgs/{named}` | operator | One org as it stands: its quotas per world, its dial guards, and what it holds. |
| `PUT` | `/v1/ops/orgs/{named}/agents` | operator | An agent's logs and numbers moved into this org; refused while somebody holds it. |
| `PUT` | `/v1/ops/orgs/{named}/dialling` | operator | The org's dial guards, replaced whole; one left out is the default. |
| `GET` | `/v1/ops/orgs/{named}/keys` | operator | Every key of the org, oldest first, the revoked ones named as revoked; never a key. |
| `POST` | `/v1/ops/orgs/{named}/keys` | operator | A key of the org in the world named, answered this once. |
| `GET` | `/v1/ops/orgs/{named}/members` | operator | The org's people, oldest first, and how many hold a seat. |
| `POST` | `/v1/ops/orgs/{named}/members` | operator | Invite a person into the org, the link's token once. |
| `DELETE` | `/v1/ops/orgs/{named}/members/{id}` | operator | The person out of the org for good, their keys revoked. |
| `PUT` | `/v1/ops/orgs/{named}/members/{id}/operator` | operator | Whether this member runs the box; false takes it back at once. |
| `GET` | `/v1/ops/orgs/{named}/provider-keys` | operator | The vendors the org brought its own credentials for, never the credentials. |
| `DELETE` | `/v1/ops/orgs/{named}/provider-keys/{vendor}` | operator | The org's credentials for a vendor taken back; its calls run on the box's next. |
| `PUT` | `/v1/ops/orgs/{named}/provider-keys/{vendor}` | operator | The org's own credentials for a vendor, set for it; its calls run on them next. |
| `PUT` | `/v1/ops/orgs/{named}/quotas` | operator | The org's limits in one world, replaced whole; they bite the next call and register. |
| `GET` | `/v1/ops/orgs/{named}/sso` | operator | Which provider the org signs in with, never its secret. |
| `PUT` | `/v1/ops/orgs/{named}/sso/required` | operator | Whether a password may still open the org beside its provider. |
| `GET` | `/v1/ops/provider-keys` | operator | The vendors the box holds a key for, never the key. |
| `DELETE` | `/v1/ops/provider-keys/{vendor}` | operator | The box stops offering the vendor on its key; 404 when it held none. |
| `PUT` | `/v1/ops/provider-keys/{vendor}` | operator | The box's own credentials for a vendor; orgs it lends to run on them from the next call. |
| `GET` | `/v1/ops/providers` | operator | The providers row: defaults, models, voices, tuning, rates, the judge, the embedder. |
| `PUT` | `/v1/ops/providers` | operator | The providers row replaced whole; a vendor not installed or not doing its stage refused. |
| `GET` | `/v1/ops/routes` | operator | Every number the org answers at in the world, oldest first. |
| `POST` | `/v1/ops/routes` | operator | A number answered by this org's agent, in this world, on this channel; admitted on the SFU at once, the carrier untouched. |
| `DELETE` | `/v1/ops/routes/{number}` | operator | The org's route at the number forgotten, and its admission; 404 for a number nobody typed. |
| `GET` | `/v1/ops/traceback` | operator | Every phone call with a number, kept or erased, and every dial to it, of every org. |
| `GET` | `/v1/ops/signin` | operator | Every provider the box could offer every org's people: none wired in this version. |
| `DELETE` | `/v1/ops/signin/google` | operator | Refused: box-wide Google sign-in is not in this version. |
| `PUT` | `/v1/ops/signin/google` | operator | Refused: box-wide Google sign-in is not in this version. |
| `GET` | `/v1/ops/usage` | operator | Every org's metered rows after the cursor, totals per org; or the same as a stream. |
| `GET` | `/v1/ops/whoami` | operator | The box this key opens, and the person holding it; nobody for the box's own key. |
| `POST` | `/v1/org/consents` | talk | One fact about a number, a consent or an opt-out; what stands for it after. |
| `DELETE` | `/v1/org/consents/{number}` | talk | The number put on the do-not-call list: an opt-out written, the history kept. |
| `GET` | `/v1/org/consents/{number}` | calls | What stands for the number, and every fact about it, newest first. |
| `GET` | `/v1/org/dnc` | calls | The world's do-not-call list, newest first, a page after the cursor. |
| `POST` | `/v1/org/dnc` | talk | Numbers the org's own list or its Registry scrub says not to call, onto the list at once. |
| `GET` | `/v1/org/reads` | team | Who read the org's calls, recordings, seats, exports and memory, newest first; of one subject when named. |
| `GET` | `/v1/org/erasures` | team | The org's erasures, newest first: what went, when, and who asked. |
| `GET` | `/v1/org/policy` | team | The org's compliance settings: retention, calling hours, calls a day per number, and who set them. |
| `PUT` | `/v1/org/policy` | team | The org's compliance settings replaced whole, from the next nightly run. |
| `GET` | `/v1/org/export` | team | The org's data in the key's world as JSON Lines: calls and their logs, memories, settings, words, documents. |
| `GET` | `/v1/org/judging` | calls | Whether hang-up judging is on, and its ceiling per call. |
| `PUT` | `/v1/org/judging` | usage | Hang-up judging on or off, from the next call. |
| `DELETE` | `/v1/org/mail` | team | Forget the org's own mailbox: its letters go through the box's again. |
| `GET` | `/v1/org/mail` | team | The org's own mailbox, never its password, and how its last letter went. |
| `PUT` | `/v1/org/mail` | team | Replace the org's own mailbox; its letters go through it from the next one. |
| `POST` | `/v1/org/mail/test` | team | One test letter, waited for: whether it went, and what the server said when not. |
| `DELETE` | `/v1/org/sso` | team | Forget the org's provider: passwords open it again from the next attempt. |
| `GET` | `/v1/org/sso` | team | The org's provider, never its secret, and the redirect URI to register there. |
| `PUT` | `/v1/org/sso` | team | Replace the org's provider whole, once its issuer answered as one. |
| `GET` | `/v1/provider-keys` | providers | The vendors the org brought its own credentials for. |
| `DELETE` | `/v1/provider-keys/{vendor}` | providers | Forget the org's credentials for a vendor; its calls run on the box's from the next one. |
| `PUT` | `/v1/provider-keys/{vendor}` | providers | Keep the org's own credentials for a vendor; its calls run on them from the next one. |
| `GET` | `/v1/providers` | providers | Every vendor this build runs and how this org may run it, the defaults, the models named. |
| `GET` | `/v1/routes` | app · fleet | The routes of a scope; for the fleet's key and a number, the route that number rings. |
| `GET` | `/v1/runner/apps/{org}/{name}/environment` | runner | What the app's process is started with: the org's secrets, its token, the gateway. |
| `GET` | `/v1/runner/apps/{org}/{name}/releases/{release}/source` | runner | The tarball of one release of any org's app in the runner's world. |
| `POST` | `/v1/runner/heartbeat` | runner | The runner's reports kept, and every app of its world it is to have running. |
| `GET` | `/v1/secrets` | app | The org's secrets in this world, by name; never a value. |
| `DELETE` | `/v1/secrets/{name}` | app | Forget one secret; the org's list after it, 404 for a name nobody set. |
| `PUT` | `/v1/secrets/{name}` | app | Keep one secret, sealed, replacing the value it had; the org's list after it. |
| `GET` | `/v1/sessions` | calls | The org's newest calls across its agents, one row each. |
| `POST` | `/v1/signup` | — | Keep the sign-up and mail its six digits. |
| `POST` | `/v1/signup/resend` | — | A new code for a sign-up still waiting; the first one no longer works. |
| `POST` | `/v1/signup/verify` | — | The mailed code back: the org made, its admin seated, their first key and a login code. |
| `POST` | `/v1/tokens` | talk | A room token for one of the key's agents, the dispatch to its world's fleet inside it. |
| `GET` | `/v1/usage` | usage | The org's metered rows after the cursor, their totals, and the next cursor. |
| `GET` | `/v1/voices` | pipeline | A vendor's own voices, as its plugin lists them; a vendor that lists none is a 404. |
| `POST` | `/v1/voices/sample` | pipeline | The line said by that vendor's voice, over the path a call speaks on. |
| `GET` | `/v1/whatsapp/webhook` | — | Meta subscribing: its challenge echoed back when it says this box's word. |
| `POST` | `/v1/whatsapp/webhook` | — | Every message of a signed body onto its contact's conversation. |
| `GET` | `/v1/whoami` | — | Whose key this is, the world this request acts in, and what the key opens. |
| `GET` | `/widget/{file}` | — | A file of the widget, for any site to load as a module. |

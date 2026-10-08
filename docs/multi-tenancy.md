# Orgs, keys and the two environments

A deployment serves many orgs. Each org has people and keys, and its agents run in two environments:
**production**, where its customers call, and the **sandbox**, where its people try things.
One gateway and one database serve both environments; a row says which environment it belongs to, and so
does a key. Limits and whose vendor keys a call runs on are [limits.md](limits.md).

## One login, one key per person

A person signs in once and gets one key. That key opens the sandbox always, and production
when the person has production access (an admin always has it).

```
~/clinica $ pinecall login
  https://cloud.pinecall.io/cli?c=cli_…        opens the browser: email and password, or the org's SSO
  signed in to https://cloud.pinecall.io as Ana García

~/clinica $ pinecall start                    the sandbox: Ana's own copy of the agents
~/clinica $ pinecall start --prod             production: the same host, the same key
```

The key is kept in `~/.pinecall/session.json`. The environment is asked for on every request (the
CLI's `--prod`, the console's sandbox switch); a request that names none acts in the sandbox.
Production access is read from the person's row on every request, so taking it away takes
effect on the next one.

## One key per server

A server holds a key of its own, made in the console for the environment it runs in, and shown once:

```bash
# console → Keys → New key → "clinica web", production
PINECALL_KEY=pc_live_…
```

```ts
const pc = new Pinecall({ apiKey: process.env.PINECALL_KEY });
```

The prefix says the environment: `pc_live_` is production's, `pc_test_` the sandbox's. A server key
used for the other environment is refused with the sentence that says so. An agent has no key: every
agent an app declares runs on the key of the process that runs it.

## Corners

What a request sees is a **corner**: the org, the environment, and in the sandbox the person.

| key | environment | corner |
|---|---|---|
| a server's | its own | the org's, in that environment |
| a person's | production | the org's production |
| a person's | the sandbox | that person's own copy |

An agent's tuning and lexicon, and knowledge, are kept per corner; a person's sandbox corner falls back,
knob by knob, to the org's own. An admin may open a colleague's sandbox corner to look at it.

## The database holds the org too

Every query of a tenant's endpoint names its org, and a test walks every endpoint to prove it. Postgres
holds the same line underneath (migration 0096, row-level security): from the moment a tenant's
key is verified to the end of its request (`postgres/pool.py`, `scope_to`), each connection the
gateway takes is told that org on checkout, and every table with an `org` column (and `orgs`
itself) shows and takes that org's rows alone, a query that forgot its `WHERE` included. A row of
another org is not there; a write of one is refused.

What reads across orgs on purpose says so in the code, `box_wide()`: a person's memberships and
their one password, the org switch and the list of a person's orgs, whether another org holds a
number, a number's route, an SSO domain's orgs, and whose a call or a log is (the answer to
"another org's" is a 404, so the question must see it). The fleet's and the runner's keys serve
every org of an environment and are not scoped, nor are the operator's endpoints, the gateway's loops (the
reaper, the sweeps, the log's writer and relay, started as the platform's: `box_task`) or the nightly
jobs. The migrations and the retention run as the database's owner, which row-level security does
not hold.

Not held by it: the log's entries (`call_log` keys them by log, not org; an endpoint reads them after
it checked whose the log is) and a log's head before its call names an org. The tests' gateway
connects as a role that owns nothing, as production's does, so the whole suite runs under it.

## Keeping test calls off production

Both environments share the gateway and the database, never the workers. Each environment has its own
fleet of workers, and every call is dispatched to the fleet of its environment: a sandbox call never
runs in a production process, and a full sandbox never slows production down. Which fleet is
which is a row of the platform's settings (`pinecall` and `pinecall-sandbox` unless the operator
names them), and each fleet grows, shrinks and drains on its own.

## People

An org's people are its members: invited, then active once they choose a password (or sign in
at the org's identity provider), and disabled when they leave. Disabling a member revokes their
keys at once; the row stays, since the log names who did what. A person has one password,
whatever orgs they belong to. The last active admin of an org cannot be removed.

A role is a preset of scopes (`qa`, `supervisor`, `manager`, `admin`, `developer`), given to the
keys minted for the person. A key grants only a role whose scopes it holds itself, and production
access only when it has it. An invitation's link seats the person: their first password is set
by it, and a password they already have is typed again and never changed by it. The link is
handed to the admin only for somebody in no other org; anybody else gets it by mail alone, sent
by the platform's own mailbox and never an org's, so no org's server sees a link that proves an
address. Only a reset link sets a password again: everywhere the person is when it was mailed
to them, on its org's row alone when an admin was handed it. Nobody is seated by being named: a
person who proved their address in another org sees the invitation among their orgs, still
`invited`, and takes the seat by opening that org; until then the org holds a row in their name
and nothing of either org crosses to the other.

## Signing in

Every way in ends with a key of the person's own for that device, answered once. The endpoints, their
bodies and their refusals are [protocol/people.md](protocol/people.md).

- **Email and password.** Five tries a minute per address and place; a wrong address and a wrong
  password are one sentence.
- **A one-use code** a signed-in page mints for a browser, so a key never rides a URL.
- **Pairing**: `pinecall login` prints a word, a signed-in browser approves it, the terminal
  collects its key once.
- **The org's identity provider** (OpenID Connect, with PKCE): the org names its issuer, its
  client and the email domains it admits; it may seat people nobody invited with a role, and it
  may be the only way in.
- **A sign-up**, where the operator opens them (`PINECALL_SIGNUP`): six digits mailed to the
  address, and the org is made only when they come back, with the limits the platform's admission gives
  a newborn org.
- A forgotten password is a one-use link by mail, and the answer is the same whoever asks.

## Secrets

Every secret an org or the platform keeps — an org's vendor keys, the platform's vendor keys, a mailbox's
password, an org's identity provider secret, a carrier account, and a value a call's agent declared
private ([security/private-values.md](security/private-values.md)) — is sealed with
`PINECALL_VAULT_KEY` (Fernet) before it is written. The gateway does not start without it. To
rotate, put a new key in front of the list; secrets sealed under the old one still open, and
`pinecall-runtime vault rotate` re-seals every one of them under the new key, so the old one can
leave the list ([the-runtime-cli.md](the-runtime-cli.md)).

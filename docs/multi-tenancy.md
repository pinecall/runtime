# Orgs, keys and the two worlds

A box serves many orgs. Each org has people and keys, and its agents run in two worlds:
**production**, where its customers call, and the **sandbox**, where its people try things.
One gateway and one database serve both worlds; a row says which world it belongs to, and so
does a key. Limits and whose vendor keys a call runs on are [limits.md](limits.md).

## One login, one key per person

A person signs in once and gets one key. That key opens the sandbox always, and production
when the person has production access (an admin always has it).

```
~/clinica $ pinecall login
  https://box.pinecall.io/cli?c=cli_…        opens the browser: email and password, or the org's SSO
  signed in to https://box.pinecall.io as Ana García

~/clinica $ pinecall start                    the sandbox: Ana's own copy of the agents
~/clinica $ pinecall start --prod             production: the same host, the same key
```

The key is kept in `~/.pinecall/session.json`. The world is asked for on every request (the
CLI's `--prod`, the console's sandbox switch); a request that names none acts in the sandbox.
Production access is read from the person's row on every request, so taking it away takes
effect on the next one.

## One key per server

A server holds a key of its own, made in the console for the world it runs in, and shown once:

```bash
# console → Keys → New key → "clinica web", production
PINECALL_KEY=pc_live_…
```

```ts
const pc = new Pinecall({ apiKey: process.env.PINECALL_KEY });
```

The prefix says the world: `pc_live_` is production's, `pc_test_` the sandbox's. A server key
used for the other world is refused with the sentence that says so. An agent has no key: every
agent an app declares runs on the key of the process that runs it.

## Corners

What a request sees is a **corner**: the org, the world, and in the sandbox the person.

| key | world | corner |
|---|---|---|
| a server's | its own | the org's, in that world |
| a person's | production | the org's production |
| a person's | the sandbox | that person's own copy |

Tuning, the lexicon and knowledge are kept per corner; a person's sandbox corner falls back,
knob by knob, to the org's own. An admin may open a colleague's sandbox corner to look at it.

## Keeping test calls off production

Both worlds share the gateway and the database, never the workers. Each world has its own
fleet of workers, and every call is dispatched to the fleet of its world: a sandbox call never
runs in a production process, and a full sandbox never slows production down. Which fleet is
which is a row of the box's settings (`pinecall` and `pinecall-sandbox` unless the operator
names them), and each fleet grows, shrinks and drains on its own.

## People

An org's people are its members: invited, then active once they choose a password (or sign in
at the org's identity provider), and disabled when they leave. Disabling a member revokes their
keys at once; the row stays, since the log names who did what. A person has one password,
whatever orgs they belong to. The last active admin of an org cannot be removed.

A role is a preset of scopes (`qa`, `supervisor`, `manager`, `admin`, `developer`), given to the
keys minted for the person. A key grants only a role whose scopes it holds itself, and production
access only when it has it. An invitation's link sets the person's one password, so it is handed
to the admin only for somebody in no other org; anybody else gets it by mail alone.

## Signing in

Every way in ends with a key of the person's own for that device, answered once. The doors, their
bodies and their refusals are [protocol/accounts.md](protocol/accounts.md).

- **Email and password.** Five tries a minute per address and place; a wrong address and a wrong
  password are one sentence.
- **A one-use code** a signed-in page mints for a browser, so a key never rides a URL.
- **Pairing**: `pinecall login` prints a word, a signed-in browser approves it, the terminal
  collects its key once.
- **The org's identity provider** (OpenID Connect, with PKCE): the org names its issuer, its
  client and the email domains it admits; it may seat people nobody invited with a role, and it
  may be the only way in.
- **A sign-up**, where the operator opens them (`PINECALL_SIGNUP`): six digits mailed to the
  address, and the org is made only when they come back, with the limits the box's admission gives
  a newborn org.
- A forgotten password is a one-use link by mail, and the answer is the same whoever asks.

## Secrets

Every secret an org or the box keeps — an org's vendor keys, the box's vendor keys, a mailbox's
password, an org's identity provider secret, a carrier account — is sealed with
`PINECALL_VAULT_KEY` (Fernet) before it is written. The gateway does not start without it. To
rotate, put a new key in front of the list; secrets sealed under the old one still open.

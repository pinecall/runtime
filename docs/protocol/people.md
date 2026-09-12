# People — members, login, and the sign-up a gateway may open

Section 8 of [gateway-api.md](gateway-api.md), on its own page: the org's people as rows, the
key a person logs in for, the code a browser spends, and the one door a stranger may knock at
where its gateway opens one.

An org's people are rows, not shared keys. A key holder invites one — `POST /v1/members` with
`{email, name, role, agents?}` answers `201` with the member and a one-use `token`, shown once and
dead in a week — and the person accepts at `POST /v1/invitations/{token}` with `{password, env?,
device?}` (no key at that door; twelve characters at least, argon2id at rest), which makes them
`active` and answers their **first key**, in the one shape a key travels in: `{key, key_id, org,
label, env, scopes, subject, name, member}`. `subject` is the member's id and `scopes` the preset of
their role: `qa` · `supervisor` · `manager` · `admin` · `developer` (`types/member.py`). `GET
/v1/members` lists them; `PATCH /v1/members/{id}` replaces `role`, `agents` or `status` — `disabled`
revokes every key of theirs and refuses their login, `active` re-enables one who had a password and
never activates one still invited.

An invitation takes a **seat**, and where the org's plan caps them the door answers `429` with the
quota's own sentence and makes no row. A seat is held by everybody the org has not disabled —
invited counts, or an org at its limit could invite forever and seat them all the moment they
accepted — so disabling somebody is what frees one, and their row stays because the log names
them. Re-inviting an email the org already holds takes no second seat.

**A person's key does not hold `app` in production.** Holding an agent is a deployment, and a
deployment is a process somebody put on a box — never a laptop that happens to be logged in. So
every key minted for a person carries their role's preset in development and that preset less
`app` in production: at login, at `POST /v1/invitations/{token}`, at sign-up and at `POST
/v1/login/env`, which reads the role and not the key that asked (a production key has already
lost it). What holds a slug in production is a key issued for a machine — `POST /v1/keys` with
`{label, scopes: ["app"]}` — and it names nobody. Two people's development keys are two people's:
the registry holds a development slug per person, so nobody takes another's agent.

`POST /v1/login` takes `{org, email, password, env?, device?}` and answers a key for that person and
that device. Every wrong thing — the org, the email, the password, an invitation not yet accepted —
is one `401` sentence; a disabled member is `403`; the sixth try in a minute for one name is `429`
whatever the password. Or it takes `{code, device?}`: a key holder minted the code at `POST
/v1/login/codes` (five minutes, one use), which is how `pinecall run` prints
`?login=<code>` and a browser ends up holding a key of its own, never the org's.

## The sign-up, where its gateway opens one

**Only where `PINECALL_SIGNUP` is set**, and it is **off unless the person who runs the gateway
turns it on** — a box somebody runs for their own agents wants no stranger making an org, and is
never asked to close a door. It is its own flag and not `cloud`: a box of its own may want sign-ups,
and a cloud may close them. `GET /.well-known/pinecall` answers `{version, cloud, signup}` with no
key, which is how a page or a CLI knows whether to offer one at all.

`POST /v1/signup {org, name?, email, person, password, device?}` — no key — answers `201` with the
same key shape plus `slug`, the `member` (an `admin`, `active`, password kept) and a one-use `code`
good for `/?login=<code>`. **What the org may do is not this runtime's to say**: it asks the one
point a package beside it may have plugged a policy into (`extensions.admitted`, given the org and
the email, answering `Quotas`) and writes the answer in the same breath the org is made. With no
such package — a box of its own — the answer is no limit and no row, the same as `orgs add`. A
plan, a trial, a price: those live in the package that charges, never here. Refusals: `403` where sign-ups are shut, naming the setting; `409` a slug taken; `400` a bad slug,
email or a short password — nothing half-made — and `429` the sixth sign-up from one place in a
minute.

The console is served by this gateway, so it is the same origin as every door it uses, and the
sign-up is **its** screen (`/signup`): a site somewhere else links to it rather than posting here.
That is why this runtime sends no CORS header at all — there is no legitimate cross-origin caller.

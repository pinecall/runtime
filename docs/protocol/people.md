# People — members, login, and the sign-up on the cloud

Section 8 of [gateway-api.md](gateway-api.md), on its own page: the org's people as rows, the
key a person logs in for, the code a browser spends, and the one door a stranger may knock at.

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

`POST /v1/login` takes `{org, email, password, env?, device?}` and answers a key for that person and
that device. Every wrong thing — the org, the email, the password, an invitation not yet accepted —
is one `401` sentence; a disabled member is `403`; the sixth try in a minute for one name is `429`
whatever the password. Or it takes `{code, device?}`: a key holder minted the code at `POST
/v1/login/codes` (five minutes, one use), which is how `pinecall run` prints
`?login=<code>` and a browser ends up holding a key of its own, never the org's.

**On Pinecall's cloud alone** (`/.well-known/pinecall` says `cloud: true`), a stranger makes an org:
`POST /v1/signup {org, name?, email, person, password, device?}` — no key — answers `201` with the
same key shape plus `slug`, the `member` (an `admin`, `active`, password kept) and a one-use `code`
the landing page hands the console as `/?login=<code>`. The org is on the **free trial**, the one
set of quotas `api/signup.py` spells (forty-five minutes, two agents, one bought number …), replaced
whole by a plan later. Refusals: `403` on a box of its own, `409` a slug taken, `400` a bad slug,
email or a short password — nothing half-made — and `429` the sixth sign-up from one place in a
minute.

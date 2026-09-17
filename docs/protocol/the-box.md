# The box's own settings — `/v1/ops/mail`, `/v1/ops/brand`, `/v1/ops/signin`

Part of the [operator API](operator-api.md), on its own page: what the person who runs the box
configures **about the box itself**, from the admin page and not from a file on the machine. Each
is one row of `box_settings` (migration 0035), by name, its one secret sealed under
`PINECALL_VAULT_KEY` exactly as a provider key, a carrier and an org's own SMTP password are. The
environment still works, and is what a box with no row falls back to.

Every door here takes the operator's key — `PINECALL_OPS_KEY`, or the key of a person the box
made an operator ([operator-api.md](operator-api.md#authentication)) — and nothing else.

## The box's mail — `GET`·`PUT`·`DELETE /v1/ops/mail`, `POST /v1/ops/mail/test`

The mail server the box posts its letters through: invitations, an admin's reset, a forgotten
password ([people.md](people.md#mail-the-letters-a-box-sends)). Until here it was
`PINECALL_SMTP_URL` and `PINECALL_MAIL_FROM`, changed by whoever can ssh in and restart the
gateway. **Precedence, on every letter**: the org's own account (`PUT /v1/org/mail`), else what
the operator **stored** here, else the **environment**'s, else nothing is sent and every door
answers exactly as it did before mail existed.

`GET /v1/ops/mail` — one envelope either way, every field always there, never the password:

```json
{"configured": true, "source": "stored",
 "host": "email-smtp.us-east-1.amazonaws.com", "port": 587, "security": "starttls",
 "username": "AKIAEXAMPLE", "from": "Acme Voice <no-reply@acme.example>",
 "verified_at": "2026-09-17T10:12:03+00:00", "last_error": null}
```

`source` is `stored` for a row the operator wrote, `environment` for the two variables, `null`
with neither (`configured: false`, every other field `null`). `verified_at` and `last_error` are
what came of the last letter through the **stored** mailbox — one of the two is always null — and
are reset by a `PUT`; the environment's has no row, and its outcome is in the box's log.

`PUT /v1/ops/mail {host, port, security?, username?, password?, from}` — the same body, the same
validation and the same refusals as `PUT /v1/org/mail` (`OrgMailWanted` in
`protocol/schema/rest.json`): `security` is `starttls` (default) · `tls` · `none`, `username` and
`password` empty for a relay that asks for none, `from` an address or `Name <address>`. Replaced
whole, the password included: it is write-only. Answers the envelope above with `source:
"stored"`. `400` in the shape's own sentence; **`503 no PINECALL_VAULT_KEY: …`** on a box that
cannot seal a password, in the vault's own words. Nothing is sent to find out it works.

`DELETE /v1/ops/mail` — `204`; the environment's mailbox answers again, or nothing does. `404
this box has no stored mail server: …` when none was stored — the environment's is not dropped
here, because it is not kept here.

`POST /v1/ops/mail/test {to}` — one test letter through the **box's** mailbox, stored or the
environment's, whatever any org wired for itself, and the one door that waits for a mail server:
`{"sent": true, "error": null}` or `{"sent": false, "error": "<the server's sentence>"}`, recorded
on the stored row as a real letter's outcome is. `409 this box has no mail server: wire one at
PUT /v1/ops/mail, or set PINECALL_SMTP_URL` with neither; `400` for a `to` that is no address.

`GET /.well-known/pinecall` says `mail: true` for either box source. `pinecall-runtime doctor`'s
mail line reads the same answer the gateway does — `… (starttls), stored by the operator` or
`… from the environment` — and `--mail-to` posts through it.

## The brand — `GET`·`PUT /v1/ops/brand`

What the letters are called and painted with, and — carried on `GET /.well-known/pinecall` as
`brand` — what a sign-in page may draw before anybody holds a key. No secret, so it needs no
vault key.

```json
{"name": "Pinecall", "logo_url": null, "accent": "#5b3df5"}
```

Those are the defaults: a box told nothing is Pinecall, with its accent and no logo. `PUT
/v1/ops/brand {name?, logo_url?, accent?}` replaces the fields it names and answers the whole
brand; **a field left out keeps what it had, and an empty string goes back to the default** —
which for the logo is none at all, the only way to clear one. `name` is one line of at most 60
characters (it goes into a subject). `accent` is `#rrggbb`, six hex digits, kept lower-cased (it
is written into a style attribute of every letter). `logo_url` is an **https** URL of an image: a
mail client fetches it from wherever the reader is, and plain http is blocked or warned about in
most of them. `400` in a sentence for anything else.

In the letters: the logo stands where the wordmark stood, an `<img>` **28px tall** and as wide as
it is, `alt` the name — a client that blocks images, which is most of them until the reader says
otherwise, shows the name as text; with no logo the name IS the wordmark. The name replaces
Pinecall everywhere a letter said it (`You're invited to <org> on <name>`, `Reset your <name>
password`, `Sent by <org> through <name>`), and the accent is the button. The logo is the **one**
outside resource a letter may ever carry, at the address the operator typed themselves; with none
set a letter still fetches nothing at all.

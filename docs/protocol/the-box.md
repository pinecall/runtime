# The platform's own settings — `/v1/ops/mail`, `/v1/ops/brand`, `/v1/ops/signin`

Part of the [operator API](operator-api.md), on its own page: what the person who runs the platform
configures **about the platform itself**, from the console's Platform settings and not from a file on the
machine. Each is one row of `box_settings`, its one secret sealed under `PINECALL_VAULT_KEY` as a
provider key is. The environment still works, and is what a deployment with no row falls back to.

## The platform's mail — `GET` · `PUT` · `DELETE /v1/ops/mail`, `POST /v1/ops/mail/test`

The mail server the platform posts its letters through: invitations, resets, a forgotten password.
**Precedence, on every letter**: the org's own mailbox (`PUT /v1/org/mail`), else what the operator
stored here, else the environment's `PINECALL_SMTP_URL` and `PINECALL_MAIL_FROM`, else nothing is
sent and every endpoint answers as it did before mail existed.

```json
{"configured": true, "source": "stored", "host": "email-smtp.us-east-1.amazonaws.com", "port": 587,
 "security": "starttls", "username": "AKIAEXAMPLE", "from": "Acme <no-reply@acme.example>",
 "verified_at": "2026-09-17T10:12:03+00:00", "last_error": null}
```

`source` is `stored`, `environment`, or null with neither. `verified_at` and `last_error` are what
came of the last letter through the stored mailbox. `PUT {host, port, security, username, password,
from}` replaces the stored one whole, the password write-only, and sends nothing; `DELETE` is `204`,
`404` when none was stored. `POST /v1/ops/mail/test {to}` is one letter through the platform's mailbox,
waited for: `{sent, error}`; `409` when the platform has none.

## The brand — `GET` · `PUT /v1/ops/brand`

What the letters and the sign-in page are called and painted with, also carried on
`GET /.well-known/pinecall`: `{name, logo_url, accent}`, Pinecall's until the operator says.
`PUT {name?, logo_url?, accent?}` replaces the fields it names: a field left out keeps what it had,
an empty string goes back to the default, the only way to clear a logo. `accent` is `#rrggbb`,
`logo_url` an https URL, `name` one line of at most 60 characters; `400` in a sentence otherwise.

## Continue with Google — `GET /v1/ops/signin`, `PUT` · `DELETE /v1/ops/signin/google`

Not in this version. `GET` names the provider unwired and the redirect URI a client would be
registered with; `PUT` and `DELETE` answer `503 platform-wide sign-in is not in this version: sign in with
a password or the org's provider`. The org's own provider is `PUT /v1/org/sso`.

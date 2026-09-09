"""How every model in this package reads Meta's JSON: never strictly, in either direction."""

from pydantic import ConfigDict

# Meta adds fields to a webhook body and to an answer without notice, and sends envelopes this
# door has no use for at all (delivery receipts, template statuses, account updates). A model that
# refused an unknown key would turn every one of those into a 422 — and a webhook that keeps
# answering 4xx is a webhook Meta disables. See docs/decisions/whatsapp.md.
LENIENT = ConfigDict(extra="ignore")

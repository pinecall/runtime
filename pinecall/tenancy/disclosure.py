"""What a spoken call says before its greeting: the AI disclosure and the recording notice."""

from pinecall.wire.rest.accounts import OrgPolicy

# The platform's sentences, by the first part of the agent's language; English for any other.
CALLING_FOR = {
    "en": "This is an automated assistant calling on behalf of {org}.",
    "es": "Le habla un asistente automático en nombre de {org}.",
    "pt": "Aqui fala um assistente automático em nome de {org}.",
    "fr": "Ici un assistant automatique qui appelle au nom de {org}.",
    "de": "Hier spricht ein automatischer Assistent im Auftrag von {org}.",
    "it": "Sono un assistente automatico che chiama per conto di {org}.",
}

MAY_BE_RECORDED = {
    "en": "This call may be recorded.",
    "es": "Esta llamada puede ser grabada.",
    "pt": "Esta chamada pode ser gravada.",
    "fr": "Cet appel peut être enregistré.",
    "de": "Dieser Anruf kann aufgezeichnet werden.",
    "it": "Questa chiamata può essere registrata.",
}

FALLBACK = "en"


def disclosure_of(policy: OrgPolicy, org: str, language: str | None) -> str | None:
    """An outbound call's first sentence: the org's own, else the platform's, or None."""
    if policy.disclosure is not None:
        return policy.disclosure.strip() or None
    return CALLING_FOR[_spoken(language)].format(org=org)


def notice_of(policy: OrgPolicy, language: str | None) -> str | None:
    """The sentence a recorded call says, or None when the org turned the notice off."""
    return MAY_BE_RECORDED[_spoken(language)] if policy.recording_notice else None


def _spoken(language: str | None) -> str:
    first = (language or FALLBACK).replace("_", "-").split("-")[0].lower()
    return first if first in CALLING_FOR else FALLBACK

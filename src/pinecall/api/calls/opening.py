"""The steps every text door takes before the first word, in the order they have to happen."""

from __future__ import annotations

from dataclasses import dataclass

from pinecall.api.agents.registry import Registration
from pinecall.log.writers import Logs
from pinecall.orgs.admission import Admission
from pinecall.orgs.vault import Vault, keys_brought_by
from pinecall.providers.models import Models
from pinecall.providers.overrides import Overrides
from pinecall.session.text.session import TextSession
from pinecall.types import CallContext, ProviderKeys


# The keys travel back out because a channel may need one of its own: WhatsApp sends its answer
# with the org's Meta token, read from the very same set the model was built from, so the vault is
# asked once per call and not once per thing that call needs.
@dataclass(frozen=True)
class TextCall:
    """A text call built and not yet started: the session, and whose keys it runs on."""

    session: TextSession
    keys: ProviderKeys


# One order, two doors: the chat socket and the WhatsApp webhook both come through here, so a call
# is refused for the same reasons in the same sequence whichever way the caller arrived. Each
# refusal is the caller's to say in their own words — NoProvider from the model, QuotaExhausted
# from admission — because a socket closes with a reason and a webhook answers Meta with a 200.
async def a_text_call(
    held: Registration,
    context: CallContext,
    overrides: Overrides,
    vault: Vault | None,
    llms: Models,
    admission: Admission,
    logs: Logs,
    running: int,
) -> TextCall:
    """The config, whose keys, the model and the quota — then the session, unstarted."""
    # What the operator turned on the Pipeline screen is on this call too: a text call reads the
    # config through the same applying function the worker's config door reads it through.
    config = overrides.config_for(held.slug, held.config)
    # Asked at the moment the call opens and never held for the next one: a tenant who rotated a
    # key a minute ago is answered on the new one, and an org that brought none runs on the box's.
    brought = await keys_brought_by(vault, held.org)
    # Before anything is accepted: a process with no key for the provider the agent declared
    # refuses the call at the door rather than dying in the middle of somebody's turn.
    llm = llms(config.llm, brought)
    # The org's quotas, also before: credits.exhausted lands in the agent's log either way.
    await admission.a_call(held.org, held.slug, running)
    # logs.writing() keeps the log, so every SSE reader of this call is already subscribed to it.
    session = TextSession(context, config, logs.writing(context.call, held.slug), llm)
    return TextCall(session=session, keys=brought)

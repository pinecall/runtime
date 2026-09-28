"""How the widget presents an agent: its title, tagline, greeting, accent, autostart and theme."""

from fastapi import APIRouter

from pinecall.domain.scope import Scope
from pinecall.gateway._deps import GatewayDep, PipelineKey, TalkKey
from pinecall.tenancy import agents
from pinecall.tenancy.agents import Look
from pinecall.wire.rest.agents import WidgetSettings

router = APIRouter()


# `talk`: whoever may mint the widget's token may read how it looks.
@router.get("/v1/agents/{slug}/widget")
async def get_widget(slug: str, key: TalkKey, gateway: GatewayDep) -> WidgetSettings:
    """How the agent's widget looks in the key's world; the widget's own defaults when unset."""
    look = await agents.look_of(gateway.connections.pool, Scope(key.org, key.env), slug)
    return widget_row(look)


# `pipeline`: the same key that sets the agent's greeting and voice.
@router.put("/v1/agents/{slug}/widget")
async def put_widget(
    slug: str, body: WidgetSettings, key: PipelineKey, gateway: GatewayDep
) -> WidgetSettings:
    """Replace how the widget looks, whole; a null field is the widget's own default."""
    look = Look(
        title=body.title,
        tagline=body.tagline,
        greeting=body.greeting,
        accent=body.accent,
        autostart=body.autostart,
        theme=body.theme,
    )
    await agents.put_look(gateway.connections.pool, Scope(key.org, key.env), slug, look)
    return widget_row(look)


def widget_row(look: Look) -> WidgetSettings:
    """A look as the doors send it."""
    return WidgetSettings(
        title=look.title,
        tagline=look.tagline,
        greeting=look.greeting,
        accent=look.accent,
        autostart=look.autostart,
        theme=look.theme,
    )

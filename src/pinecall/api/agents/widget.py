"""GET and PUT /v1/agents/{slug}/widget: how the widget presents an agent, in the key's world."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from starlette.requests import HTTPConnection

from pinecall.api.deps import PipelineKeyDep, TalkKeyDep, held
from pinecall.orgs.widgets import Widget, Widgets
from pinecall_protocol.rest import WidgetSettings

router = APIRouter()


def the_widgets(connection: HTTPConnection) -> Widgets:
    """How the widget presents each agent. A Protocol, so isinstance says nothing here."""
    return held(connection, "widgets")


WidgetsDep = Annotated[Widgets, Depends(the_widgets)]


# Read with `talk`: whoever may mint the token the widget calls with may read what it shows.
@router.get("/v1/agents/{slug}/widget")
async def widget(slug: str, key: TalkKeyDep, widgets: WidgetsDep) -> WidgetSettings:
    """The agent's widget settings, or the widget's defaults when nobody set any."""
    return _as_the_wire(await widgets.of(key.org, key.env, slug))


# Written with `pipeline`: what a caller meets first — its greeting, whether it starts on open — is
# the agent's presentation, the same key that turns the agent's greeting and voice.
@router.put("/v1/agents/{slug}/widget")
async def set_the_widget(
    slug: str, said: WidgetSettings, key: PipelineKeyDep, widgets: WidgetsDep
) -> WidgetSettings:
    """The whole set replaced: a field left null is the widget's own default."""
    kept = Widget(
        title=said.title,
        tagline=said.tagline,
        greeting=said.greeting,
        accent=said.accent,
        autostart=said.autostart,
        theme=said.theme,
    )
    await widgets.put(key.org, key.env, slug, kept)
    return _as_the_wire(kept)


def _as_the_wire(widget: Widget) -> WidgetSettings:
    return WidgetSettings(
        title=widget.title,
        tagline=widget.tagline,
        greeting=widget.greeting,
        accent=widget.accent,
        autostart=widget.autostart,
        theme=widget.theme,
    )

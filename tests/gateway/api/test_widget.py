"""Tests for how the widget presents an agent."""

from tests.conftest import AGENT, Knocking, issued, postgres

WIDGET = f"/v1/agents/{AGENT}/widget"
A_LOOK = {
    "title": "Clinica Norte",
    "tagline": "Pide cita",
    "greeting": "Hola",
    "accent": "#0a7",
    "autostart": True,
    "theme": "dark",
}


@postgres
async def test_the_look_is_set_whole_per_world_and_read_with_talk(knocking: Knocking) -> None:
    pool = knocking.gateway.connections.pool
    talker = await issued(pool, knocking.org.id, "sandbox", frozenset({"talk"}))
    async with knocking.http(knocking.app["sandbox"]) as org:
        defaults = await org.get(WIDGET)
        put = await org.put(WIDGET, json=A_LOOK)
    async with knocking.http(talker) as talk:
        read = await talk.get(WIDGET)
        refused = await talk.put(WIDGET, json=A_LOOK)
    async with knocking.http(knocking.app["production"]) as production:
        other = await production.get(WIDGET)
    assert defaults.json()["autostart"] is False
    assert (put.status_code, put.json()) == (200, A_LOOK)
    assert read.json() == A_LOOK
    assert refused.status_code == 403
    assert other.json()["title"] is None


@postgres
async def test_an_accent_that_is_no_colour_is_refused(knocking: Knocking) -> None:
    async with knocking.http(knocking.app["sandbox"]) as org:
        refused = await org.put(WIDGET, json={**A_LOOK, "accent": "red; }"})
    assert refused.status_code == 400
    assert "CSS colour" in refused.json()["detail"]

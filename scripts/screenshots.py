"""Photograph every console screen docs/a-box-in-production.md shows, in both themes."""

# Run by scripts/screenshots. It is a script and not part of the package: nothing at runtime draws
# a browser. What ships is the PNG this wrote, reviewable as a diff beside the page that shows it.
#
# It signs in the way `pinecall start` signs a person in and never any other way: the key in
# ~/.pinecall/config.json mints a ONE-USE code at /v1/login/codes, and the browser spends it at
# `/a/<agent>?login=<code>` for a key of its own. No key is typed into the page, printed, or left
# in a file — and none is ever on screen, because the console shows a key by its id.

from __future__ import annotations

import argparse
import json
import sys
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from playwright.sync_api import Browser, Page, sync_playwright
from playwright.sync_api import TimeoutError as Timeout

HERE = Path(__file__).resolve().parent.parent
IMAGES = HERE / "docs" / "images"
CONFIG = Path.home() / ".pinecall" / "config.json"

# The page's own width, one device pixel per CSS pixel: a shot is the size of the viewport, so two
# runs of this script differ exactly where the console changed.
WIDTH, HEIGHT = 1440, 900
# The page is a single-page app reading several doors at once. Idle network is the signal, and the
# rest is what an animation costs — except on a screen listening to the floor, which never goes
# idle at all, so the wait for it is short on purpose.
SETTLED_MS = 1200
IDLE_MS = 6_000
PATIENCE_MS = 30_000
# A real call takes as long as a real call: the room opens, the caller speaks, the agent answers.
A_CALL_MS = 180_000
# Long enough for the simulated caller and the agent to have said something to each other.
SOME_TURNS_MS = 45_000


# A screen that shows nothing until something happens on it gets a hand first. It is done in the
# page and not through a door: what the shot has to show is the console doing its own work.
Acting = Callable[[Page], None]


def a_written_turn(page: Page) -> None:
    """Write to the agent and wait for its answer: a Chat tab nobody has used is an empty box."""
    page.get_by_role("button", name="Write", exact=True).click()
    written = page.get_by_label("Write to the agent")
    written.wait_for(state="visible", timeout=PATIENCE_MS)
    written.fill("hola, quería una cita con la doctora Vidal")
    page.click(".talk-send", timeout=PATIENCE_MS)
    page.wait_for_selector(".talk-said:not(.talk-said-you)", timeout=PATIENCE_MS)


def a_simulation(page: Page) -> None:
    """Call the agent as the caller the form names, and wait until the two have spoken."""
    page.get_by_role("button", name="Call the agent").click()
    page.wait_for_url("**/simulations/call_*", timeout=A_CALL_MS)
    # The metrics panel fills on the first measured turn: by then there is a conversation to show.
    page.wait_for_selector(".lv-meter", timeout=A_CALL_MS)
    page.wait_for_timeout(SOME_TURNS_MS)


def the_newest_ended_call(page: Page) -> None:
    """Open the newest call the floor has finished: the pane beside the list is the point of it."""
    page.click('.fl-label:has-text("Recent") + a.fl-row', timeout=PATIENCE_MS)


@dataclass(frozen=True)
class Shot:
    """One screenshot: the file it is written as, where it lives, and what to do there first."""

    name: str
    path: str
    # What to do on the screen before the shutter, when the screen is only itself once it is used.
    doing: Acting | None = None


# THE list, in the order docs/a-box-in-production.md shows them. A screen that moves house moves
# here; a screen the page stops showing goes from here and its two PNGs are deleted by hand.
# `{agent}` is the slug the shots are taken of.
SHOTS: tuple[Shot, ...] = (
    # The org's floor.
    Shot("agents", "/overview"),
    Shot("live", "/live", the_newest_ended_call),
    Shot("sessions", "/sessions"),
    # One caller open, because a roster on its own says nothing about what a persona is.
    Shot("personas", "/personas/alta-y-reserva?agent={agent}"),
    # The form with an agent and a caller already in it, as the Personas screen hands it over.
    Shot("simulations", "/simulations?agent={agent}&persona=alta-y-reserva", a_simulation),
    # One agent, tab by tab.
    Shot("talk", "/a/{agent}/talk", a_written_turn),
    Shot("calls", "/a/{agent}/calls"),
    Shot("settings", "/a/{agent}/settings"),
    Shot("pipeline", "/a/{agent}/pipeline"),
    Shot("docs", "/a/{agent}/docs"),
    Shot("memory", "/a/{agent}/memory"),
    Shot("evals", "/a/{agent}/evals"),
    # The org's own, which are nobody's agent.
    Shot("numbers", "/numbers"),
    Shot("keys", "/tokens"),
    Shot("providers", "/providers"),
    Shot("team", "/team"),
    Shot("usage", "/usage"),
)

# The two the page is drawn in, and the only two `color_scheme` is ever handed.
Theme = Literal["light", "dark"]
THEMES: tuple[Theme, ...] = ("light", "dark")


def the_key(profile: str) -> tuple[str, str]:
    """The gateway and the key of that profile of ~/.pinecall/config.json. Neither is printed."""
    if not CONFIG.is_file():
        raise SystemExit(f"no {CONFIG}: run `pinecall login <gateway>` first")
    profiles = json.loads(CONFIG.read_text(encoding="utf-8")).get("profiles", {})
    if profile not in profiles:
        raise SystemExit(f"no profile {profile} in {CONFIG}: {', '.join(sorted(profiles))}")
    kept = profiles[profile]
    return str(kept["url"]).rstrip("/"), str(kept["key"])


def a_login_code(gateway: str, key: str) -> str:
    """A one-use code standing for this key, five minutes long — what `pinecall start` prints."""
    asked = urllib.request.Request(
        f"{gateway}/v1/login/codes",
        method="POST",
        data=b"{}",
        headers={"authorization": f"Bearer {key}", "content-type": "application/json"},
    )
    with urllib.request.urlopen(asked, timeout=30) as answer:  # noqa: S310 — the gateway's own URL
        return str(json.load(answer)["code"])


def settled(page: Page) -> None:
    """Wait for the doors this screen reads to answer and for what they drew to stop moving."""
    page.wait_for_selector(".frame", timeout=PATIENCE_MS)
    try:
        page.wait_for_load_state("networkidle", timeout=IDLE_MS)
    except Timeout:
        # The console listens on a stream, so a screen watching the floor never goes idle. It is
        # drawn long before the wait gives up, and the settle below is what the shot waits on.
        pass
    page.wait_for_timeout(SETTLED_MS)


def signed_in(browser: Browser, gateway: str, key: str, agent: str, theme: Theme) -> Page:
    """A tab of its own key, in that theme: the `?login=` link spent, once, by this browser."""
    context = browser.new_context(
        viewport={"width": WIDTH, "height": HEIGHT},
        device_scale_factor=1,
        color_scheme=theme,
    )
    page = context.new_page()
    page.goto(
        f"{gateway}/a/{agent}?login={a_login_code(gateway, key)}", wait_until="domcontentloaded"
    )
    settled(page)
    return page


def taken(page: Page, shot: Shot, gateway: str, agent: str, into: Path) -> Path:
    """One screen, photographed at the size of the viewport."""
    page.goto(gateway + shot.path.replace("{agent}", agent), wait_until="domcontentloaded")
    settled(page)
    if shot.doing is not None:
        shot.doing(page)
        settled(page)
    written = into / f"{shot.name}.png"
    page.screenshot(path=written)
    return written


def main() -> int:
    parsing = argparse.ArgumentParser(description=__doc__)
    parsing.add_argument("--profile", default="box", help="the ~/.pinecall/config.json profile")
    parsing.add_argument("--agent", default="clinica-norte", help="the slug the agent shots are of")
    parsing.add_argument("--only", default="", help="comma-separated shot names, else every one")
    parsing.add_argument("--theme", default="", choices=["", *THEMES], help="else both")
    asked = parsing.parse_args()

    gateway, key = the_key(asked.profile)
    wanted = [name for name in asked.only.split(",") if name != ""]
    shots = [shot for shot in SHOTS if not wanted or shot.name in wanted]
    missing = sorted(set(wanted) - {shot.name for shot in shots})
    if missing:
        raise SystemExit(f"no such shot: {', '.join(missing)}")
    themes: tuple[Theme, ...] = (asked.theme,) if asked.theme else THEMES

    with sync_playwright() as playing:
        browser = playing.chromium.launch()
        for theme in themes:
            into = IMAGES / theme
            into.mkdir(parents=True, exist_ok=True)
            page = signed_in(browser, gateway, key, asked.agent, theme)
            for shot in shots:
                written = taken(page, shot, gateway, asked.agent, into)
                print(f"{theme:5} {shot.name:12} {written.relative_to(HERE)}")
            page.context.close()
        browser.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())

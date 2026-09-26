"""The brand a letter carries: the name, the accent and the logo the operator gave this box."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

from pinecall.orgs.box_settings import BRAND, BoxSettings
from pinecall.types import DeclarationRefused

# What a box that was told nothing is called, and the colour its one button is. They were
# constants of the frame until a box could be somebody else's product.
NAME = "Pinecall"
ACCENT = "#5b3df5"

# Six hex digits and nothing else. The value is written into a style attribute of every letter,
# so `red`, `rgb(…)` and anything with a quote or a semicolon in it is refused before it is kept.
_A_COLOUR = re.compile(r"^#[0-9a-fA-F]{6}$")

# A name goes into a subject line: a line break in one is a second header (RFC 5322 §2.2).
LONGEST_NAME = 60

NOT_A_NAME = f"a brand's name is one line of at most {LONGEST_NAME} characters"
NOT_A_COLOUR = "{said!r} is not an accent: a colour is #rrggbb, six hex digits"
NOT_A_LOGO = (
    "{said!r} is not a logo: an https:// URL of an image — a mail client fetches it from "
    "wherever the reader is, and plain http is blocked or warned about in most of them"
)


@dataclass(frozen=True)
class Brand:
    """What a letter — and, later, a sign-in page — calls this box, and how it paints it."""

    name: str = NAME
    # None: the name is the wordmark, as text, and a letter fetches nothing at all.
    logo_url: str | None = None
    accent: str = ACCENT

    def __post_init__(self) -> None:
        if not self.name.strip() or len(self.name) > LONGEST_NAME or "\n" in self.name:
            raise DeclarationRefused(NOT_A_NAME)
        if not _A_COLOUR.match(self.accent):
            raise DeclarationRefused(NOT_A_COLOUR.format(said=self.accent))
        if self.logo_url is not None and not _is_an_https_url(self.logo_url):
            raise DeclarationRefused(NOT_A_LOGO.format(said=self.logo_url))

    @property
    def as_json(self) -> dict[str, Any]:
        """The three fields, as the operator's door, the discovery and the row all spell them."""
        return {"name": self.name, "logo_url": self.logo_url, "accent": self.accent}


# A field left out keeps what it had; an EMPTY one goes back to what a box told nothing has —
# which for the logo is none at all, the only way to clear one.
def rebranded(
    brand: Brand, name: str | None = None, logo_url: str | None = None, accent: str | None = None
) -> Brand:
    """The brand with these replaced, or a refusal in a sentence. Nothing is kept by this."""
    return Brand(
        name=brand.name if name is None else (name.strip() or NAME),
        logo_url=brand.logo_url if logo_url is None else (logo_url.strip() or None),
        accent=brand.accent if accent is None else (accent.strip().lower() or ACCENT),
    )


async def the_brand(box: BoxSettings | None) -> Brand:
    """What the operator set, else what a box told nothing is: Pinecall, its accent, no logo."""
    kept = None if box is None else await box.of(BRAND)
    if kept is None:
        return Brand()
    try:
        return Brand(
            name=str(kept.value.get("name") or NAME),
            logo_url=kept.value.get("logo_url") or None,
            accent=str(kept.value.get("accent") or ACCENT),
        )
    # A row somebody wrote by hand is not a reason for an invitation not to leave.
    except DeclarationRefused:
        return Brand()


def _is_an_https_url(written: str) -> bool:
    """Whether this is https, names a host, and carries nothing that would break out of `src`."""
    parts = urlsplit(written)
    return (
        parts.scheme == "https"
        and bool(parts.hostname)
        and not any(char in written for char in " \"'<>\n")
    )

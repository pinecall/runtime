"""The one frame every letter is drawn in: the palette, the card, and the pieces inside it."""

from __future__ import annotations

from html import escape

# The brand, and nothing beside it. A letter is read in a client that supports a tenth of CSS, so
# every one of these ends up inline on the element it paints: there is no stylesheet to load, no
# image to fetch and no pixel to count. A letter that renders with the network off is the point.
WASH = "#f7f6fa"
CARD = "#ffffff"
INK = "#101014"
MUTED = "#6b6975"
HAIRLINE = "#eeedf2"
ACCENT = "#5b3df5"

# Inter where the reader has it, and the system's own everywhere else: a webfont in a letter is a
# remote request, which is exactly what this frame does not make.
FONT = "Inter,-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif"

# 560 is the width every desktop client shows without a horizontal scrollbar, and the width a
# phone scales down from without reflowing the card into a column of two words.
WIDTH = 560


def heading(text: str) -> str:
    """The one line at the top of the card that says what the letter is."""
    return (
        f'<h1 style="margin:0 0 14px;font-family:{FONT};font-size:21px;line-height:1.3;'
        f'font-weight:650;letter-spacing:-0.02em;color:{INK};">{escape(text)}</h1>'
    )


def paragraph(text: str) -> str:
    """A body paragraph. Whatever a person typed reaches it as text and never as markup."""
    return (
        f'<p style="margin:0 0 16px;font-family:{FONT};font-size:15px;line-height:1.6;'
        f'color:{INK};">{escape(text)}</p>'
    )


def small(text: str) -> str:
    """The quiet line: when a link dies, and what to do if nobody asked for it."""
    return (
        f'<p style="margin:0 0 8px;font-family:{FONT};font-size:13px;line-height:1.5;'
        f'color:{MUTED};">{escape(text)}</p>'
    )


def button(label: str, href: str) -> str:
    """The one thing to press. A table, because Outlook lays an inline-block out as it pleases."""
    return (
        '<table role="presentation" cellpadding="0" cellspacing="0" '
        'style="margin:6px 0 18px;"><tr><td>'
        f'<a href="{escape(href, quote=True)}" style="display:inline-block;background:{ACCENT};'
        f"color:#ffffff;font-family:{FONT};font-size:15px;font-weight:600;text-decoration:none;"
        f'padding:12px 22px;line-height:18px;border-radius:9px;">{escape(label)}</a>'
        "</td></tr></table>"
    )


def fallback(href: str) -> str:
    """The same link as text, for a reader whose client ate the button or the colour."""
    return (
        f'<p style="margin:0 0 16px;font-family:{FONT};font-size:13px;line-height:1.5;'
        f'color:{MUTED};word-break:break-all;">{escape(href)}</p>'
    )


# The wordmark is a WORD. A logo would be an image, an image would be a URL, and a URL in a letter
# is a request that says when it was opened and from where — which is the tracking pixel this
# frame exists without.
def a_letter(preheader: str, content: str, footer: str) -> str:
    """One letter, framed: the wordmark, the white card and the quiet line underneath."""
    return (
        "<!DOCTYPE html>\n"
        '<html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        '<meta name="color-scheme" content="light only">'
        '<meta name="supported-color-schemes" content="light"></head>'
        f'<body style="margin:0;padding:0;background:{WASH};-webkit-font-smoothing:antialiased;">'
        '<div style="display:none;max-height:0;overflow:hidden;opacity:0;">'
        f"{escape(preheader)}</div>"
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
        f'style="background:{WASH};padding:36px 14px;"><tr><td align="center">'
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
        f'style="max-width:{WIDTH}px;">'
        '<tr><td style="padding:0 6px 16px;">'
        f'<span style="font-family:{FONT};font-size:16px;font-weight:650;letter-spacing:-0.02em;'
        f'color:{INK};">pinecall</span></td></tr>'
        f'<tr><td style="background:{CARD};border:1px solid {HAIRLINE};border-radius:14px;'
        f'padding:30px 30px 24px;">{content}</td></tr>'
        f'<tr><td style="padding:18px 6px 0;font-family:{FONT};font-size:12px;line-height:1.6;'
        f'color:{MUTED};">{escape(footer)}</td></tr>'
        "</table></td></tr></table></body></html>"
    )

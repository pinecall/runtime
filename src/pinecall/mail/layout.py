"""The one frame every letter is drawn in: the palette, the card, and the pieces inside it."""

from __future__ import annotations

from html import escape

from pinecall.mail.brand import Brand

# The frame's own palette; the ACCENT and the name are the operator's (mail/brand.py). A letter
# is read in a client that supports a tenth of CSS, so every one of these ends up inline on the
# element it paints: there is no stylesheet to load and no pixel to count. Unless the operator
# gave the box a logo, there is no image to fetch either, and the letter renders with the
# network off.
WASH = "#f7f6fa"
CARD = "#ffffff"
INK = "#101014"
MUTED = "#6b6975"
HAIRLINE = "#eeedf2"

# How tall the logo is drawn, whatever it is: a fixed height and a free width, so a wide wordmark
# and a square mark both sit on the line the name used to.
LOGO_HEIGHT = 28

# Inter where the reader has it, and the system's own everywhere else: a webfont in a letter is a
# remote request, which is exactly what this frame does not make.
FONT = "Inter,-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif"

# A code a person copies letter by letter: fixed-width, so a 1 and an l never look alike.
MONO = "SFMono-Regular,Menlo,Consolas,'Liberation Mono',monospace"

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


def button(label: str, href: str, accent: str) -> str:
    """The one thing to press. A table, because Outlook lays an inline-block out as it pleases."""
    return (
        '<table role="presentation" cellpadding="0" cellspacing="0" '
        'style="margin:6px 0 18px;"><tr><td>'
        f'<a href="{escape(href, quote=True)}" style="display:inline-block;background:{accent};'
        f"color:#ffffff;font-family:{FONT};font-size:15px;font-weight:600;text-decoration:none;"
        f'padding:12px 22px;line-height:18px;border-radius:9px;">{escape(label)}</a>'
        "</td></tr></table>"
    )


# The v1 letter's box: the code large, spaced and centred on the wash, the one thing the eye
# finds. The left padding matches the letter-spacing, which a browser also adds after the last
# digit, so the digits sit in the middle and not a little to the left of it.
def code_box(code: str) -> str:
    """A code to copy, alone in its own box."""
    return (
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
        'style="margin:8px 0 20px;"><tr>'
        f'<td align="center" style="background:{WASH};border:1px solid {HAIRLINE};'
        'border-radius:12px;padding:22px 16px;">'
        f'<div style="font-family:{MONO};font-size:34px;line-height:1;font-weight:600;'
        f'letter-spacing:10px;padding-left:10px;color:{INK};">{escape(code)}</div>'
        "</td></tr></table>"
    )


def fallback(href: str) -> str:
    """The same link as text, for a reader whose client ate the button or the colour."""
    return (
        f'<p style="margin:0 0 16px;font-family:{FONT};font-size:13px;line-height:1.5;'
        f'color:{MUTED};word-break:break-all;">{escape(href)}</p>'
    )


# The top of a letter is the operator's LOGO or nothing. A box told nothing used to write its name
# there as a word, which put "Pinecall" in plain type over every letter of a box nobody had
# branded — a header that says less than the footer already does. A logo is an image, an image is
# a URL, and a URL in a letter is a request that says when it was opened and from where — so the
# only one this frame ever makes is to the address the operator of this box typed themselves,
# and with none set it makes none. `alt` is the name: a client that blocks images, which is most
# of them until the reader says otherwise, shows the word in the logo's place.
def wordmark(brand: Brand) -> str:
    """The row above the card: the operator's logo at a fixed height, or no row at all."""
    if brand.logo_url is None:
        return ""
    return (
        '<tr><td style="padding:0 6px 16px;">'
        f'<img src="{escape(brand.logo_url, quote=True)}" '
        f'alt="{escape(brand.name, quote=True)}" '
        f'height="{LOGO_HEIGHT}" style="display:block;height:{LOGO_HEIGHT}px;width:auto;'
        f"border:0;outline:none;text-decoration:none;font-family:{FONT};font-size:16px;"
        f'font-weight:650;color:{INK};">'
        "</td></tr>"
    )


def a_letter(preheader: str, content: str, footer: str, brand: Brand) -> str:
    """One letter, framed: the logo when there is one, the white card, the quiet line underneath."""
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
        f"{wordmark(brand)}"
        f'<tr><td style="background:{CARD};border:1px solid {HAIRLINE};border-radius:14px;'
        f'padding:30px 30px 24px;">{content}</td></tr>'
        f'<tr><td style="padding:18px 6px 0;font-family:{FONT};font-size:12px;line-height:1.6;'
        f'color:{MUTED};">{escape(footer)}</td></tr>'
        "</table></td></tr></table></body></html>"
    )

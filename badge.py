#!/usr/bin/env python3
"""A README badge as an SVG, flat style (narthelix/ops#2089 T19). Stdlib only.

    python3 badge.py coverage 87.4% green > coverage.svg

The badges live on each repository's own `badges` branch and the README links them by their full
`https://github.com/<org>/<repo>/raw/badges/<file>.svg` address -- measured 2026-10-20 in a
private repository: that form renders for anyone who can read the repository; a relative path
(`../badges/x.svg`, `/<org>/<repo>/raw/badges/x.svg`) resolves against the branch being viewed
and breaks. No third party, no public Gist, no token in the README.
"""

from __future__ import annotations

import sys
from xml.sax.saxutils import escape

COLORS = {
    "green": "#3fb950", "yellowgreen": "#9bbe1e", "yellow": "#d4a72c", "orange": "#e3772b",
    "red": "#d73a49", "grey": "#6e7681", "blue": "#1f6feb",
}

# Verdana 11px advance widths, close enough for a badge (the widest common glyphs are listed;
# everything else gets the average). Width only has to stop text clipping, not be typographic.
_WIDE = {"m": 10.7, "w": 9.0, "M": 10.0, "W": 11.2, "%": 12.0, "@": 11.0}
_NARROW = {"i": 3.1, "l": 3.1, "j": 3.4, "I": 4.2, "f": 3.9, "t": 4.3, "r": 4.7, ".": 3.6,
           ",": 3.6, ":": 4.5, " ": 3.9, "/": 4.6, "(": 4.6, ")": 4.6, "·": 3.6}


def text_width(text: str) -> int:
    return int(sum(_WIDE.get(c) or _NARROW.get(c) or 7.0 for c in text)) + 10


def coverage_color(percent: float) -> str:
    """An absolute scale: the badge shows the number, the repository's own floor is what gates."""
    if percent >= 85:
        return "green"
    if percent >= 70:
        return "yellowgreen"
    if percent >= 50:
        return "yellow"
    if percent >= 30:
        return "orange"
    return "red"


def render(label: str, message: str, color: str) -> str:
    fill = COLORS.get(color, color if color.startswith("#") else COLORS["grey"])
    lw, mw = text_width(label), text_width(message)
    w = lw + mw
    lab, msg = (escape(t, {"\"": "&quot;"}) for t in (label, message))
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="20" role="img" '
        f'aria-label="{lab}: {msg}"><title>{lab}: {msg}</title>'
        f'<clipPath id="r"><rect width="{w}" height="20" rx="3" fill="#fff"/></clipPath>'
        f'<g clip-path="url(#r)"><rect width="{lw}" height="20" fill="#555"/>'
        f'<rect x="{lw}" width="{mw}" height="20" fill="{fill}"/></g>'
        f'<g fill="#fff" text-anchor="middle" font-family="Verdana,Geneva,DejaVu Sans,sans-serif" '
        f'font-size="11"><text x="{lw / 2}" y="14">{lab}</text>'
        f'<text x="{lw + mw / 2}" y="14">{msg}</text></g></svg>\n'
    )


if __name__ == "__main__":
    if len(sys.argv) != 4:
        sys.exit("usage: badge.py LABEL MESSAGE COLOR")
    sys.stdout.write(render(*sys.argv[1:]))

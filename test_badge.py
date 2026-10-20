#!/usr/bin/env python3
"""Tests for badge.py — run with `python3 test_badge.py`."""

from __future__ import annotations

import sys
import xml.etree.ElementTree as ET

import badge


def main() -> int:
    fails = 0

    def check(name: str, ok: bool, detail: object = ""):
        nonlocal fails
        print(f"{'ok   ' if ok else 'FAIL '} {name}" + ("" if ok else f": {detail}"))
        fails += not ok

    svg = badge.render("coverage", "87.4%", "green")
    root = ET.fromstring(svg)
    check("well-formed SVG with its text", root.tag.endswith("svg") and "87.4%" in svg, svg[:120])
    check("named colour resolved", badge.COLORS["green"] in svg)
    check("hex colour passed through", "#123456" in badge.render("a", "b", "#123456"))
    check("unknown colour falls back to grey", badge.COLORS["grey"] in badge.render("a", "b", "nope"))
    hostile = badge.render('a<b>&"', "<script>", "green")
    ET.fromstring(hostile)
    check("label and message are escaped", "<script>" not in hostile and "&lt;script&gt;" in hostile)
    check("width grows with the text",
          badge.text_width("gece: 30/30 geçti") > badge.text_width("ok"))
    check("coverage scale",
          [badge.coverage_color(p) for p in (90, 75, 55, 35, 10)]
          == ["green", "yellowgreen", "yellow", "orange", "red"])

    print(f"\n{'FAILED' if fails else 'all passed'} ({fails} failure(s))")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())

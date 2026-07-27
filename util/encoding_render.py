#!/usr/bin/env python3
"""
util/encoding_render.py — render encoding.yaml back to encoding.md style.

The YAML is the structured source of truth for the pairing packet encoding
(fields, per-frame bit layouts, prose). This tool regenerates the human-facing
markdown with ASCII-art bit tables from it, so the two never drift and so the
round-trip can be diffed against the hand-written encoding.md.

Usage:
    python3 util/encoding_render.py            # print to stdout
    python3 util/encoding_render.py -o FILE     # write to FILE
    python3 util/encoding_render.py --check     # diff against encoding.md
"""
from __future__ import annotations
import argparse
import difflib
import os
import sys

import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# The header block is invariant across every frame; emit it verbatim.
HEADER = [
    "┌─┬─────────┬─┬─────────┬─────────┬──────┬─────────┬──────────┐",
    "│h│ funct5  │g│  rs2    │  rs1    │funct3│   rd    │  opcode  │",
    "└─┴─────────┴─┴─────────┴─────────┴──────┴─────────┴──────────┘",
]
OPCODE_TAIL = "opcode5│10"          # appended (with a leading │) to every data row


def _center(text, w):
    """Center with any odd extra space on the RIGHT (matches encoding.md's
    convention; Python's str.center biases the other way on odd widths)."""
    pad = max(0, w - len(text))
    left = pad // 2
    return " " * left + text + " " * (pad - left)


def _cell(text):
    """Split a cell token into (text, span). 'imma[5:0]*2' -> ('imma[5:0]', 2)."""
    if "*" in text:
        body, _, n = text.rpartition("*")
        if n.isdigit():
            return body, int(n)
    return text, 1


def render_row(cells, widths, tag=None):
    rendered, pos = [], 0
    for token in cells:
        text, span = _cell(token)
        w = sum(widths[pos:pos + span]) + (span - 1)   # absorb internal separators
        rendered.append(_center(text, w))
        pos += span
    if pos != len(widths):
        raise ValueError(f"row spans {pos} columns, expected {len(widths)}: {cells}")
    line = "│" + "│".join(rendered) + "│" + OPCODE_TAIL + "│"
    if tag:
        line += f" ({tag})"
    return line


BANNER = "<!-- Generated from encoding.yaml by util/encoding_render.py — do not edit by hand. -->"


def render(spec) -> str:
    widths = spec["grid"]["display"]
    out: list[str] = [BANNER, ""]
    for node in spec["doc"]:
        if "md" in node:
            out.append(node["md"].rstrip("\n"))
            out.append("")
        elif "frame" in node:
            f = node["frame"]
            out.append("#" * f.get("level", 2) + " " + f["name"])
            out.append("")
            for ln in f["asm"].rstrip("\n").split("\n"):
                out.append("    " + ln if ln.strip() else "")
            out.append("")
            out.extend(HEADER)
            for row in f["rows"]:
                if isinstance(row, dict):
                    out.append(render_row(row["c"], widths, row.get("tag")))
                else:
                    out.append(render_row(row, widths))
            if f.get("notes"):
                out.append("")
                out.append(f["notes"].rstrip("\n"))
            out.append("")
    # collapse any run of >2 blank lines to a single blank, tidy trailing ws
    text = "\n".join(out)
    while "\n\n\n\n" in text:
        text = text.replace("\n\n\n\n", "\n\n\n")
    return text.rstrip("\n") + "\n"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--yaml", default=os.path.join(ROOT, "encoding.yaml"))
    ap.add_argument("-o", "--output")
    ap.add_argument("--check", action="store_true",
                    help="diff the render against encoding.md and report")
    args = ap.parse_args()

    with open(args.yaml) as fh:
        spec = yaml.safe_load(fh)
    text = render(spec)

    if args.check:
        md_path = os.path.join(ROOT, "encoding.md")
        with open(md_path) as fh:
            original = fh.read()
        diff = list(difflib.unified_diff(
            original.splitlines(True), text.splitlines(True),
            fromfile="encoding.md", tofile="encoding.yaml->render"))
        if not diff:
            print("IDENTICAL: render matches encoding.md byte-for-byte.")
        else:
            sys.stdout.writelines(diff)
            adds = sum(1 for d in diff if d.startswith("+") and not d.startswith("+++"))
            dels = sum(1 for d in diff if d.startswith("-") and not d.startswith("---"))
            print(f"\n# {dels} lines removed / {adds} lines added vs encoding.md")
        return

    if args.output:
        with open(args.output, "w") as fh:
            fh.write(text)
    else:
        sys.stdout.write(text)


if __name__ == "__main__":
    main()

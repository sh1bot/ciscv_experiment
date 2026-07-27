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
import re
import sys

import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Every data row carries these two invariant tail fields after rd:
#   opcode5 (5-bit opcode) and the 2-bit packet marker "10", shown as bits.
TAIL_CELLS = ["opcode5", "1 0"]

# Header labels for the seven variable columns + the merged opcode field
# (opcode = opcode5 + marker). funct3's label is shown as "fn3" because a
# 3-bit box (2*3-1 = 5 cols) can't hold "funct3".
HEADER_LABELS = ["h", "funct5", "g", "rs2", "rs1", "fn3", "rd", "opcode"]


def _center(text, w):
    """Center with any odd extra space on the RIGHT."""
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


def _spanned(widths, pos, span):
    """Display width of a cell spanning `span` columns from `pos`
    (sum of the column widths plus the internal separators they absorb)."""
    return sum(widths[pos:pos + span]) + (span - 1)


def header_lines(colwidths):
    """The boxed 3-line header, sized from the column display widths.
    opcode5 and the marker are merged into a single 'opcode' box."""
    hw = list(colwidths[:7]) + [_spanned(colwidths, 7, 2)]   # merge opcode5+marker
    top = "┌" + "┬".join("─" * w for w in hw) + "┐"
    mid = "│" + "│".join(_center(l, w) for l, w in zip(HEADER_LABELS, hw)) + "│"
    bot = "└" + "┴".join("─" * w for w in hw) + "┘"
    return [top, mid, bot]


def render_row(cells, colwidths, tag=None):
    rendered, pos = [], 0
    for token in list(cells) + TAIL_CELLS:
        text, span = _cell(token)
        rendered.append(_center(text, _spanned(colwidths, pos, span)))
        pos += span
    if pos != len(colwidths):
        raise ValueError(f"row spans {pos} columns, expected {len(colwidths)}: {cells}")
    line = "│" + "│".join(rendered) + "│"
    if tag:
        line += f" ({tag})"
    return line


BANNER = "<!-- Generated from encoding.yaml by util/encoding_render.py — do not edit by hand. -->"


def render(spec) -> str:
    # column display widths: the seven variable columns, then opcode5(5 bits ->
    # 9) and the marker(2 bits -> 3), all following width = 2*bits - 1.
    widths = list(spec["grid"]["display"]) + [9, 3]
    header = header_lines(widths)
    out: list[str] = [BANNER, ""]
    for node in spec["doc"]:
        if "md" in node:
            out.append(node["md"].rstrip("\n"))
            out.append("")
        elif "reserved" in node:
            out.append("# Reserved register encodings")
            out.append("")
            for e in node["reserved"]:
                regs = "/".join(e["regs"])
                alt = f" (or {'/'.join(e['alt'])})" if e.get("alt") else ""
                note = " ".join(e["note"].split())
                out.append(f" * **{e['where']} — {regs}{alt}** "
                           f"[{e['status']}]: {note}")
            out.append("")
        elif "frame" in node:
            f = node["frame"]
            out.append("#" * f.get("level", 2) + " " + f["name"])
            out.append("")
            for i, pair in enumerate(f["templates"]):
                if i:
                    out.append("")                 # blank line between template pairs
                for ln in pair:
                    out.append("    " + ln)
            out.append("")
            out.extend(header)
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


# --- asm <-> row correspondence lint --------------------------------------
# Operand meta-variables that may appear in an asm instruction. tmp (=x31) and
# named architectural registers are IMPLICIT and are not encoded in a row.
_OPERAND = re.compile(
    r"\b(rs1a|rs2a|rs1b|rs2b|rsda|rsdb|rda|rdb|rbase|imma|immb|imm|tmp)\b")
_IMPLICIT = {"tmp", "sp", "ra", "zero", "x0", "x31"}
_NON_OPERAND_CELLS = {"h", "g", "i", "fn3", "opcode5", "10"}


def asm_pairs(frame):
    """The frame's (line_a, line_b) template pairs."""
    return [[ln.strip() for ln in pair] for pair in frame["templates"]]


def asm_operands(pair):
    """Encoded operand names used by an asm pair (implicit regs removed)."""
    ops = set()
    for line in pair:
        _, _, rest = line.partition(" ")       # drop the opcode meta-variable
        ops |= set(_OPERAND.findall(rest))
    return ops - _IMPLICIT


def row_operands(cells):
    ops = set()
    for cell in cells:
        name = cell.split("*")[0].split("[")[0]
        if name in _NON_OPERAND_CELLS:
            continue
        if re.fullmatch(r"[01 ]+", cell):       # fixed bit pattern e.g. "0 0 0 0 1"
            continue
        ops.add(name)
    return ops


def lint(spec):
    problems = 0
    for node in spec["doc"]:
        if "frame" not in node:
            continue
        f = node["frame"]
        pairs = asm_pairs(f)
        asm_ops = set().union(*(asm_operands(p) for p in pairs)) if pairs else set()
        rows = [r["c"] if isinstance(r, dict) else r for r in f["rows"]]
        row_ops = set().union(*(row_operands(r) for r in rows)) if rows else set()

        notes = f.get("notes", "") or ""
        bad_pair = [p for p in pairs if len(p) != 2]
        missing = asm_ops - row_ops           # operand in asm, never encoded
        spurious = row_ops - asm_ops          # field in a row, not in any asm
        # A small immediate carried in the g/h bits shows as "g"/"h" cells, not
        # a named field; accept it when the frame's notes document that use.
        documented = {m for m in missing
                      if m.startswith("imm") and re.search(rf"\b{m}\b", notes)}
        missing -= documented
        if bad_pair or missing or spurious:
            problems += 1
            print(f"✗ {f['name']}")
            if bad_pair:
                print(f"    asm chunks that aren't 2-line pairs: "
                      f"{[len(p) for p in bad_pair]}")
            if missing:
                print(f"    operands in asm but NOT encoded in any row: {sorted(missing)}")
            if spurious:
                print(f"    fields in rows but NOT used by any asm line: {sorted(spurious)}")
        else:
            extra = f"  [{','.join(sorted(documented))} in g/h per notes]" if documented else ""
            print(f"✓ {f['name']}  ({len(pairs)} pair(s), {len(rows)} row(s)){extra}")
    print(f"\n{problems} frame(s) with correspondence problems.")
    return problems


# --- opcode-field capacity ------------------------------------------------
# The opcode namespace is opcode5(5) + funct3(3) + g(1) + h(1) = 10 bits =
# 1024 entries, shared by all frames as a prefix code. g and h double as the
# immediate-extension bits, so an op that needs a wide immediate takes MULTIPLE
# entries (one per immediate sub-range: 2 with g, 4 with g+h) rather than one.
# The (opA, opB) count below is the BASE demand (one entry per op-combo,
# assuming a base-width immediate); wide-immediate sub-range entries add to it.
OPCODE_NAMESPACE = 1024


def opcode_demand(ops):
    """How many distinct (opA, opB) codepoints a frame's declared ops need."""
    if not ops:
        return None
    if "tuples" in ops:
        return len(ops["tuples"])
    if "same" in ops:                    # both slots the same op
        return len(ops["same"])
    return len(ops.get("a", [])) * len(ops.get("b", []))


def opcodes(spec):
    print(f"{'frame':44} {'shape':>13} {'codepoints':>10}")
    print("-" * 70)
    total, missing = 0, []
    for node in spec["doc"]:
        if "frame" not in node:
            continue
        f = node["frame"]
        ops = f.get("ops")
        if not ops:
            missing.append(f["name"]); continue
        d = opcode_demand(ops)
        total += d
        if "tuples" in ops:
            shape = f"{len(ops['tuples'])} tuples"
        elif "same" in ops:
            shape = f"{len(ops['same'])} same"
        else:
            shape = f"{len(ops['a'])}×{len(ops['b'])}"
        print(f"{f['name']:44} {shape:>13} {d:10}")
    print("-" * 70)
    print(f"{'TOTAL base (opA×opB) opcode demand':44} {'':>13} {total:10}")
    spare = OPCODE_NAMESPACE - total
    print(f"\nopcode namespace = opcode5(5)+funct3(3)+g(1)+h(1) = {OPCODE_NAMESPACE} entries.")
    if total <= OPCODE_NAMESPACE:
        print(f"Base demand {total} FITS with {spare} entries spare.")
        print(f"Those {spare} spare entries are what wide-immediate ops draw on: each\n"
              f"immediate sub-range beyond the base field width costs one extra entry\n"
              f"(x2 via g, x4 via g+h). Whether base+sub-ranges stays <= {OPCODE_NAMESPACE}\n"
              f"depends on how many wide-immediate variants each frame declares.")
    else:
        print(f"Base demand {total} OVER by {total-OPCODE_NAMESPACE} before any "
              f"immediate sub-range entries.")
    print("This is DECLARED demand (every allowed op-combo); real corpus usage is\n"
          "far sparser (see analysis/encoding_verify + encoding_budget).")
    if missing:
        print(f"\nFrames with no ops declared: {missing}")
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--yaml", default=os.path.join(ROOT, "encoding.yaml"))
    ap.add_argument("-o", "--output")
    ap.add_argument("--check", action="store_true",
                    help="diff the render against encoding.md and report")
    ap.add_argument("--lint", action="store_true",
                    help="check asm<->row operand correspondence")
    ap.add_argument("--opcodes", action="store_true",
                    help="report per-frame opcode-field demand vs namespace")
    args = ap.parse_args()

    if args.lint:
        with open(args.yaml) as fh:
            sys.exit(1 if lint(yaml.safe_load(fh)) else 0)

    if args.opcodes:
        with open(args.yaml) as fh:
            sys.exit(opcodes(yaml.safe_load(fh)))

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

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
            if node.get("note"):
                out.append("")
                out.append(" ".join(node["note"].split()))
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
    grid = spec["grid"]
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
        # A declared op width must fit the slot's actual immediate field.
        overwide = []
        for slot in ("a", "b"):
            _, full = imm_field_bits(f, grid, slot)
            for c in f.get("ops") or []:
                for e in c.get(slot, []):
                    b = op_bits(e)
                    if b and b > full:
                        overwide.append(f"{op_name(e)}({slot}) wants {b}b > {full}b field")
        if bad_pair or missing or spurious or overwide:
            problems += 1
            print(f"✗ {f['name']}")
            if bad_pair:
                print(f"    asm chunks that aren't 2-line pairs: "
                      f"{[len(p) for p in bad_pair]}")
            if missing:
                print(f"    operands in asm but NOT encoded in any row: {sorted(missing)}")
            if spurious:
                print(f"    fields in rows but NOT used by any asm line: {sorted(spurious)}")
            if overwide:
                print(f"    op immediate wider than its field: {overwide}")
        else:
            extra = f"  [{','.join(sorted(documented))} in g/h per notes]" if documented else ""
            print(f"✓ {f['name']}  ({len(pairs)} pair(s), {len(rows)} row(s)){extra}")
    print(f"\n{problems} frame(s) with correspondence problems.")
    return problems


# --- opcode-field capacity ------------------------------------------------
# The opcode namespace is a fixed pool of 1024 codepoints shared by all frames
# as a prefix code. An op whose immediate range is wider than its frame's base
# range is distinguished within this namespace rather than by a dedicated field
# bit, so each extra bit of range doubles the codepoints that op occupies (an
# extended-range op costs 2, one bit wider again costs 4, ...). The (opA, opB)
# count below is the BASE demand (one codepoint per combo at the base range);
# extended-range ops add to it.
OPCODE_NAMESPACE = 1024


def op_name(entry):
    """Mnemonic of an op-list entry, which is either a bare string or a
    {op: name, imm: {...}} mapping (a width-annotated op, usually via anchor)."""
    return entry if isinstance(entry, str) else entry["op"]


def op_bits(entry):
    """Declared absolute immediate width of an op entry, or None for a bare op
    (bare = default, which flexes to the frame's base field)."""
    if isinstance(entry, dict):
        return (entry.get("imm") or {}).get("bits")
    return None


def opcode_demand(ops):
    """BASE (opA, opB) combos a frame's ops allow: Σ over clusters of a×b.
    Ignores immediate width — see opcode_codepoints for the ext-aware count."""
    if not ops:
        return None
    return sum(len(c.get("a", [])) * len(c.get("b", [])) for c in ops)


def imm_field_bits(frame, grid, slot):
    """(base, full) immediate-range widths for a slot, read from the frame's
    base (non-SP) rows: `full` is the whole slice the layout holds, `base` is
    the part in the dedicated field columns (the two low opcode-word columns
    hold the extension). slot 'a'→imma, 'b'→immb; a shared `imm` counts for
    either. Range above the base (full-base) is what an op pays for in extra
    codepoints rather than field bits."""
    cols, bits = grid["columns"], grid["bits"]
    gi, hi = cols.index("g"), cols.index("h")
    names = {"a": {"imma", "imm"}, "b": {"immb", "imm"}}[slot]
    base = full = 0
    for row in frame.get("rows", []):
        tag = row.get("tag") if isinstance(row, dict) else None
        if tag == "SP-relative":
            continue
        cells = row["c"] if isinstance(row, dict) else row
        pos = 0
        for cell in cells:
            body, span = _cell(cell)
            if body.split("[")[0] in names:
                cspan = range(pos, pos + span)
                tot = sum(bits[c] for c in cspan)
                gh = sum(bits[c] for c in cspan if c in (gi, hi))
                full, base = max(full, tot), max(base, tot - gh)
            pos += span
    return base, full


def _slot_weight(op_list, base):
    """Codepoints a slot's ops occupy: Σ 2^ext, where ext = the bits of range an
    op's declared width needs above the base (0 for a bare/base-range op, so
    register-form ops never inflate the count)."""
    w = 0
    for e in op_list:
        b = op_bits(e)
        w += 1 << (max(0, b - base) if b else 0)
    return w


def opcode_codepoints(frame, grid):
    """Real codepoint demand: Σ over clusters of weight(a)×weight(b), where each
    slot weight sums 2^ext per op. Factors per slot because the A and B
    immediates extend their range independently."""
    ops = frame.get("ops")
    if not ops:
        return None
    base_a, _ = imm_field_bits(frame, grid, "a")
    base_b, _ = imm_field_bits(frame, grid, "b")
    return sum(_slot_weight(c.get("a", []), base_a)
               * _slot_weight(c.get("b", []), base_b) for c in ops)


def opcodes(spec):
    grid = spec["grid"]
    print(f"{'frame':44} {'shape':>13} {'base':>6} {'codepoints':>10}")
    print("-" * 78)
    base_total, cp_total, missing, wide = 0, 0, [], []
    for node in spec["doc"]:
        if "frame" not in node:
            continue
        f = node["frame"]
        ops = f.get("ops")
        if not ops:
            missing.append(f["name"]); continue
        d = opcode_demand(ops)
        cp = opcode_codepoints(f, grid)
        base_total += d; cp_total += cp
        if len(ops) == 1:
            shape = f"{len(ops[0]['a'])}×{len(ops[0]['b'])}"
        else:
            shape = f"{len(ops)} clusters"
        flag = "  wide-imm" if cp > d else ""
        if cp > d:
            wide.append(f["name"])
        print(f"{f['name']:44} {shape:>13} {d:6} {cp:10}{flag}")
    print("-" * 78)
    print(f"{'TOTAL':44} {'':>13} {base_total:6} {cp_total:10}")
    spare = OPCODE_NAMESPACE - cp_total
    print(f"\nopcode namespace = opcode5(5)+funct3(3)+g(1)+h(1) = {OPCODE_NAMESPACE} entries.")
    print(f"'base' = Σ a×b combos; 'codepoints' = Σ weight(a)×weight(b) with each\n"
          f"op weighted 2^ext (ext = immediate bits above the frame's base range).\n"
          f"Each extra bit of range doubles that op's codepoints (extended-range\n"
          f"op = 2, two bits wider = 4, ...); bare and register-form ops cost 1,\n"
          f"so they never inflate the count.")
    if cp_total <= OPCODE_NAMESPACE:
        print(f"Codepoint demand {cp_total} FITS with {spare} spare.")
    else:
        print(f"Codepoint demand {cp_total} OVER by {cp_total-OPCODE_NAMESPACE}.")
    if wide:
        print(f"Frames with extended-range immediates: {', '.join(wide)}")
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

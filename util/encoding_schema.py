#!/usr/bin/env python3
"""
util/encoding_schema.py — structural validation of encoding.yaml (TODO A2).

The render/lint/accounting pipeline checks what it computes over; this checks
the document itself, so a malformed frame fails loudly at the gate instead of
being caught later by a rendering exception or not at all.  Evidence this was
needed: a row naming an unknown immediate field once cost a frame its
displacement field, a six-column row was caught only by a rendering exception,
and a frame edit once left rows and templates mismatched half-way.

Checks, per the A2 list:
  * grid arithmetic: columns/bits/display agree, bits + opcode5 + marker = 32
  * every row spans exactly the grid's columns, net of *N span markers
  * every row cell resolves: h/g/fn3, a literal bit pattern, a known
    immediate field, or an operand named in the frame's own templates
  * frame names unique, non-empty; budget a power of two
  * immediate contracts well-formed ({bits, signed, scale} with sane values)
  * measures_also / rules_py_names shaped correctly

Template<->ops agreement (unencodable clusters, missing/spurious operands) is
lint's job in encoding_render and already gated via encoding_assign.

Usage:  python3 util/encoding_schema.py [encoding.yaml]     exits 1 on errors
"""
import os
import re
import sys

import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from encoding_render import (ALL_IMM_NAMES, _cell, asm_operands, asm_pairs,
                             op_imm, op_name)

_LITERAL = re.compile(r"^[01o.](?: [01o.])*$")
_BRACKET = re.compile(r"^\[\d+:\d+(?:\|\d+:\d+)*\]$")
_FIXED_CELLS = {"h", "g", "fn3"}


def _frame_operands(frame):
    """Operand names the frame's templates declare (registers and imms)."""
    pairs = asm_pairs(frame) if frame.get("templates") else []
    return set().union(*(asm_operands(p) for p in pairs)) if pairs else set()


def _check_row(frame, cells, ncols, ops_in_templates, errs):
    name = frame.get("name")
    span = sum(_cell(c)[1] for c in cells)
    if span != ncols:
        errs.append(f"{name}: row {cells} spans {span} columns, grid has {ncols}")
    for cell in cells:
        body, _ = _cell(cell)
        stem, _, spec = body.partition("[")
        if stem in _FIXED_CELLS or _LITERAL.match(body):
            continue
        if stem in ALL_IMM_NAMES:
            if spec and not _BRACKET.match("[" + spec):
                errs.append(f"{name}: malformed immediate spec '{cell}'")
            continue
        if stem.startswith("imm"):
            # imm_field_bits also hard-errors on this; repeated here so the
            # schema gate reports every problem in one pass.
            errs.append(f"{name}: unknown immediate field '{stem}'")
            continue
        if stem not in ops_in_templates:
            errs.append(f"{name}: row cell '{cell}' names no template operand "
                        f"(templates use {sorted(ops_in_templates)})")


def _check_contract(name, entry, errs):
    c = op_imm(entry)
    if not c:
        return
    extra = set(c) - {"bits", "signed", "scale"}
    if extra:
        errs.append(f"{name}: op {op_name(entry)} contract has unknown "
                    f"keys {sorted(extra)}")
    bits = c.get("bits")
    if not isinstance(bits, int) or not 1 <= bits <= 13:
        errs.append(f"{name}: op {op_name(entry)} declares bits={bits!r} "
                    f"(expected int 1..13)")
    if "signed" in c and not isinstance(c["signed"], bool):
        errs.append(f"{name}: op {op_name(entry)} signed={c['signed']!r} "
                    f"is not a bool")
    scale = c.get("scale")
    if scale is not None and (not isinstance(scale, int) or scale < 1):
        errs.append(f"{name}: op {op_name(entry)} scale={scale!r} "
                    f"(expected positive int)")


def validate(spec):
    """Return a list of error strings; empty means structurally valid."""
    errs = []
    grid = spec.get("grid")
    if not grid:
        return ["no grid section"]
    cols = grid.get("columns") or []
    bits = grid.get("bits") or []
    disp = grid.get("display") or []
    if not (len(cols) == len(bits) == len(disp)):
        errs.append(f"grid: columns({len(cols)})/bits({len(bits)})/"
                    f"display({len(disp)}) lengths disagree")
    if sum(bits) + 5 + 2 != 32:
        errs.append(f"grid: bits sum to {sum(bits)} + opcode5(5) + marker(2) "
                    f"= {sum(bits) + 7}, not 32")

    seen = set()
    for node in spec.get("doc") or []:
        frame = node.get("frame") if isinstance(node, dict) else None
        if not frame:
            continue
        name = frame.get("name")
        if not name or not str(name).strip():
            errs.append("frame with no name")
            continue
        if name in seen:
            errs.append(f"duplicate frame name: {name}")
        seen.add(name)

        budget = frame.get("budget")
        if not isinstance(budget, int) or budget < 1 or budget & (budget - 1):
            errs.append(f"{name}: budget {budget!r} is not a power of two")

        rpn = frame.get("rules_py_names")
        if rpn is not None and (not isinstance(rpn, list)
                                or not all(isinstance(x, str) for x in rpn)):
            errs.append(f"{name}: rules_py_names must be a list of strings")

        for h in frame.get("probe") or []:
            if not isinstance(h, dict) or not all(
                    isinstance(h.get(s), dict) and "op" in h[s]
                    for s in ("a", "b")):
                errs.append(f"{name}: probe hint must give a and b op dicts "
                            f"(got {h!r})")

        ma = frame.get("measures_also")
        if ma is not None:
            if not isinstance(ma, dict) or set(ma) - {"a", "b"} or not all(
                    isinstance(v, list) and all(isinstance(x, str) for x in v)
                    for v in ma.values()):
                errs.append(f"{name}: measures_also must map slot a/b to "
                            f"lists of mnemonics")

        ops_in_templates = _frame_operands(frame)
        for row in frame.get("rows") or []:
            cells = row["c"] if isinstance(row, dict) else row
            _check_row(frame, cells, len(cols), ops_in_templates, errs)
        for cluster in frame.get("ops") or []:
            for slot in ("a", "b"):
                for entry in cluster.get(slot) or []:
                    _check_contract(name, entry, errs)
    return errs


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, "encoding.yaml")
    with open(path) as fh:
        spec = yaml.safe_load(fh)
    errs = validate(spec)
    for e in errs:
        print(f"✗ {e}")
    if not errs:
        print("encoding.yaml is structurally valid.")
    return 1 if errs else 0


if __name__ == "__main__":
    raise SystemExit(main())

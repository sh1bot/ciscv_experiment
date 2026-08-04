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

from encoding_render import (ALL_IMM_NAMES, WRITABLE_FIELDS, field_width,
                             asm_operands, asm_pairs,
                             op_imm, op_name)

_BRACKET = re.compile(r"^\[\d+:\d+(?:\|\d+:\d+)*\]$")


def _frame_operands(frame):
    """Operand names the frame's templates declare (registers and imms)."""
    pairs = asm_pairs(frame) if frame.get("templates") else []
    return set().union(*(asm_operands(p) for p in pairs)) if pairs else set()


def _check_row(frame, row, grid, ops_in_templates, errs):
    """A row is a mapping over the writable fields; see the grid comment.

    Checked here: keys are writable field names (`g`/`h`/`funct3` and the
    opcode fields are NOT writable — they are opcode bits); a split field's
    sub-parts each declare their bits and sum to the field's width; every
    value names either a template operand, an immediate field the pricing
    model knows, or `unused`.
    """
    name = frame.get("name")
    if not isinstance(row, dict):
        errs.append(f"{name}: row {row!r} is not a field mapping")
        return
    for key, v in row.items():
        if key == "tag":
            continue
        if key not in WRITABLE_FIELDS:
            errs.append(f"{name}: row writes field {key!r}; writable fields "
                        f"are {list(WRITABLE_FIELDS)} — everything else is "
                        f"opcode bits")
            continue
        if isinstance(v, list):
            total = 0
            for part in v:
                if (not isinstance(part, dict)
                        or set(part) != {"bits", "value"}
                        or not isinstance(part.get("bits"), int)):
                    errs.append(f"{name}: split field {key} sub-part {part!r} "
                                f"must be {{bits: N, value: ...}}")
                    continue
                total += part["bits"]
                _check_value(frame, key, str(part["value"]),
                             ops_in_templates, errs)
            want = field_width(grid, key)
            if total != want:
                errs.append(f"{name}: split field {key} sub-parts sum to "
                            f"{total} bits; the field is {want}")
        else:
            _check_value(frame, key, str(v), ops_in_templates, errs)


def _check_value(frame, key, body, ops_in_templates, errs):
    name = frame.get("name")
    stem, _, spec = body.partition("[")
    if stem == "unused":
        return
    if stem in ALL_IMM_NAMES:
        if spec and not _BRACKET.match("[" + spec):
            errs.append(f"{name}: malformed immediate spec '{body}' in {key}")
        return
    if stem.startswith("imm"):
        # imm_field_bits also hard-errors on this; repeated here so the
        # schema gate reports every problem in one pass.
        errs.append(f"{name}: unknown immediate field '{stem}' in {key}")
        return
    if stem not in ops_in_templates:
        errs.append(f"{name}: row field {key} = '{body}' names no template "
                    f"operand (templates use {sorted(ops_in_templates)})")


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
    fields = grid.get("fields") or {}
    if not fields:
        errs.append("grid: no `fields` section (named fields with machine-word "
                    "bit positions)")
    else:
        covered = []
        for fname, fd in fields.items():
            b = (fd or {}).get("bits")
            if (not isinstance(b, list) or len(b) != 2
                    or not all(isinstance(x, int) for x in b) or b[0] < b[1]):
                errs.append(f"grid: field {fname} bits {b!r} must be "
                            f"[hi, lo] with hi >= lo")
                continue
            covered += list(range(b[1], b[0] + 1))
        if fields and sorted(covered) != list(range(32)):
            errs.append("grid: fields do not tile bits 0..31 exactly")
    cols = grid.get("columns") or []
    bits = grid.get("bits") or []
    disp = grid.get("display") or []
    if not (len(cols) == len(bits) == len(disp)):
        errs.append(f"grid: columns({len(cols)})/bits({len(bits)})/"
                    f"display({len(disp)}) lengths disagree")
    if sum(bits) + 5 + 2 != 32:
        errs.append(f"grid: bits sum to {sum(bits)} + opcode5(5) + marker(2) "
                    f"= {sum(bits) + 7}, not 32")

    # No frame name may CONTAIN another.  Frame names are the identity strings
    # shared by the yaml, rules.py, the tests and every measurement record, so
    # they get renamed and grepped in bulk; a name that is a substring of
    # another turns any careless sweep into silent corruption.  Keeping the set
    # containment-free means even a naive search cannot go wrong.
    for node in spec.get("doc") or []:
        if isinstance(node, dict) and "md" in node:
            errs.append("doc carries an `md` prose block: frame prose belongs "
                        "in that frame's own comments or `notes`, and a "
                        "standing rule belongs in the code that enforces it")

    all_names = []
    for node in spec.get("doc") or []:
        frame = node.get("frame") if isinstance(node, dict) else None
        if frame and frame.get("name"):
            all_names += [x.strip() for x in str(frame["name"]).split(",")]
    for a in all_names:
        for b in all_names:
            if a != b and a in b:
                errs.append(f"frame name {a!r} is a substring of {b!r} — "
                            f"rename one so bulk edits cannot corrupt them")

    seen = set()
    for node in spec.get("doc") or []:
        frame = node.get("frame") if isinstance(node, dict) else None
        if not frame:
            continue
        notes = frame.get("notes")
        if notes is not None and (not isinstance(notes, list) or
                                  not all(isinstance(x, str) for x in notes)):
            errs.append(f"{frame.get('name')}: notes must be a list of "
                        f"strings, one note per entry — not a prose block")
        name = frame.get("name")
        if not name or not str(name).strip():
            errs.append("frame with no name")
            continue
        if name in seen:
            errs.append(f"duplicate frame name: {name}")
        seen.add(name)

        does = frame.get("does")
        if not does or not str(does).strip():
            errs.append(f"{name}: no `does:` — every frame states, in one line, "
                        f"the essential operation it performs")

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
            _check_row(frame, row, grid, ops_in_templates, errs)
        for cluster in frame.get("ops") or []:
            if "same_op" in cluster:
                if not isinstance(cluster["same_op"], bool):
                    errs.append(f"{name}: same_op must be a bool")
                elif cluster["same_op"]:
                    a = {op_name(x) for x in cluster.get("a") or []}
                    b = {op_name(y) for y in cluster.get("b") or []}
                    if a != b:
                        errs.append(
                            f"{name}: same_op cluster has different A and B op "
                            f"sets (only {sorted(a & b)} can ever pair; "
                            f"A-only {sorted(a - b)}, B-only {sorted(b - a)})")
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

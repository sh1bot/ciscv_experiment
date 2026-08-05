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


# A row is a MAPPING over the operand-bearing fields of the grid: any field it
# leaves unset is free for opcode assignment, `unused` marks a column that
# carries no operand (the enumerator allocates its selector pattern from the
# reserved sentinel pool), and a field split between two operands states its
# sub-parts as {bits, value} entries, most-significant first.
WRITABLE_FIELDS = ("funct5", "rs2", "rs1", "rd")


def field_width(grid, field):
    """Bits of a named grid field, from its declared machine-word positions."""
    hi, lo = grid["fields"][field]["bits"]
    return hi - lo + 1


# The opcode-owned tail fields: never operand columns, always rendered last.
TAIL_FIELDS = ("opcode5", "marker")


def grid_columns(grid):
    """Operand-column order, derived from the fields' bit positions: highest
    bit first, tail fields excluded.  `fields` is the single source; storing
    the order separately let it drift."""
    named = [(fd["bits"][0], name) for name, fd in grid["fields"].items()
             if name not in TAIL_FIELDS]
    return [name for _hi, name in sorted(named, reverse=True)]


def display_widths(grid):
    """ASCII-art box widths for the operand columns plus the tail: every field
    displays at 2*bits - 1 (each bit a character, one space between)."""
    return ([2 * field_width(grid, f) - 1 for f in grid_columns(grid)]
            + [2 * field_width(grid, f) - 1 for f in TAIL_FIELDS])


def row_parts(row, grid):
    """Every operand piece of a mapping-form row, in column order.

    Yields (field, stem, bits, raw): `bits` is the field's full width for a
    plain value and the declared {bits} for a sub-part of a split field --
    which is what makes a split field priceable without billing the register
    beside it for the immediate's column.
    """
    for field in grid_columns(grid):
        v = row.get(field)
        if v is None:
            continue
        if isinstance(v, list):
            for part in v:
                raw = str(part["value"])
                yield field, raw.split("[")[0], int(part["bits"]), raw
        else:
            raw = str(v)
            yield field, raw.split("[")[0], field_width(grid, field), raw


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


# What an unset field displays as: opcode bits show their column label, and an
# unset operand field is explicitly free.
_UNSET_LABEL = {"h": "h", "g": "g", "funct3": "fn3"}


def _display_value(v):
    # A split field displays its stems only ("imma+rda"): the exact sub-ranges
    # are structured data in the yaml, and the full spelling overflows the box.
    if isinstance(v, list):
        return "+".join(str(p["value"]).split("[")[0] for p in v)
    return str(v)


def render_row(row, grid, colwidths, tag=None):
    rendered = []
    for pos, field in enumerate(grid_columns(grid)):
        v = row.get(field)
        if v is None:
            text = _UNSET_LABEL.get(field, "free")
        else:
            text = _display_value(v)
        rendered.append(_center(text, _spanned(colwidths, pos, 1)))
    pos = len(grid_columns(grid))
    for token in TAIL_CELLS:
        rendered.append(_center(token, _spanned(colwidths, pos, 1)))
        pos += 1
    line = "│" + "│".join(rendered) + "│"
    if tag:
        line += f" ({tag})"
    return line


BANNER = "<!-- Generated from encoding.yaml by util/encoding_render.py — do not edit by hand. -->"


def render(spec) -> str:
    # column display widths: the seven variable columns, then opcode5(5 bits ->
    # 9) and the marker(2 bits -> 3), all following width = 2*bits - 1.
    widths = display_widths(spec["grid"])
    header = header_lines(widths)
    out: list[str] = [BANNER, ""]
    for node in spec["doc"]:
        if "reserved" in node:
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
            if f.get("does"):
                out.append("*" + " ".join(str(f["does"]).split()) + "*")
                out.append("")
            for i, pair in enumerate(f["templates"]):
                if i:
                    out.append("")                 # blank line between template pairs
                for ln in pair:
                    out.append("    " + ln)
            out.append("")
            out.extend(header)
            for row in f["rows"]:
                out.append(render_row(row, spec["grid"], widths,
                                      row.get("tag")))
            if f.get("notes"):
                out.append("")
                import textwrap as _tw
                for note in f["notes"]:
                    w = _tw.wrap(" ".join(str(note).split()), width=73,
                                 initial_indent=" * ", subsequent_indent="   ")
                    out.extend(w)
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


def row_operands(row, grid):
    """Operand stems a mapping-form row encodes (`unused` is not an operand)."""
    return {stem for _f, stem, _b, _raw in row_parts(row, grid)
            if stem != "unused"}


def template_op_fields(frame):
    """{slot: {op-name: {field, ...}}} learned from the frame's templates.

    A template line names its op and the operand fields that op uses, with `/`
    marking aligned alternatives -- "mv/li rdb, rs2b/immb" says mv takes rs2b
    and li takes immb. Lines whose op is a generic placeholder (`alu`, `load`,
    `store`, `shXadd`) name no concrete op, so they contribute nothing and the
    caller simply skips those ops.
    """
    out = {"a": {}, "b": {}}
    for pair in frame.get("templates", []):
        if len(pair) != 2:
            continue
        for slot, line in (("a", pair[0]), ("b", pair[1])):
            mnem, _, rest = line.strip().partition(" ")
            alts = mnem.split("/")
            for i, op in enumerate(alts):
                # resolve each operand, taking the i-th alternative where the
                # operand offers alternatives aligned with the op alternatives
                fields = set()
                for operand in rest.split(","):
                    operand = operand.strip()
                    choices = operand.split("/")
                    pick = choices[i] if len(choices) == len(alts) else choices[0]
                    fields |= set(_OPERAND.findall(pick))
                out[slot].setdefault(op, set()).update(fields - _IMPLICIT)
    return out


def unencodable_clusters(frame, grid):
    """(opA, opB) combinations `ops` allows for which NO row supplies the fields
    both sides need. Only pairs whose ops are both named in the templates are
    judged; a placeholder-templated frame (`alu`, `load`, ...) is skipped.

    This is the check that a per-slot field-name comparison cannot make: each
    side may be individually encodable while no single row carries both.
    """
    tof = template_op_fields(frame)
    rows = [set(row_operands(r, grid)) for r in frame.get("rows", [])]
    bad = []
    for cluster in frame.get("ops") or []:
        for ea in cluster.get("a", []):
            na = op_name(ea)
            fa = tof["a"].get(na)
            if fa is None:
                continue
            for eb in cluster.get("b", []):
                nb = op_name(eb)
                fb = tof["b"].get(nb)
                if fb is None:
                    continue
                if not any(fa <= r and fb <= r for r in rows):
                    bad.append(f"({na}, {nb}) needs "
                               f"{sorted(fa)}+{sorted(fb)}, no row has both")
    return bad


def lint(spec):
    grid = spec["grid"]
    problems = 0
    for node in spec["doc"]:
        if "frame" not in node:
            continue
        f = node["frame"]
        pairs = asm_pairs(f)
        asm_ops = set().union(*(asm_operands(p) for p in pairs)) if pairs else set()
        row_ops = (set().union(*(row_operands(r, grid) for r in f["rows"]))
                   if f.get("rows") else set())

        bad_pair = [p for p in pairs if len(p) != 2]
        unencodable = unencodable_clusters(f, grid)
        missing = asm_ops - row_ops           # operand in asm, never encoded
        spurious = row_ops - asm_ops          # field in a row, not in any asm
        # A declared op width above the drawn field rides the opcode list at
        # 2^ext codepoints — the one widening mechanism there is. Legitimate
        # and already charged, so reported for visibility only.
        on_opcode_list = []
        for slot in ("a", "b"):
            base = imm_field_bits(f, grid, slot)
            for c in f.get("ops") or []:
                for e in c.get(slot, []):
                    b = op_bits(e)
                    if b and b > base:
                        on_opcode_list.append(
                            f"{op_name(e)}({slot}) {b}b = {base}b field + "
                            f"{b - base}b via {1 << (b - base)} codepoints")
        if bad_pair or missing or spurious or unencodable:
            problems += 1
            print(f"✗ {f['name']}")
            if bad_pair:
                print(f"    asm chunks that aren't 2-line pairs: "
                      f"{[len(p) for p in bad_pair]}")
            if missing:
                print(f"    operands in asm but NOT encoded in any row: {sorted(missing)}")
            if spurious:
                print(f"    fields in rows but NOT used by any asm line: {sorted(spurious)}")
            if unencodable:
                print(f"    ops combination with no row to encode it:")
                for u in unencodable:
                    print(f"        {u}")
        else:
            print(f"✓ {f['name']}  ({len(pairs)} pair(s), {len(rows)} row(s))")
        for msg in on_opcode_list:
            print(f"    · wide immediate on the opcode list: {msg}")
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


def op_imm(entry):
    """The full declared immediate contract of an op entry, or {} for a bare op:
    {bits, signed, scale}. `scale` is the multiplier the field carries (4 for
    addi4spn, whose low two bits are structurally zero), and exists for ops whose
    mnemonic never appears in a template line — a template coefficient is the
    normal way to declare scale, but an op that is only named in `ops` has no
    template to carry one. Where BOTH are present they must agree; see
    analysis/encoding_verify.py, which cross-checks them."""
    if isinstance(entry, dict):
        return dict(entry.get("imm") or {})
    return {}


def op_contracts(frame):
    """{mnemonic: {bits, signed, scale}} for every op in a frame that declares
    an immediate contract. Ops appear per slot but a mnemonic's contract is a
    property of the opcode, so a disagreement across slots is an error."""
    out = {}
    for cluster in frame.get("ops") or []:
        for slot in ("a", "b"):
            for entry in cluster.get(slot, []):
                c = op_imm(entry)
                if not c:
                    continue
                name = op_name(entry)
                if name in out and out[name] != c:
                    # A k-scaled op legitimately declares a different `scale`
                    # per width cluster (pre-inc addi: 8 with ld/sd, 4 with
                    # lw/sw). Width and signedness are still opcode properties.
                    strip = lambda d: {k: v for k, v in d.items() if k != "scale"}
                    if strip(out[name]) != strip(c):
                        raise ValueError(
                            f"{frame.get('name')}: conflicting immediate "
                            f"contracts for {name}: {out[name]} vs {c}")
                    c = dict(c, scale=max(out[name].get("scale") or 0,
                                          c.get("scale") or 0))
                out[name] = c
    return out


# Access width in bytes, for `same_width` clusters.  XLEN-switchable ops have
# no fixed width and never take part in a width diagonal.
MEM_WIDTH = {"lb": 1, "lbu": 1, "sb": 1, "lh": 2, "lhu": 2, "sh": 2,
             "lw": 4, "lwu": 4, "sw": 4, "ld": 8, "sd": 8}


def cluster_pairs(c):
    """The (a_entry, b_entry) combinations one cluster allows.

    Normally the cross product: any A op with any B op.  Two flags restrict it
    to a DIAGONAL, and both exist so the constraint lives in the data rather
    than in how the clusters happen to be split — enumerating a diagonal as N
    singleton clusters prices identically but reads as a free choice, and does
    not survive anyone tidying the list:

      `same_op`    — A and B must be the SAME opcode (mem-base-pair: a load
                     pairs only with the same width of load).
      `same_width` — A and B must have the same ACCESS WIDTH, though not the
                     same opcode (load-store-chain: lw pairs with sw)."""
    a, b = c.get("a", []), c.get("b", [])
    if c.get("same_op"):
        by = {op_name(y): y for y in b}
        return [(x, by[n]) for x in a if (n := op_name(x)) in by]
    if c.get("same_width"):
        return [(x, y) for x in a for y in b
                if MEM_WIDTH.get(op_name(x)) is not None
                and MEM_WIDTH.get(op_name(x)) == MEM_WIDTH.get(op_name(y))]
    return [(x, y) for x in a for y in b]


def cluster_combos(c):
    """How many (opA, opB) combinations one cluster allows."""
    return len(cluster_pairs(c))


def opcode_demand(ops):
    """BASE (opA, opB) combos a frame's ops allow: Σ over clusters.
    Ignores immediate width — see opcode_codepoints for the ext-aware count."""
    if not ops:
        return None
    return sum(cluster_combos(c) for c in ops)


# Every immediate field name the layout may use. A row naming anything else is
# an error, not a field the pricing model quietly ignores: an unrecognised name
# is invisible to imm_field_bits, so its width is never charged. That is how a
# 7-bit `immc` field once cost nothing.
IMM_NAMES = {"a": {"imma", "imm"}, "b": {"immb", "imm"}}
ALL_IMM_NAMES = IMM_NAMES["a"] | IMM_NAMES["b"]


def rd_column_cells(frame, grid):
    """The cell stem each row places in the `rd` column, one per row.

    The rd column is where the x0/x2 sentinel lives (encoding.yaml `reserved`),
    so what a frame puts there decides its role: a destination register in
    every row means the frame OWES the reservation and can HOST a guest in the
    slice its rd cannot reach; `unused` means the frame IS a guest, selected
    by a sentinel pattern the enumerator allocates from the reserved pool; an
    immediate means neither -- the column is not a register field at all."""
    out = []
    for row in frame.get("rows") or []:
        v = row.get("rd")
        if v is None:
            out.append(None)
        elif isinstance(v, list):
            out.append("+".join(str(p["value"]).split("[")[0] for p in v))
        else:
            out.append(str(v).split("[")[0])
    return out


def rd_column_role(frame, grid):
    """'host' | 'guest' | None — see rd_column_cells."""
    stems = [s for s in rd_column_cells(frame, grid) if s is not None]
    if not stems:
        return None
    if all(s.startswith(("rd", "rsd")) and s[-1] in "ab" for s in stems):
        return "host"
    if all(s == "unused" for s in stems):
        return "guest"
    return None


def imm_field_bits(frame, grid, slot):
    """The immediate FIELD width a slot's rows draw: the register columns the
    field occupies, and nothing else. Five bits from one column, ten from two.
    slot 'a'→imma, 'b'→immb; a shared `imm` counts for either; the widest row
    governs, since the encoder may pick whichever row holds an op.

    THE IMMEDIATE RULE, stated here because this is what enforces it: a field
    is five bits per register column it consumes, and grows incrementally past
    that by taking multiple opcode-list entries — an op declaring `imm: {bits: N}` occupies
    2^(N - field) entries, which `_slot_weight` charges. There is no other
    widening mechanism. `g` and `h` are opcode bits and not writable by a row,
    and a field name the model does not know is an error, not a wider field."""
    names = IMM_NAMES[slot]
    width = 0
    for row in frame.get("rows", []):
        row_width = 0     # a field may be SPLIT across columns (imma[9:5] in
        for _f, stem, bits, raw in row_parts(row, grid):   # one, [4:0] in another)
            if stem.startswith("imm") and stem not in ALL_IMM_NAMES:
                raise ValueError(
                    f"{frame.get('name')}: row names immediate field "
                    f"'{stem}', which the pricing model does not recognise "
                    f"(expected one of {sorted(ALL_IMM_NAMES)}). An "
                    f"unrecognised name is never charged for its width.")
            if stem in names:
                row_width += bits
        width = max(width, row_width)
    return width


def narrow_field_bits(frame, grid, slot):
    """The immediate field of the NARROWEST row that draws this slot's
    immediate at all: the un-extended base band an op with no declared
    contract actually gets.

    `imm_field_bits` takes the widest row — right for pricing what the frame
    CAN carry, wrong for the base band once a frame mixes full-register rows
    with split rows: dual-setup-pair's any-rd band is the 5-bit row beside
    its 7-bit a0-a7 split rows, and reading the 7 there silently widens a
    band whose extra bits only exist under the register restriction."""
    names = IMM_NAMES[slot]
    widths = []
    for row in frame.get("rows", []):
        row_width = 0
        for _f, stem, bits, _raw in row_parts(row, grid):
            if stem in names:
                row_width += bits
        if row_width:
            widths.append(row_width)
    return min(widths) if widths else 0


def _slot_weight(op_list, base):
    """Codepoints a slot's ops occupy: Σ 2^ext, where ext = the bits of range an
    op's declared width needs above the base (0 for a bare/base-range op, so
    register-form ops never inflate the count)."""
    w = 0
    for e in op_list:
        b = op_bits(e)
        w += 1 << (max(0, b - base) if b else 0)
    return w


def lint_frame(frame, grid):
    """Complaints about a frame whose codepoint demand cannot be trusted.

    With the single-rule model — a field is its register columns, wider range
    is bought by opcode duplication — the only violations left are structural,
    and `imm_field_bits` raises on both: an immediate parked in `g`/`h`, and a
    field name the model does not recognise. This collects them per slot.

    Returns a list of strings; empty means nothing detectable is wrong."""
    out = []
    for slot in ("a", "b"):
        try:
            imm_field_bits(frame, grid, slot)
        except ValueError as e:
            out.append(str(e))
    return out


def shared_imm(frame, grid):
    """True if the frame's rows name one `imm` field serving BOTH slots, rather
    than separate `imma`/`immb`."""
    return any(stem == "imm"
               for row in frame.get("rows", [])
               for _f, stem, _b, _raw in row_parts(row, grid))


def _ext(entry, base):
    b = op_bits(entry)
    return max(0, b - base) if b else 0


def opcode_codepoints(frame, grid):
    """Real codepoint demand: Σ over clusters of weight(a)×weight(b), where each
    slot weight sums 2^ext per op. Factors per slot because the A and B
    immediates extend their range independently.

    UNLESS the frame draws ONE shared `imm` serving both slots (mem-base-pair). Then
    there is only one field, so its extension is bought once: the cluster costs
    `|a| * |b| * 2^maxext`, not `2^ext(a) * 2^ext(b)`. Billing a shared field
    per slot squares a cost that was never paid twice — on mem-base-pair that is the
    difference between 16 codepoints and 48."""
    ops = frame.get("ops")
    if not ops:
        return None
    base_a = imm_field_bits(frame, grid, "a")
    base_b = imm_field_bits(frame, grid, "b")
    if shared_imm(frame, grid):
        base = max(base_a, base_b)
        total = 0
        for c in ops:
            a, b = c.get("a", []), c.get("b", [])
            ext = max([_ext(e, base) for e in list(a) + list(b)] or [0])
            total += max(cluster_combos(c), 1) * (1 << ext)
        return total
    total = 0
    for c in ops:
        if c.get("same_op"):
            # One opcode, not two: the same op in both slots pays its ext once.
            total += sum(1 << max(_ext(x, base_a), _ext(y, base_b))
                         for x, y in cluster_pairs(c))
        else:
            # Two independent opcodes and two independent fields, so each
            # allowed combination pays both extensions.  For a full cross
            # product this is exactly _slot_weight(a) * _slot_weight(b).
            total += sum((1 << _ext(x, base_a)) * (1 << _ext(y, base_b))
                         for x, y in cluster_pairs(c))
    return total


def budget_status(cp, budget):
    """Assert a frame's codepoints fit its declared budget: (ok, message).
    Valid when half-full..full — cp in (budget/2, budget]. Over = too many ops
    for the block; under-half = table not yet filled (or budget too big)."""
    if budget is None:
        return True, ""
    if cp > budget:
        return False, f"OVER budget {budget} by {cp - budget}"
    if cp * 2 < budget:
        return False, f"under half of {budget} (table unfinished?)"
    return True, f"ok ≤{budget}"


def opcodes(spec):
    grid = spec["grid"]
    print(f"{'frame':40} {'shape':>13} {'base':>6} {'cpts':>5} {'budget':>7}  status")
    print("-" * 88)
    base_total, cp_total, missing, wide, viol = 0, 0, [], [], []
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
        if cp > d:
            wide.append(f["name"])
        budget = f.get("budget")
        ok, msg = budget_status(cp, budget)
        if not ok:
            viol.append(f["name"])
        wtag = "wide-imm " if cp > d else ""
        print(f"{f['name']:40} {shape:>13} {d:6} {cp:5} "
              f"{('-' if budget is None else budget):>7}  {wtag}{msg}")
    print("-" * 88)
    print(f"{'TOTAL':40} {'':>13} {base_total:6} {cp_total:5}")
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
    print("A frame's optional `budget` asserts codepoints in (budget/2, budget]:\n"
          "over = too many ops for the block, under-half = table not yet filled.")
    if missing:
        print(f"\nFrames with no ops declared: {missing}")
    if viol:
        print(f"\nBUDGET VIOLATIONS ({len(viol)}): {', '.join(viol)}")
        return 1
    return 0


# --- equivalences: lint + deduce which frames each one feeds ---------------
_KNOWN_GUARDS = {"single_bit", "low_mask", "high_mask"}


def _head_ops(line):
    """The mnemonic(s) heading an asm line, splitting an X/Y alternate."""
    head = line.strip().split()[0] if line.strip() else ""
    return set(head.split("/"))


def _frame_op_sets(frame):
    a, b = set(), set()
    for c in frame.get("ops") or []:
        a |= {op_name(e) for e in c.get("a", [])}
        b |= {op_name(e) for e in c.get("b", [])}
    return a, b


def equivalences(spec):
    equivs = spec.get("equivalences") or []
    frames = [n["frame"] for n in spec["doc"] if "frame" in n]
    problems = 0
    print(f"{len(equivs)} equivalence(s). Each `spelled` surface form is accepted "
          f"as matching the frame(s)\nwhose templates realise the `canonical` form:\n")
    for eq in equivs:
        name = eq.get("name", "?")
        canon = eq.get("canonical") or []
        spelled = eq.get("spelled") or []
        when = eq.get("when") or {}
        bind = eq.get("bind") or {}
        issues = []
        if not canon or not spelled:
            issues.append("missing canonical/spelled")
        elif len(canon) != len(spelled):
            issues.append(f"{len(canon)} canonical vs {len(spelled)} spelled lines")
        for var, cls in when.items():
            if cls not in _KNOWN_GUARDS:
                issues.append(f"unknown guard class '{cls}' (known: {sorted(_KNOWN_GUARDS)})")
        # a named-class guard binds `n`; every bind expression must reference it
        for tgt, expr in bind.items():
            if "n" not in re.findall(r"[A-Za-z_]\w*", str(expr)):
                issues.append(f"bind {tgt}={expr!r} does not reference n")
        # which frames realise the canonical form? match head opcodes per slot
        ca = _head_ops(canon[0]) if canon else set()
        cb = _head_ops(canon[-1]) if len(canon) > 1 else set()
        hits = []
        for f in frames:
            fa, fb = _frame_op_sets(f)
            if len(canon) > 1:
                if (ca & fa) and (cb & fb):
                    hits.append(f["name"])
            elif ca & (fa | fb):                 # single-line op alias
                hits.append(f["name"])
        tgt = ", ".join(hits) if hits else "(no frame yet)"
        mark = "✗" if issues else "✓"
        print(f" {mark} {name}")
        print(f"     {' ; '.join(spelled)}  ⟶  {' ; '.join(canon)}")
        if when:
            print(f"     guard {when}   bind {bind or '(none)'}")
        print(f"     realised by: {tgt}")
        if issues:
            problems += 1
            print(f"     ISSUES: {issues}")
        print()
    print(f"{problems} equivalence(s) with problems.")
    return problems


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
    ap.add_argument("--equiv", action="store_true",
                    help="lint equivalences and show which frames each feeds")
    args = ap.parse_args()

    if args.equiv:
        with open(args.yaml) as fh:
            sys.exit(1 if equivalences(yaml.safe_load(fh)) else 0)

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
            return
        sys.stdout.writelines(diff)
        adds = sum(1 for d in diff if d.startswith("+") and not d.startswith("+++"))
        dels = sum(1 for d in diff if d.startswith("-") and not d.startswith("---"))
        print(f"\n# {dels} lines removed / {adds} lines added vs encoding.md"
              f"\n# regenerate: python3 util/encoding_render.py -o encoding.md")
        sys.exit(1)

    if args.output:
        with open(args.output, "w") as fh:
            fh.write(text)
    else:
        sys.stdout.write(text)


if __name__ == "__main__":
    main()

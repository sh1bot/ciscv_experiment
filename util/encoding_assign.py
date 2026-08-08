#!/usr/bin/env python3
"""
util/encoding_assign.py — assign concrete opcode bit-patterns to every frame in
encoding.yaml as a VARIABLE-LENGTH PREFIX CODE (canonical Huffman, à la
zlib/DEFLATE), resolve every op-select bit against the frame's opcode tables,
and emit the result as the `ciscv-proto.yml` data file (default) or as a
human-readable report (--text).

The opcode selector is the 10-bit word  opcode5(5) : funct3(3) : g(1) : h(1),
read MSB->LSB (opcode5[4] first, h last). Each frame spends

    total_depth = identifier_bits + op_select_bits           (<= 10)

of that word; op_select = ceil(log2(#op-combos)); the identifier is a
prefix-free constant that names the frame. Any bits BELOW total_depth (toward
h) are free — a frame whose word stops at or before bit funct3[0] leaves g and
h free to carry a wide immediate.

Each frame reserves a FIXED block sized to its `budget` (not its current fill),
buddy-allocated largest-first so blocks never overlap and a frame can grow into
its own unused slots — up to its budget — without moving any other frame. The
identifier length is therefore variable: big blocks get short identifiers, small
blocks long ones. The tool asserts the reservations fit (Σ budget ≤ 1024) so the
planned table cannot overflow even before it is fully populated; it exits
non-zero on overflow, or if a frame's current fill already exceeds its block.

Two "nice-to-have" biases are applied when they don't cost feasibility:
  * frames are ORDERED for canonical assignment by their A-slot RISC-V format
    (load / OP-IMM / store / OP / branch / jump), so the leading identifier bits
    — which physically sit in opcode[6:2] — climb in the same order the real
    base ISA opcodes do (bit 5 clear ~ immediate/I-type, set ~ register/R-type;
    bit 6 ~ arithmetic vs control). A hardware A-slot decoder can therefore
    branch on the same bits it already uses.
  * enumeration preferences — which selector bits serve which purposes, block
    ordering, rounding — are intent only, documented in encoding.yaml's
    "Enumeration policy" note.  Nothing here reads them as capacity.

INSIDE a frame's block the same buddy discipline resolves the op-select bits
themselves: each `ops` cluster takes an aligned sub-block, and inside it the
index splits into a fixed A-index field, a B-index field and (for a frame whose
rows draw ONE shared immediate) the immediate's extension bits. A cluster that
states a diagonal (`same_op`/`same_width`) is indexed by the PAIR instead, so
the combinations it forbids have no encoding at all. Every table is rounded up
to a power of two, so every 'p' in a layout is a plain bit-field bit — no divide
anywhere in the decode path — and the rounding holes are the frame's room to
grow. `--check-tables` asserts the padded tables still fit each block, that the
enumeration costs exactly what the pricing model charges, and that every
codepoint decodes back to one frame and one op pair.

In a layout the op-select bits are drawn as 'p', and a box is one FIELD rather
than one grid column: columns holding ADJOINING bits of the same thing fuse
(`imma[9:5] | imma[4:0]` becomes one `imma[9:0]`, h | g becomes `p p` the way
funct3 already draws `p p p`) and a column split between two operands divides
(`imb│rdb`, footnoted under the layout). Both keep the drawing's width and
every boundary on a bit boundary — see `cells`.

For each frame the tool prints its bare form, then its `templates:` — per asm
pair, the A slot and the B slot as structured YAML: the fields each one draws
(name, bits, type — rs/rd/rsd/imm) plus any register the line hard-codes
outright rather than encoding (`implicit`), so it is visible which fields each
slot owns, which they share, and which registers are fixed by the opcode.

Usage:
    python3 util/encoding_assign.py                 # the ciscv-proto.yml data file
    python3 util/encoding_assign.py -o FILE         # ... written to FILE
    python3 util/encoding_assign.py --text          # the human-readable report
    python3 util/encoding_assign.py --decode 0x2a1 [--rd x2]     # resolve one word
"""
import argparse
import json
import math
import os
import re
import sys

import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from encoding_render import (_center, _spanned, header_lines,
                             grid_columns, display_widths, field_width,
                             opcode_demand, opcode_codepoints, op_name,
                             op_bits, op_imm, _ext, imm_field_bits, shared_imm,
                             cluster_pairs, row_operands, row_parts,
                             lint_frame, rd_column_role,
                             _OPERAND, _IMPLICIT)

# Sentinel bit patterns reserved in the rd column (encoding.yaml `reserved`):
# x0 = "0 0 0 0 0" and x2 = "0 0 0 1 0".  A guest row says `rd: unused` and
# the ENUMERATOR allocates which pattern selects it, from this pool -- a lent
# codepoint carries one guest per DISTINCT sentinel value in use, not two
# automatically, so the lending is tracked per (host, sentinel).
SENTINEL_PATTERNS = {"x0": "0 0 0 0 0", "x2": "0 0 0 1 0"}
SENTINEL_ORDER = ("x2", "x0")           # allocation preference, deterministic
SENTINEL_REGS = {0: "x0", 2: "x2"}      # rd's value -> the pattern it names

WBITS = 10                      # opcode5(5)+funct3(3)+g(1)+h(1)
MARKER = "1 0"
# What one op-select bit draws as in a layout.  Deliberately NOT 'o': it stands
# shoulder to shoulder with the constant '0'/'1' identifier bits, and 'o' and
# '0' are the same shape in a monospace grid.
OPSEL_CHAR = "p"
# A frame can only carry an immediate bit in g or h if its selector word STOPS
# above that bit -- identifier + op-select must leave it free.  These are the
# depths that requires.  Every frame currently sits at depth 10/10, so nothing
# can claim either bit, which is why the canonical form buys extra range by
# repeating the opcode instead.  Checked in main(); they were dead constants
# for a long time and the claim went unaudited.
# Grid column indices (encoding.yaml grid.columns): the g bit is column 2, the
# h bit column 0.  They are opcode bits; the renderer substitutes the selector
# word's actual bit character where a row names them.
COL_H, COL_G = 0, 2
_GRID = None                     # set by load(); row helpers need field widths

# --- RISC-V A-slot format classification (nice-to-have #1) -----------------
# Real base-ISA opcode[6:2] values, so the ordering below climbs the way the
# hardware's own opcode field does.
_LOADS  = {"lb", "lbu", "lh", "lhu", "lw", "lwu", "ld"}
_STORES = {"sb", "sh", "sw", "sd"}
_BRANCH = {"beq", "bne", "blt", "bge", "bltu", "bgeu", "beqz", "bnez"}
_JUMP   = {"j", "jal", "jalr", "ret", "jr"}
_IMM_OP = {"addi", "addiw", "andi", "ori", "xori", "slli", "srli", "srai",
           "li", "mv", "addi4spn"}   # OP-IMM (I-type)
_REG_OP = {"add", "addw", "sub", "subw", "and", "andn", "or", "xor", "mul",
           "mulh", "mulhu", "mulhsu", "min", "max", "minu", "maxu", "div",
           "divu", "divw", "divuw", "rem", "remu", "remw", "remuw", "slt",
           "sh1add", "sh2add", "sh3add"}  # OP / OP-32 (R-type)

# (rank, label, canonical opcode[6:2]) — rank orders canonical assignment.
_FORMATS = [
    ("load",   0b00000),   # LOAD
    ("i-type", 0b00100),   # OP-IMM
    ("store",  0b01000),   # STORE
    ("r-type", 0b01100),   # OP
    ("branch", 0b11000),   # BRANCH
    ("jump",   0b11011),   # JAL/JALR
    ("mixed",  None),
]
_FMT_RANK = {name: i for i, (name, _) in enumerate(_FORMATS)}
_FMT_OPC = {name: opc for name, opc in _FORMATS}


def a_ops(frame):
    """Every A-slot opcode across the frame's biclique clusters."""
    out = set()
    for c in frame.get("ops") or []:
        out |= {op_name(e) for e in c.get("a", [])}
    return out


def a_format(frame):
    """Coarse RISC-V format of the frame's A-slot op(s)."""
    ops = a_ops(frame)
    if not ops:
        return "mixed"
    if ops <= _LOADS:
        return "load"
    if ops <= _STORES:
        return "store"
    if ops <= _BRANCH:
        return "branch"
    if ops <= _JUMP:
        return "jump"
    if ops <= _IMM_OP:
        return "i-type"
    if ops <= _REG_OP:
        return "r-type"
    # a mix of immediate- and register-form arithmetic (addi + add + ...)
    if ops <= (_IMM_OP | _REG_OP):
        return "i-type"        # leans I-type: bit 5 clear covers the addi head
    return "mixed"


def opsel_bits(demand):
    return max(0, math.ceil(math.log2(demand))) if demand and demand > 1 else 0


def _p2(n):
    """The power of two at or above n (0 and 1 -> 1)."""
    return 1 << max(0, (n - 1).bit_length())


def _log2(n):
    """log2 of an exact power of two."""
    return max(0, (n - 1).bit_length())


# --- budget-driven fixed-block allocation ----------------------------------
def allocate_blocks(frames):
    """Reserve each frame a FIXED block of 2^opsel codepoints, where opsel comes
    from the frame's budget (not its current fill). Blocks are buddy-allocated
    largest-first, so they never overlap and a frame can grow into its own unused
    slots — up to its budget — without moving any other frame. All blocks sit at
    the uniform depth W = ceil(log2(total reserved)); the low WBITS-W bits are
    then free for every frame to carry an extended immediate whenever the
    namespace is not full. Returns (order, reserved, W)."""
    total = sum(1 << f["opsel"] for f in frames)
    W = max(1, (total - 1).bit_length()) if total > 1 else 1   # ceil(log2 total)
    order = sorted(frames, key=lambda f: (-f["opsel"], f["a_rank"], f["name"]))
    cursor = 0
    for f in order:
        blk = 1 << f["opsel"]
        base = ((cursor + blk - 1) // blk) * blk        # align to block size
        f["base_cp"] = base
        f["id_len"] = max(0, W - f["opsel"])
        f["id_val"] = base >> f["opsel"]
        f["depth"] = min(W, WBITS)
        cursor = base + blk
    order.sort(key=lambda f: f["base_cp"])
    return order, total, W


# --- op tables: what the op-select bits actually select ---------------------
# A frame's op-select index is a bit-field, laid out the same way the frames
# themselves are laid out in the namespace: aligned power-of-two blocks,
# largest first.  Top bits pick the `ops` cluster; inside a cluster the index
# splits into an A-table index, a B-table index and — only where the frame's
# rows draw ONE shared immediate — the extension bits of that immediate.
#
# A cluster that states a DIAGONAL (`same_op` / `same_width`) is indexed by the
# PAIR instead: its allowed combinations are a list, not a product, and giving
# each slot its own field would encode combinations the frame forbids.
#
# Inside a slot's table the entries are ordered WIDEST FIRST, because an op
# that declares more immediate range than its frame's field draws occupies
# 2^ext consecutive entries (encoding.yaml's one widening mechanism).  Sorting
# by descending span keeps every op's run aligned to its own size, so the low
# ext bits of its index ARE the high bits of its immediate, with no adder in
# the decode path.
def slot_entries(op_list, field_bits):
    """(entries, span) for one slot of one cluster. `field_bits` is the
    immediate width the frame's rows draw for this slot, or None when the
    frame's immediate is shared and its extension is bought once per cluster
    rather than per op."""
    ents = []
    for e in op_list:
        n = 1 if field_bits is None else 1 << _ext(e, field_bits)
        ents.append({"op": op_name(e), "n": n, "imm": op_imm(e),
                     "field": field_bits})
    ents.sort(key=lambda e: -e["n"])          # stable: yaml order within a width
    at = 0
    for e in ents:
        e["at"] = at
        at += e["n"]
    return ents, _p2(at)


def _slot_entry(entry, field_bits, n):
    return {"op": op_name(entry), "n": n, "imm": op_imm(entry),
            "field": field_bits}


def pair_entries(cluster, ba, bb, shared):
    """(entries, span) for a DIAGONAL cluster, one entry per allowed (A, B)
    combination.  An entry spans 2^ext indices exactly as a slot entry does —
    the extensions of the two immediates, or, where one opcode serves both
    slots (`same_op`), the single extension it pays for."""
    ents = []
    for x, y in cluster_pairs(cluster):
        ea, eb = _ext(x, ba), _ext(y, bb)
        if shared:                      # bought once, at the cluster level
            ea = eb = 0
        elif cluster.get("same_op"):    # one opcode, so one extension
            ea, eb = max(ea, eb), 0
        ents.append({"a": _slot_entry(x, None if shared else ba, 1 << ea),
                     "b": _slot_entry(y, None if shared else bb, 1 << eb),
                     "ext_a": ea, "ext_b": eb, "n": 1 << (ea + eb)})
    ents.sort(key=lambda e: -e["n"])          # stable: yaml order within a width
    at = 0
    for e in ents:
        e["at"] = at
        at += e["n"]
    return ents, _p2(at)


def frame_tables(frame, grid):
    """(clusters, used) — the frame's op-select space.

    Each cluster gets an aligned sub-block of `size` indices; `used` is the
    high-water mark, which must fit the frame's block. Emitted in yaml order,
    allocated largest-first."""
    ba = imm_field_bits(frame, grid, "a")
    bb = imm_field_bits(frame, grid, "b")
    shared = shared_imm(frame, grid)
    out = []
    for c in frame.get("ops") or []:
        a, b = list(c.get("a", [])), list(c.get("b", []))
        ext, imm = 0, {}
        if shared:
            # One field serving both slots: its extension is bought once for
            # the cluster, not once per slot (see opcode_codepoints).
            base = max(ba, bb)
            widest = max(a + b, key=lambda e: op_bits(e) or 0, default=None)
            ext = _ext(widest, base) if widest is not None else 0
            if ext:
                imm = dict(op_imm(widest), field=base)
        diagonal = next((k for k in ("same_op", "same_width") if c.get(k)), None)
        cl = {"ext": ext, "imm": imm, "diagonal": diagonal}
        if diagonal:
            pairs, span = pair_entries(c, ba, bb, shared)
            cl.update(pairs=pairs, span_p=span, size=span * (1 << ext))
        else:
            ea, sa = slot_entries(a, None if shared else ba)
            eb, sb = slot_entries(b, None if shared else bb)
            cl.update(a=ea, b=eb, span_a=sa, span_b=sb,
                      size=sa * sb * (1 << ext))
        out.append(cl)
    cursor = 0
    for c in sorted(out, key=lambda c: -c["size"]):
        c["at"] = ((cursor + c["size"] - 1) // c["size"]) * c["size"]
        cursor = c["at"] + c["size"]
    return out, cursor


def pair_fields(cluster):
    """(pair-index bits, A-extension bits, B-extension bits) for a diagonal
    cluster whose entries all span the same shape — the case where the index
    still divides into named fields. (0, 0, 0) when they do not, and the entry
    table's own `at`/`n` are the only way to read it."""
    pairs = cluster["pairs"]
    exts = {(e["ext_a"], e["ext_b"]) for e in pairs}
    if len(exts) != 1:
        return 0, 0, 0
    ea, eb = exts.pop()
    return _log2(cluster["span_p"]) - ea - eb, ea, eb


def sel_bits(frame):
    """Width of the frame's op-select index — the opcode bits its block spans.
    A hosted frame's rd sentinel is not one of them: its rows NAME a single
    reserved pattern, so it selects the frame and then holds still."""
    return frame["opsel"]


def cluster_word(frame, cluster):
    """The cluster's op-select index spelled bit by bit, MSB first: constant
    bits select the cluster, then 'a'/'b' index the two op tables (or 'p' the
    pair table of a diagonal cluster, followed by the 'a'/'b' bits of the two
    immediates it extends), and 'i' carries a shared immediate's high bits."""
    span = _log2(cluster["size"])
    pre = sel_bits(frame) - span
    bits = "".join(str((cluster["at"] >> (span + pre - 1 - i)) & 1)
                   for i in range(pre))
    if cluster["diagonal"]:
        p, ea, eb = pair_fields(cluster)
        body = "p" * (p or _log2(cluster["span_p"])) + "a" * ea + "b" * eb
    else:
        body = "a" * _log2(cluster["span_a"]) + "b" * _log2(cluster["span_b"])
    return bits + body + "i" * cluster["ext"]


def _entry_at(entries, i):
    """(entry, offset) for table index i, or (None, 0) if i lands in the
    padding above the last entry."""
    for e in entries:
        if e["at"] <= i < e["at"] + e["n"]:
            return e, i - e["at"]
    return None, 0


def _resolved(entry, offset):
    out = {"op": entry["op"]}
    if entry["imm"]:
        out["imm"] = dict(entry["imm"])
        if entry["field"] is not None:
            out["imm"]["field"] = entry["field"]
    if entry["n"] > 1:
        out["imm_high"] = offset            # the immediate's bits above the field
    return out


def resolve_index(frame, index):
    """THE op-select function: turn a frame's op-select index into the pair of
    opcodes it names, or None where the index falls in a rounding hole (free
    space the frame may grow into).

    Returns {cluster, a: {op, imm, imm_high}, b: {...}, imm_high} — `imm_high`
    on a slot is that op's immediate bits above the field its row draws; the
    frame-level one is the same for a shared immediate."""
    for n, c in enumerate(frame["tables"]):
        if not (c["at"] <= index < c["at"] + c["size"]):
            continue
        rel = index - c["at"]
        high = rel & ((1 << c["ext"]) - 1)
        j = rel >> c["ext"]
        if c["diagonal"]:
            p, off = _entry_at(c["pairs"], j)
            if p is None:
                return None                  # padding inside the cluster
            ea, eb = p["a"], p["b"]
            oa, ob = off >> p["ext_b"], off & ((1 << p["ext_b"]) - 1)
        else:
            ea, oa = _entry_at(c["a"], j // c["span_b"])
            eb, ob = _entry_at(c["b"], j % c["span_b"])
        if ea is None or eb is None:
            return None                      # padding inside the cluster
        out = {"cluster": n, "a": _resolved(ea, oa), "b": _resolved(eb, ob)}
        if c["ext"]:
            out["imm"] = dict(c["imm"])
            out["imm_high"] = high
        return out
    return None


def covers(frame, word):
    """Does this frame's block contain the codepoint the selector word names?"""
    cp = word >> (WBITS - frame["depth"])
    return frame["base_cp"] <= cp < frame["base_cp"] + (1 << frame["opsel"])


def decode(frames, word, rd=None):
    """Resolve a selector word — opcode5:funct3:g:h, MSB first — into the frame
    and the two opcodes it names, or None if nothing is assigned there.

    `rd` is the rd field's register name or number. A hosted frame rides inside
    its host's codepoints and is told apart by rd holding the one reserved
    sentinel its rows name (x0 or x2); any other rd means the host owns the
    codepoint."""
    name = SENTINEL_REGS.get(rd, rd if rd in SENTINEL_PATTERNS else None)
    if name is not None:
        for f in frames:
            if f.get("sentinel") == name and covers(f, word):
                cp = word >> (WBITS - f["depth"])
                hit = resolve_index(f, cp - f["base_cp"])
                return dict(hit, frame=f["name"]) if hit else None
    for f in frames:
        if not f.get("host") and covers(f, word):
            cp = word >> (WBITS - f["depth"])
            hit = resolve_index(f, cp - f["base_cp"])
            return dict(hit, frame=f["name"]) if hit else None
    return None


def word_chars(frame):
    """The 10 selector bits MSB->LSB as display chars:
       '0'/'1' identifier, 'p' op-select, '.' free/unused.

    'p', not 'o': these sit beside the constant '0'/'1' identifier bits in
    every drawing, and an 'o' there is a zero at a glance."""
    idl, opsel = frame["id_len"], frame["opsel"]
    w = []
    for pos in range(WBITS - 1, -1, -1):          # bit 9 (MSB) .. 0
        depth = WBITS - pos                        # 1..10 from the MSB
        if depth <= idl:
            w.append(str((frame["id_val"] >> (idl - depth)) & 1))
        elif depth <= idl + opsel:
            w.append(OPSEL_CHAR)
        else:
            w.append(".")
    return w


def frame_rows(spec):
    """(row_mapping, tag) for every row."""
    return [(r, r.get("tag")) for r in spec["rows"]]


def _part(text, bits):
    """One drawn piece of a column: (text, bits, stems)."""
    return (text, bits, (text.split("[")[0],))


def _tokens(row, w, sentinel=None):
    """Per grid-column token: (parts, field), with the selector bits injected —
    funct3 as its 3 bits, g/h as their bit — and, for a guest frame, the
    allocated sentinel pattern where the row says `unused`.

    `parts` is a list because a field SPLIT between two operands draws as the
    two operands it holds, each sized to its own bits, rather than as one box
    with both names crammed in. The sub-widths add back to the column's own
    (Σ 2·bits-1, plus one separator each), so splitting a column never moves
    anything else on the line."""
    fn3 = " ".join(w[5:8])
    g_char, h_char = w[8], w[9]
    out = []
    for field in grid_columns(_GRID):
        v = row.get(field)
        bits = field_width(_GRID, field)
        if field == "funct3":
            out.append(([(fn3, bits, ())], field))
        elif field == "g":
            out.append(([(g_char, bits, ())], field))
        elif field == "h":
            out.append(([(h_char, bits, ())], field))
        elif v is None:
            out.append(([("free", bits, ())], field))
        elif v == "unused":
            pat = SENTINEL_PATTERNS.get(sentinel) or ". . . . ."
            out.append(([(pat, bits, ())], field))
        elif isinstance(v, list):
            out.append(([_part(str(p["value"]), int(p["bits"])) for p in v],
                        field))
        else:
            out.append(([_part(str(v), bits)], field))
    return out


# --- one box per FIELD -----------------------------------------------------
# The grid's columns tile the word contiguously (h[31], g[30], funct5[29:25],
# ... marker[1:0]), so two ADJACENT columns always hold adjacent bits.  Where
# they also hold two halves of one thing, the column boundary between them is
# an artefact of the grid rather than of the encoding, and drawing it invites
# the reader to count two fields where there is one.  Split columns are the
# mirror image: one column, two operands, and no boundary drawn between them.
#
# So a box is a FIELD, not a column: adjoining columns fuse, split columns
# divide.  Both only ever move a boundary ONTO a real bit boundary; every
# character position on the line, and the line's width, are untouched.
_RANGE = re.compile(r"^(\w+)\[(\d+):(\d+)\]$")

# Columns the renderer fills from the selector word rather than from an
# operand.  funct3 already draws as one three-bit box; h and g are the same
# kind of thing, split only because the grid names them separately.
_BIT_COLUMNS = {"h", "g", "funct3"}


def fuse(left, right):
    """The one cell `left` and `right` make when they draw adjoining bits of
    the same thing, or None when they do not.

    Two cases fuse. Sub-ranges of ONE field whose bits meet — `imma[9:5]`
    beside `imma[4:0]` IS `imma[9:0]`. And two opcode-bit columns, which spell
    one wider bit box.

    A SPLIT column never fuses with its neighbour, and must not: its parts
    interleave with that neighbour's, so the two pieces the drawing puts side
    by side are not adjoining bits of anything (dual-setup-pair draws
    `imma[6:5]` in the top of rs1, three bits of rda below it, and `imma[4:0]`
    in the column to the LEFT — a fused box would claim a contiguity the
    encoding does not have)."""
    (lparts, lfield), (rparts, rfield) = left, right
    if len(lparts) != 1 or len(rparts) != 1:
        return None
    (ltext, lbits, lstems), (rtext, rbits, rstems) = lparts[0], rparts[0]
    joined = None
    if lfield in _BIT_COLUMNS and rfield in _BIT_COLUMNS:
        joined = f"{ltext} {rtext}"
    else:
        lm, rm = _RANGE.match(ltext), _RANGE.match(rtext)
        if lm and rm and lm[1] == rm[1] and int(lm[3]) == int(rm[2]) + 1:
            joined = f"{lm[1]}[{lm[2]}:{rm[3]}]"
    if joined is None:
        return None
    return ([(joined, lbits + rbits, lstems + rstems)], lfield)


def fuse_tokens(tokens):
    """`tokens` with every run of adjoining fields merged, as
    (parts, field, span) — `span` being the grid columns the cell covers.

    Fusion depends on the ROW alone, never on which slot is being drawn, so a
    frame's plain form and its per-slot copies all break at the same column
    boundaries and stay aligned with one another."""
    out = []
    for token in tokens:
        merged = fuse(out[-1][:2], token) if out else None
        if merged:
            out[-1] = merged + (out[-1][2] + 1,)
        else:
            out.append(token + (1,))
    return out


def shorten(text, width):
    """(text_that_fits, was_it_shortened). A box narrower than the name it
    holds drops the bit range first, then squeezes the stem by taking its
    leading characters plus its LAST one — `immb` reads `imb`, which still
    tells it from `imma`. Every shortening is footnoted (`frame_notes`), and
    nothing is ever allowed to overflow: an overflowing cell would push the
    rest of the line sideways and break the drawing."""
    if len(text) <= width:
        return text, False
    stem = text.split("[")[0]
    if len(stem) <= width:
        return stem, True
    return (stem[:max(1, width - 1)] + stem[-1])[:width], True


def cells(tokens, colwidths):
    """The boxes of one encoding line: (text, width, stems, full) each, where
    `full` is the unshortened name when the box could not hold it.

    A fused field is one box spanning several columns; a split column is
    several boxes, each 2·bits-1 wide. Since those sub-widths plus their
    separators add back to the column's own width, the line is the same length
    either way — only where the boundaries fall changes."""
    out, pos = [], 0
    for parts, _field, span in fuse_tokens(tokens):
        width = _spanned(colwidths, pos, span)
        for text, bits, stems in parts:
            w = width if len(parts) == 1 else 2 * bits - 1
            fit, cut = shorten(text, w)
            out.append((fit, w, stems, text if cut else None))
        pos += span
    return out


def render_line(tokens, o5, colwidths, keep=None):
    """Render one encoding line. `keep(base)` decides whether a slot-owned
    field is shown; when None every field shows (the plain form). Opcode bits,
    sentinel patterns and free fields always show; erased cells render blank."""
    rendered = []
    for text, width, stems, _full in cells(tokens, colwidths):
        if keep is None or not stems:
            show = True                            # opcode bits / sentinel / free
        else:
            show = any(keep(stem) for stem in stems)
        rendered.append(_center(text if show else "", width))
    pos = len(tokens)
    for token in [o5, MARKER]:                      # opcode5 + marker: shared
        rendered.append(_center(token, _spanned(colwidths, pos, 1)))
        pos += 1
    return "│" + "│".join(rendered) + "│"


def frame_notes(frame, colwidths):
    """`short = full` for every name a box was too narrow to spell out, in the
    order the frame's rows first draw them.

    Only a split column's sub-box can be too narrow — it is sized to its own
    bits rather than to the column — so this is normally empty.

    Two names shortening to the SAME text would make the footnote a lie; that
    is what test_layout_explains_every_shortened_name gates."""
    w = word_chars(frame)
    seen = {}
    for row in frame["spec"]["rows"]:
        for text, _width, _stems, full in cells(_tokens(row, w,
                                                        frame.get("sentinel")),
                                                colwidths):
            if full:
                seen.setdefault(text, full)
    return [f"{short} = {full}" for short, full in seen.items()]


# --- template <-> row matching --------------------------------------------
def line_ops(line):
    """Encoded operand names an asm line uses (implicit regs dropped)."""
    _, _, rest = line.partition(" ")
    return set(_OPERAND.findall(rest)) - _IMPLICIT


_ALT = re.compile(r"\b\w+(?:/\w+)+\b")


def specialize(line, row_ops):
    """Collapse each `X/Y` operand alternate to the side this row encodes, e.g.
    `rs2a/imma` -> `rs2a` on a register row, `imma` on an immediate row. Opcode
    alternates (`mv/li`, `beqz/bnez`) have neither side in row_ops — the op-
    select bits don't pin them down — so they are left as written."""
    def repl(m):
        keep = [p for p in m.group(0).split("/") if p in row_ops]
        return keep[0] if len(keep) == 1 else m.group(0)
    return _ALT.sub(repl, line)


# Registers a template line can name literally rather than through a field the
# frame encodes -- fixed by the opcode (`beqz`'s zero, `jalr_link_t1`'s t1) or
# by convention (the `tmp` scratch register). The standard RISC-V ABI names
# plus `tmp` cover every register a template could hard-code.
_ABI_REGS = {
    "zero", "ra", "sp", "gp", "tp", "fp", "tmp",
    *(f"t{i}" for i in range(7)), *(f"s{i}" for i in range(12)),
    *(f"a{i}" for i in range(8)), *(f"x{i}" for i in range(32)),
}
_REG_TOKEN = re.compile(r"\b\w+\b")


def matches(row, tag, a_ops, b_ops, sp_template, has_sp_rows):
    """A row realises a template when every field the row encodes is an operand
    of the template, and (for frames that distinguish them) its SP-relative
    variant agrees."""
    if not row_operands(row, _GRID) <= (a_ops | b_ops | {"unused"}):
        return False
    if has_sp_rows and sp_template != (tag == "SP-relative"):
        return False
    return True


def field_type(stem):
    """rs/rd/rsd/imm, from the field-name convention this codebase already
    uses everywhere else (imm_contracts.py's `stem.startswith(("rd","rsd"))`,
    encoding_render.py's rd-column test): `rsd*` reads and writes the same
    register, `rd*` only writes, `imm*` is an immediate, everything else
    (`rs1*`, `rs2*`, `rbase`) only reads."""
    if stem.startswith("rsd"):
        return "rsd"
    if stem.startswith("rd"):
        return "rd"
    if stem.startswith("imm"):
        return "imm"
    return "rs"


def slot_fields(row, grid, ops):
    """[{name, bits, type}] for the fields `ops` names, row order, a split
    field's pieces summed into the width it draws as a whole."""
    order, bits = [], {}
    for _field, stem, w, _raw in row_parts(row, grid):
        if stem not in ops:
            continue
        if stem not in bits:
            order.append(stem)
        bits[stem] = bits.get(stem, 0) + w
    return [{"name": s, "bits": bits[s], "type": field_type(s)} for s in order]


# Mnemonics used in templates that never write a register -- a store's target
# is memory, branches don't write, and `jr`-style jumps drop the link. Every
# operand position in one of these lines is a read.
_NO_DEST_MNEMS = {"bXX", "beqz", "bnez", "j", "jr_any", "jr_t1", "store"}


def _operand_roles(pos, mnem, n_operands):
    """The register roles operand `pos` plays for one mnemonic alternate:
    {'w'} at position 0 (the destination) unless `mnem` never writes one,
    {'r'} everywhere else -- except `addi`'s bare 2-operand form (`addi sp,
    -16*imm`), which elides its own name as rs1, so its one operand is read
    AND written, not write-only."""
    if pos != 0 or mnem in _NO_DEST_MNEMS:
        return {"r"}
    if mnem == "addi" and n_operands == 2:
        return {"r", "w"}
    return {"w"}


def slot_implicit(line):
    """[{name, type}] for the registers a template line hard-codes -- fixed
    by the opcode (`beqz`'s zero, `jalr_link_t1`'s t1) or by convention (the
    `tmp` scratch register), never by an encoded field. `type` is read from
    how THIS line actually uses each one: `tmp` is rd where `alu tmp, ...`
    writes it and rs where `alu rdb, tmp, ...` reads it back; `ra` is rsd on
    `jalr_ra ra, imm(ra)`, which both writes the link register and reads the
    same register as its own base -- one name naming both roles in one line."""
    mnem, _, rest = line.partition(" ")
    operands = [o.strip() for o in rest.split(",")] if rest else []
    order, roles = [], {}
    for pos, operand in enumerate(operands):
        for t in _REG_TOKEN.findall(operand):
            if t not in _ABI_REGS:
                continue
            if t not in roles:
                order.append(t)
                roles[t] = set()
            for alt in mnem.split("/"):
                roles[t] |= _operand_roles(pos, alt, len(operands))
    out = []
    for name in order:
        rw = roles[name]
        typ = "rsd" if rw >= {"r", "w"} else ("rd" if rw == {"w"} else "rs")
        out.append({"name": name, "type": typ})
    return out


def frame_templates(frame, grid):
    """Per template, per matching row: the A and B slots as field lists --
    encoded fields (name, bits, type) plus any hard-coded register the line
    names outright (`implicit`) -- and the specialized asm each slot runs."""
    spec = frame["spec"]
    rows = frame_rows(spec)
    has_sp = any(tag == "SP-relative" for _, tag in rows)
    out = []
    for pair in spec["templates"]:
        a_line, b_line = pair[0].strip(), pair[1].strip()
        a_ops, b_ops = line_ops(pair[0]), line_ops(pair[1])
        sp_t = any("(sp)" in ln for ln in pair)
        hits = [(r, t) for r, t in rows
                if matches(r, t, a_ops, b_ops, sp_t, has_sp)]
        approx = False
        if not hits:
            # Contorted frames (e.g. dual-mem) reuse one encoding row across
            # several asm forms, so no row's fields are a strict subset of this
            # template's operands. Fall back to the single best-overlap row.
            cand = [(r, t) for r, t in rows
                    if not (has_sp and sp_t != (t == "SP-relative"))]
            cand.sort(key=lambda rt: -len(row_operands(rt[0], grid)
                                          & (a_ops | b_ops)))
            if cand and row_operands(cand[0][0], grid) & (a_ops | b_ops):
                hits, approx = [cand[0]], True
        if not hits:
            out.append({"a": {"template": a_line}, "b": {"template": b_line},
                       "unrealised": True})
            continue
        for row, tag in hits:
            rops = row_operands(row, grid)
            entry = {
                "a": {"fields": slot_fields(row, grid, a_ops),
                      "implicit": slot_implicit(a_line),
                      "template": specialize(a_line, rops)},
                "b": {"fields": slot_fields(row, grid, b_ops),
                      "implicit": slot_implicit(b_line),
                      "template": specialize(b_line, rops)},
            }
            if tag:
                entry["tag"] = tag
            if approx:
                entry["approx"] = True
            out.append(entry)
    return out


def _slot_yaml_lines(slot):
    """One `a:`/`b:` mapping's content as YAML text lines (unindented)."""
    lines = [f"fields: {_flow(slot['fields'])}"]
    if slot.get("implicit"):
        lines.append(f"implicit: {_flow(slot['implicit'])}")
    lines.append(f"template: {_q(slot['template'])}")
    return lines


def frame_templates_lines(frame, grid):
    """The frame's `templates:` field breakdown as YAML text lines
    (unindented -- callers nest them under the frame's own indent)."""
    entries = frame_templates(frame, grid)
    if not entries:
        return []
    out = ["templates:"]
    for e in entries:
        if e.get("unrealised"):
            out.append("- unrealised: true")
            out.append(f"  a: {{template: {_q(e['a']['template'])}}}")
            out.append(f"  b: {{template: {_q(e['b']['template'])}}}")
            continue
        out.append("- a:")
        out.extend("    " + ln for ln in _slot_yaml_lines(e["a"]))
        out.append("  b:")
        out.extend("    " + ln for ln in _slot_yaml_lines(e["b"]))
        if e.get("tag"):
            out.append(f"  tag: {_q(e['tag'])}")
        if e.get("approx"):
            out.append("  approx: true")
    return out


def frame_body_lines(frame, colwidths):
    """The frame's plain form: the header row's bit pattern, once per row."""
    spec = frame["spec"]
    w = word_chars(frame)
    o5 = " ".join(w[0:5])
    out = []
    for row, tag in frame_rows(spec):
        line = render_line(_tokens(row, w, frame.get("sentinel")), o5, colwidths)
        out.append(line + (f" ({tag})" if tag else ""))
    notes = frame_notes(frame, colwidths)
    if notes:
        out.append("")
        out.append("    where " + ",  ".join(notes))
    return out


# --- loading ---------------------------------------------------------------
def load(path=None):
    """Read encoding.yaml and assign every frame its block, its identifier and
    its op tables. Returns (frames, info) with frames in codepoint order."""
    spec = yaml.safe_load(open(path or os.path.join(ROOT, "encoding.yaml")))
    grid = spec["grid"]
    global _GRID
    _GRID = grid
    frames = []
    for node in spec["doc"]:
        if "frame" not in node:
            continue
        f = node["frame"]
        if not f.get("ops"):
            continue
        base = opcode_demand(f.get("ops"))          # a×b combos, before ext
        d = opcode_codepoints(f, grid)              # real codepoints, ext-aware
        budget = f.get("budget") or d               # reserve current fill if none
        fmt = a_format(f)
        frames.append({
            "name": f["name"], "spec": f, "demand": d, "base": base,
            "budget": budget, "opsel": opsel_bits(budget),
            "fmt": fmt, "a_rank": _FMT_RANK[fmt],
            "role": rd_column_role(f, grid),
            "doc_order": len(frames),
        })

    complaints = []
    for f in frames:
        complaints += lint_frame(f["spec"], grid)

    # A1.11 — hosting.  A frame whose rd column carries the sentinel is
    # selected by that bit pattern, not by an opcode of its own, so it needs no
    # top-level block: it rides inside a host's codepoints, in the rd slice the
    # host can never reach (rules.py enforces that the host never writes x0 or
    # x2 there).  The host gives up nothing -- that slice was dead -- and the
    # guest's whole budget comes back to the namespace.
    guests = [f for f in frames if f["role"] == "guest"]
    hosts = sorted((f for f in frames if f["role"] == "host"),
                   key=lambda f: -(1 << f["opsel"]))
    # A codepoint can carry one guest per distinct sentinel, so guests on
    # DIFFERENT sentinels overlay the same codepoints for free.  Track the
    # lending per (host, sentinel).
    lend = {}
    unhosted = []
    for g in sorted(guests, key=lambda f: -f["budget"]):
        # `rd: unused` leaves the sentinel to the enumerator: try each pattern
        # in the pool, in a fixed order for determinism, and take the first
        # (host, sentinel) with room.  Largest guests place first so the
        # choice cannot be starved by a small one.
        need = g["budget"]                       # rd is fixed: one op per codepoint
        for sentinel in SENTINEL_ORDER:
            for h in hosts:
                key = (h["name"], sentinel)
                free = (1 << h["opsel"]) - lend.get(key, 0)
                if free >= need:
                    lend[key] = lend.get(key, 0) + need
                    g["sentinel"] = sentinel
                    g["host"] = h["name"]
                    g["host_cp"] = need
                    break
            if g.get("host"):
                break
        else:
            unhosted.append(g["name"])

    hosted = [f for f in frames if f.get("host")]
    order, reserved, W = allocate_blocks([f for f in frames if not f.get("host")])

    # Place each guest at the top of its host's block, largest first so every
    # sub-block stays aligned to its own size.  The guest's identifier is a
    # real prefix in the same namespace -- it just lives inside codepoints the
    # host already reserved, and is told apart from the host by the rd field.
    by_name = {f["name"]: f for f in order}
    taken = {}
    for g in sorted(hosted, key=lambda f: -f["host_cp"]):
        h = by_name[g["host"]]
        need = g["host_cp"]
        key = (h["name"], g["sentinel"])
        off = taken.get(key, 0) + need
        taken[key] = off
        g["opsel"] = max(0, (need - 1).bit_length())
        g["base_cp"] = h["base_cp"] + (1 << h["opsel"]) - off
        g["id_len"] = max(0, W - g["opsel"])
        g["id_val"] = g["base_cp"] >> g["opsel"]
        g["depth"] = min(W, WBITS)

    overfull = []
    for f in frames:
        f["tables"], f["used"] = frame_tables(f["spec"], grid)
        f["space"] = 1 << sel_bits(f)
        if f["used"] > f["space"]:
            overfull.append(f["name"])

    # Emission order is the frame's order in encoding.yaml, not its codepoint —
    # allocation had to sort by (-opsel, a_rank, name) to buddy-place blocks,
    # but the reader wants the document's own order back, not the namespace's.
    frames.sort(key=lambda f: f["doc_order"])
    info = {
        "grid": grid, "reserved": reserved, "W": W, "blocks": len(order),
        "opsets": spec.get("opsets") or {},
        "pseudo_ops": spec.get("pseudo_ops") or {},
        "complaints": complaints, "unhosted": unhosted, "hosted": hosted,
        "overflow": reserved > (1 << WBITS), "overfull": overfull,
        "overbudget": [f["name"] for f in frames
                       if f["demand"] > (1 << f["opsel"])],
    }
    return frames, info


# --- self-check ------------------------------------------------------------
def check(frames, grid):
    """Every assigned codepoint must resolve to exactly one frame and one op
    pair, every combination the tables encode must be one the yaml allows, and
    every op the yaml declares must be reachable. Returns a list of complaints;
    empty means the tables and the namespace agree."""
    bad = []
    seen = {}
    for f in frames:
        allowed = {(op_name(x), op_name(y))
                   for c in f["spec"].get("ops") or [] for x, y in cluster_pairs(c)}
        found = set()
        for index in range(1 << sel_bits(f)):
            hit = resolve_index(f, index)
            if hit is None:
                continue
            found.add((hit["a"]["op"], hit["b"]["op"]))
            cp = f["base_cp"] + index
            rd = f.get("sentinel")
            key = (cp, rd)
            if key in seen:
                bad.append(f"codepoint {cp} (rd={rd}) claimed by both "
                           f"{seen[key]} and {f['name']}")
            seen[key] = f["name"]
            word = cp << (WBITS - f["depth"])
            got = decode(frames, word, rd or "x1")
            if not got or got["frame"] != f["name"]:
                bad.append(f"{f['name']}: index {index} does not decode back "
                           f"(got {got and got['frame']})")
        if found - allowed:
            bad.append(f"{f['name']}: tables encode op pairs the yaml forbids: "
                       f"{sorted(found - allowed)}")
        if allowed - found:
            bad.append(f"{f['name']}: op pairs with no codepoint: "
                       f"{sorted(allowed - found)}")
        # The enumeration must cost exactly what the pricing model charges,
        # before the rounding that buys the bit-fields.
        dense = sum(sum(e["n"] for e in c["pairs"]) * (1 << c["ext"])
                    if c["diagonal"] else
                    sum(e["n"] for e in c["a"]) * sum(e["n"] for e in c["b"])
                    * (1 << c["ext"])
                    for c in f["tables"])
        priced = opcode_codepoints(f["spec"], grid)
        if dense != priced:
            bad.append(f"{f['name']}: tables enumerate {dense} codepoints, "
                       f"pricing charges {priced}")
    return bad


# --- yaml emission ---------------------------------------------------------
BANNER = "# Generated by util/encoding_assign.py from encoding.yaml — do not edit by hand."

PREAMBLE = """\
#
# One entry per packet frame: a pair of RISC-V instructions encoded in a single
# 32-bit packet.  `layout` is the frame's bit layout, with the frame identifier
# filled in as constants and only the op-select bits left as letters.
#
# A layout box is one FIELD, not one grid column.  Two columns holding
# adjoining bits of the same thing are drawn FUSED, so an immediate spread
# across funct5 and rs2 reads as the single `imma[9:0]` it is and the h and g
# opcode bits share one box the way funct3's three already do; a column split
# between two operands is DIVIDED at the bit where they meet (`imb│rdb`), and
# any name the narrower box cannot spell is shortened and then given in a
# `where` line at the foot of the layout.  Both only move a boundary onto a
# real bit boundary: the drawing keeps its width and its character positions,
# and the `header` above stays per-column, so one box may span several of its
# cells or several boxes share one.
#
# READING THE OP-SELECT BITS
#
# Every 'p' in a layout is one bit of that frame's op-select index, taken MSB
# first in the order  opcode5[4:0], funct3[2:0], g, h.  That is NOT the drawing's
# left-to-right order: read the `opcode` box first (dropping its trailing `1 0`
# packet marker), then `fn3` in the middle, then the `g` column, then `h` at the
# far left.  A hosted frame (`hosted_in`) rides inside its host's codepoints and
# is told apart by its `rd` sentinel, which its rows draw as a constant: the
# sentinel selects the frame and contributes no index bit.
#
# `select` on a frame spells the whole 10-bit selector word: '0'/'1' are the
# identifier, 'p' the op-select bits, '.' a bit the frame does not reach.
# `block` is how many op-select indices the frame has — 2^(number of 'p's).
#
# RESOLVING AN INDEX TO A PAIR OF OPCODES
#
# `opcodes` is a list of clusters, disjoint in both the index and the pairs they
# allow.  Each has its own aligned sub-block of the index and spells it out in
# `select` — the SAME bits the layout draws as 'p', re-lettered by the job each
# one does inside this cluster:
#
#     constant bits select the cluster
#     'a' bits index the cluster's `a` table       (the first instruction)
#     'b' bits index the cluster's `b` table       (the second)
#     'p' bits index the cluster's `pairs` table   (a cluster that states a
#                                                   diagonal rather than a
#                                                   cross-product; its 'a'/'b'
#                                                   bits then extend the two
#                                                   immediates, nothing more)
#     'i' bits, where present, carry `imm.ext`     (a shared immediate's
#                                                   high bits)
#
# so, given an index i and a cluster whose sub-block is [at, at + n):
#
#     rel = i - at
#     imm_high = rel & ((1 << i_bits) - 1)         # 'i' bits, if any
#     j       = rel >> i_bits
#     a_index = j // b_span                        # b_span = 1 << (# 'b' bits)
#     b_index = j %  b_span
#
# and for a `pairs` cluster, j indexes that table instead — the allowed (A, B)
# combinations are a LIST there, not a product, so a `same_op` cluster cannot
# spell a mismatched pair at all.
#
# A table entry covers indices [at, at + n).  n > 1 means that op declares more
# immediate range than the field its row draws: it takes 2^extra entries, and
# the offset within them is the immediate's high bits — `imm.ext` names which.
# `imm.bits` is the op's total immediate width, `imm.field` the width the row
# draws, `imm.scale` the multiplier the field carries where the op has one.  An
# entry with no `imm` takes the frame's field as drawn (a register-form op
# names no immediate at all).  Indices no cluster or table entry claims are
# unassigned — the room each frame has to grow.
#
# Two kinds of name in those tables are not RISC-V mnemonics: `xlen_switchable`
# ops, one opcode whose width follows the base ISA, and `pseudo_ops`, which are
# register/immediate patterns on a real instruction — both are listed below
# with what they encode as.
"""


def _q(s):
    """A double-quoted yaml scalar (JSON strings are valid yaml)."""
    return json.dumps(str(s))


def _flow(value):
    """A yaml value on one line, passed through unchanged."""
    return yaml.safe_dump(value, default_flow_style=True,
                          sort_keys=False, width=10 ** 6).strip()


def _imm_yaml(imm, n=1):
    """Flow-mapped immediate contract, with the bits the index carries named."""
    if not imm:
        return None
    parts = [f"bits: {imm['bits']}"] if imm.get("bits") is not None else []
    if "signed" in imm:
        parts.append(f"signed: {json.dumps(bool(imm['signed']))}")
    if imm.get("scale"):
        parts.append(f"scale: {imm['scale']}")
    if imm.get("field") is not None:
        parts.append(f"field: {imm['field']}")
    if n > 1 and imm.get("bits") is not None and imm.get("field") is not None:
        ext = "imm[%d:%d]" % (imm["bits"] - 1, imm["field"])
        parts.append(f"ext: {_q(ext)}")
    return "{" + ", ".join(parts) + "}"


def _entry_yaml(e):
    imm = _imm_yaml(dict(e["imm"], field=e["field"]) if e["imm"] else {}, e["n"])
    parts = [f"at: {e['at']}", f"n: {e['n']}", f"op: {_q(e['op'])}"]
    if imm:
        parts.append(f"imm: {imm}")
    return "{" + ", ".join(parts) + "}"


def _slot_yaml(e):
    """One side of a `pairs` entry: the op, and the immediate it extends."""
    imm = _imm_yaml(dict(e["imm"], field=e["field"]) if e["imm"] else {}, e["n"])
    return "{" + f"op: {_q(e['op'])}" + (f", imm: {imm}" if imm else "") + "}"


def emit_yaml(frames, info):
    """The ciscv-proto.yml data file: every frame, its layout, its opcode
    tables and the bit-level meaning of every op-select bit."""
    widths = display_widths(info["grid"])
    out = [BANNER, PREAMBLE.rstrip("\n"), ""]
    out.append("selector:")
    out.append(f"  word: [opcode5, funct3, g, h]        # {WBITS} bits, MSB first")
    out.append(f"  bits: {WBITS}")
    out.append(f"  codepoints: {1 << WBITS}")
    out.append(f"  reserved: {info['reserved']}")
    out.append(f"  blocks: {info['blocks']}")
    out.append("  sentinels: {rd: [x0, x2]}"
               "        # reserved rd patterns; a hosted frame names one")
    out.append("")
    out.append("header: |")
    for line in header_lines(widths, info["grid"]):
        out.append("  " + line)
    out.append("")
    opsets = info["opsets"]
    if opsets.get("xlen_switchable"):
        out.append("xlen_switchable:")
        for name, by_xlen in opsets["xlen_switchable"].items():
            out.append(f"  {name}: {_flow(by_xlen)}")
        out.append("")
    if info["pseudo_ops"]:
        out.append("pseudo_ops:")
        for name, d in info["pseudo_ops"].items():
            keep = {k: v for k, v in d.items() if k in ("base", "encode")}
            out.append(f"  {name}: {_flow(keep)}")
        out.append("")
    out.append("frames:")
    for f in frames:
        out.append(f"- name: {_q(f['name'])}")
        if f["spec"].get("does"):
            out.append(f"  does: {_q(' '.join(str(f['spec']['does']).split()))}")
        out.append(f"  a_format: {f['fmt']}")
        idl = f["id_len"]
        out.append(f"  id: {_q(format(f['id_val'], f'0{idl}b') if idl else '')}")
        out.append(f"  select: {_q(''.join(word_chars(f)))}")
        if f.get("host"):
            out.append(f"  hosted_in: {_q(f['host'])}")
            out.append(f"  rd: {f['sentinel']}"
                       f"                      # the allocated sentinel:"
                       f" what tells it from its host")
        out.append(f"  block: {f['space']}"
                   f"          # op-select indices; {f['used']} used, "
                   f"{f['space'] - f['used']} free")
        out.append("  layout: |")
        for line in frame_body_lines(f, widths):
            out.append(("    " + line).rstrip())
        for line in frame_templates_lines(f, info["grid"]):
            out.append("  " + line)
        out.append("  opcodes:")
        for c in f["tables"]:
            out.append(f"  - select: {_q(cluster_word(f, c))}")
            out.append(f"    at: {c['at']}")
            out.append(f"    n: {c['size']}")
            if c["ext"]:
                out.append(f"    imm: {_imm_yaml(c['imm'], 1 << c['ext'])}")
            if c["diagonal"]:
                out.append(f"    {c['diagonal']}: true")
                out.append("    pairs:")
                for p in c["pairs"]:
                    out.append(f"    - {{at: {p['at']}, n: {p['n']}, "
                               f"a: {_slot_yaml(p['a'])}, b: {_slot_yaml(p['b'])}}}")
            else:
                for slot in ("a", "b"):
                    out.append(f"    {slot}:")
                    for e in c[slot]:
                        out.append(f"    - {_entry_yaml(e)}")
    return "\n".join(out) + "\n"


# --- text report -----------------------------------------------------------
def _op_text(e):
    """`op` plus the immediate contract it carries, if any."""
    imm = dict(e["imm"], field=e["field"]) if e["imm"] else {}
    tail = ""
    if imm.get("bits") is not None:
        sign = "s" if imm.get("signed") else "u"
        tail = f"   imm {imm['bits']}{sign}"
        if imm.get("scale"):
            tail += f" x{imm['scale']}"
        if e["n"] > 1:
            tail += f" = field[{imm['field']-1}:0] + index[{imm['bits']-1}:{imm['field']}]"
    return f"{e['op']:<10}{tail}".rstrip()


def _span_text(e):
    """The indices a table entry covers."""
    return f"{e['at']}" if e["n"] == 1 else f"{e['at']}..{e['at']+e['n']-1}"


def print_tables(f):
    for n, c in enumerate(f["tables"]):
        head = f"    cluster {n}: index {cluster_word(f, c)}"
        diag = f", {c['diagonal']}" if c["diagonal"] else ""
        print(f"{head}   ({c['size']} codepoint(s) at {c['at']}{diag})")
        if c["ext"]:
            imm = c["imm"]
            print(f"      shared immediate {imm['bits']} bits = "
                  f"field[{imm['field']-1}:0] + index[{imm['bits']-1}:{imm['field']}]")
        if c["diagonal"]:
            for p in c["pairs"]:
                print(f"      {_span_text(p):>9}  {_op_text(p['a'])} ; "
                      f"{_op_text(p['b'])}")
            continue
        for slot in ("a", "b"):
            for e in c[slot]:
                print(f"      {slot} {_span_text(e):>7}  {_op_text(e)}")


def report(frames, info):
    widths = display_widths(info["grid"])
    header = header_lines(widths, info["grid"])
    reserved, order = info["reserved"], info["blocks"]

    if info["complaints"]:
        print("## Codepoint-accounting complaints\n")
        for c in info["complaints"]:
            print(f"  ✗ {c}")
        print()

    print("# Assigned opcode bit-patterns (variable-length prefix code)\n")
    print(f"Selector word = opcode5(5):funct3(3):g:h = {WBITS} bits, "
          f"{1<<WBITS} codepoints, read MSB->LSB.")
    print("'0'/'1' = frame identifier (constant), 'p' = op-select, "
          "'.' = free/unused.\n")
    print(f"Reserved {reserved}/{1<<WBITS} codepoints across {order} frames "
          f"({100*reserved/(1<<WBITS):.0f}%), each frame a fixed block sized to its\n"
          f"budget so it can grow into its own free slots without moving the rest.\n")
    hosted = info["hosted"]
    if hosted:
        print("Hosted frames (rd = x0/x2 sentinel) take NO block of their own — each\n"
              "rides in a host's codepoints, in the rd slice that host cannot reach.\n"
              "The host loses nothing: it keeps every one of those codepoints for its\n"
              "own encodings, which all name a real register there.  Each lent\n"
              "Each guest names ONE reserved pattern, so a codepoint carries one\n"
              "guest per distinct sentinel -- guests on x0 and x2 overlay for free.\n")
        for g in hosted:
            print(f"    {g['name']:20} budget {g['budget']:>3} -> "
                  f"{g['host_cp']:>3} codepoint(s) of {g['host']} (rd = {g['sentinel']})")
        print(f"    {'':20} {sum(g['budget'] for g in hosted):>10} codepoints "
              f"returned to the namespace\n")
    if info["unhosted"]:
        print(f"⚠ Sentinel frames with no host large enough: "
              f"{', '.join(info['unhosted'])}\n")
    print("Each frame prints its form, then its templates: per matched asm pair, the\n"
          "A slot and the B slot as field lists (name, bits, type) plus any register\n"
          "the line hard-codes rather than encodes (`implicit`, typed rs/rd/rsd from\n"
          "how THAT line uses it -- a name naming both a write and a read in one line,\n"
          "like jalr_ra's own base register, is rsd). A box in the form is one FIELD,\n"
          "not one column: columns holding adjoining bits of one thing are drawn\n"
          "fused, a column split between two operands is drawn divided, and any name\n"
          "a box is too narrow to spell is given in a `where` line below it.\n")
    print("Then its op tables: which op-select index selects which pair of opcodes.\n"
          "Constant bits pick the ops cluster, 'a'/'b' index that cluster's two op\n"
          "tables, 'i' carries a shared immediate's high bits.  An op spanning\n"
          "several indices is one that buys immediate range by opcode repetition.\n")

    for f in frames:
        idl, opsel, depth = f["id_len"], f["opsel"], f["depth"]
        block = 1 << opsel
        room = block - f["demand"]
        opc = _FMT_OPC[f["fmt"]]
        opc_s = f"opcode[6:2]≈{opc:05b}" if opc is not None else "opcode[6:2]=mixed"
        tag = ""
        if f["used"] > f["space"]:
            tag = "   [⚠ op tables exceed the block]"
        elif f["demand"] > block:
            tag = "   [⚠ current fill exceeds its block]"
        idbits = f"{f['id_val']:0{idl}b}" if idl else "(none)"
        print(f"## {f['name']}{tag}")
        if f.get("host"):
            print(f"    A-slot: {f['fmt']:7} ({opc_s})   no block of its own; "
                  f"selected by rd = {f['sentinel']}\n"
                  f"    inside {f['host_cp']} codepoint(s) of {f['host']}; "
                  f"identifier {idl} bit(s) = {idbits}")
        else:
            print(f"    A-slot: {f['fmt']:7} ({opc_s})   "
                  f"block {block} (budget {f['budget']}); using {f['demand']}, "
                  f"{room} free to grow; identifier {idl} bit(s) = {idbits}; "
                  f"depth {depth}/{WBITS}")
        print(f"    op-select: {sel_bits(f)} bit(s), {f['space']} index/es, "
              f"{f['used']} used by the tables below, {f['space']-f['used']} free")
        print()
        print("\n".join(header))
        print("\n".join(frame_body_lines(f, widths)))
        tmpl_lines = frame_templates_lines(f, info["grid"])
        if tmpl_lines:
            print()
            print("\n".join("    " + ln for ln in tmpl_lines))
        print()
        print_tables(f)
        print()

    print("─" * 72)
    if info["overflow"]:
        print(f"⚠ OVERFLOW: reserved {reserved} > {1<<WBITS} codepoints — the planned\n"
              f"  budgets do not fit the namespace. Shrink some budgets or op-lists.")
    else:
        print(f"Planned budgets FIT: {reserved}/{1<<WBITS} reserved, "
              f"{(1<<WBITS)-reserved} spare. Every frame can grow to its full budget\n"
              f"without overflowing the namespace or perturbing another frame.")
    if info["overbudget"]:
        print(f"\n⚠ Frames whose current fill already exceeds their block "
              f"(raise the budget): {', '.join(info['overbudget'])}")
    if info["overfull"]:
        print(f"\n⚠ Frames whose op tables do not fit their block once rounded to\n"
              f"  power-of-two fields: {', '.join(info['overfull'])}")


def show(hit):
    """One decoded packet as text."""
    if hit is None:
        return "unassigned"
    def slot(s):
        d = hit[s]
        out = d["op"]
        if "imm_high" in d:
            imm = d.get("imm") or hit.get("imm") or {}
            out += (f"  [imm[{imm.get('bits', 0)-1}:{imm.get('field', 0)}] "
                    f"= {d['imm_high']}]")
        return out
    line = f"{hit['frame']}  cluster {hit['cluster']}:  A {slot('a')} ; B {slot('b')}"
    if "imm_high" in hit:
        imm = hit["imm"]
        line += (f"   shared imm[{imm['bits']-1}:{imm['field']}] "
                 f"= {hit['imm_high']}")
    return line


def _rd_value(text):
    """--rd as either a register name or a bit pattern."""
    if text is None:
        return None
    text = text.strip()
    if text.startswith("x") and text[1:].isdigit():
        return text
    return SENTINEL_REGS.get(int(text, 0), int(text, 0))


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--yaml", default=os.path.join(ROOT, "encoding.yaml"))
    ap.add_argument("-o", "--output", help="write the data file here")
    ap.add_argument("--text", action="store_true",
                    help="the human-readable report instead of the data file")
    ap.add_argument("--decode", metavar="WORD",
                    help="resolve one selector word (opcode5:funct3:g:h)")
    ap.add_argument("--rd", metavar="REG",
                    help="rd field for --decode; x0/x2 select a hosted frame")
    ap.add_argument("--check-tables", action="store_true",
                    help="only self-check the tables; print nothing on success")
    args = ap.parse_args()

    frames, info = load(args.yaml)
    bad = check(frames, info["grid"])

    if args.decode is not None:
        print(show(decode(frames, int(args.decode, 0), _rd_value(args.rd))))
        return 1 if bad else 0

    if args.check_tables:
        for b in bad:
            print(f"  ✗ {b}", file=sys.stderr)
        return 1 if (bad or info["overfull"]) else 0

    if args.text:
        report(frames, info)
    else:
        text = emit_yaml(frames, info)
        if args.output:
            with open(args.output, "w") as fh:
                fh.write(text)
        else:
            sys.stdout.write(text)
        # The data file stays clean: warnings go to stderr.
        for c in info["complaints"]:
            print(f"  ✗ Codepoint-accounting complaints: {c}", file=sys.stderr)
        if info["overflow"]:
            print(f"⚠ OVERFLOW: reserved {info['reserved']} > {1<<WBITS} codepoints",
                  file=sys.stderr)
        if info["overbudget"]:
            print(f"⚠ Frames whose current fill exceeds their block: "
                  f"{', '.join(info['overbudget'])}", file=sys.stderr)
        if info["overfull"]:
            print(f"⚠ Frames whose op tables exceed their block: "
                  f"{', '.join(info['overfull'])}", file=sys.stderr)

    for b in bad:
        print(f"  ✗ {b}", file=sys.stderr)
    return 1 if (info["overflow"] or info["overbudget"] or info["overfull"]
                 or bad) else 0


if __name__ == "__main__":
    sys.exit(main())

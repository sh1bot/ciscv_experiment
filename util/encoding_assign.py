#!/usr/bin/env python3
"""
util/encoding_assign.py — assign concrete opcode bit-patterns to every frame in
encoding.yaml as a VARIABLE-LENGTH PREFIX CODE (canonical Huffman, à la
zlib/DEFLATE) and print the layouts with the frame IDENTIFIER bits filled in as
constants (0/1), leaving only the op-SELECT bits ('o') that choose the specific
opcode from that frame's list.

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

For each frame the tool prints its bare form, then walks the frame's asm
templates and, for each, reprints the matching encoding row TWICE — once for the
A instruction and once for the B instruction — blanking the fields that slot
does not use, so it is visible which fields each slot owns and which they share.

Usage:  python3 util/encoding_assign.py
"""
import math
import os
import re
import sys

import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from encoding_render import (_center, _cell, _spanned, header_lines,
                             opcode_demand, opcode_codepoints, op_name,
                             row_operands, lint_frame, _OPERAND, _IMPLICIT)

WBITS = 10                      # opcode5(5)+funct3(3)+g(1)+h(1)
MARKER = "1 0"
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


def word_chars(frame):
    """The 10 selector bits MSB->LSB as display chars:
       '0'/'1' identifier, 'o' op-select, '.' free/unused."""
    idl, opsel = frame["id_len"], frame["opsel"]
    w = []
    for pos in range(WBITS - 1, -1, -1):          # bit 9 (MSB) .. 0
        depth = WBITS - pos                        # 1..10 from the MSB
        if depth <= idl:
            w.append(str((frame["id_val"] >> (idl - depth)) & 1))
        elif depth <= idl + opsel:
            w.append("o")
        else:
            w.append(".")
    return w


def frame_rows(spec):
    """(cells, tag) for every row, dict-form or bare-list."""
    out = []
    for r in spec["rows"]:
        if isinstance(r, dict):
            out.append((list(r["c"]), r.get("tag")))
        else:
            out.append((list(r), None))
    return out


def _tokens(cells, w):
    """Per operand-column cell: (display_text, span, pos, body) with the
    selector bits injected — fn3 as its 3 bits, a discrete g/h cell as its
    bit, everything else as its span-stripped label."""
    fn3 = " ".join(w[5:8])
    g_char, h_char = w[8], w[9]
    out, pos = [], 0
    for cell in cells:
        body, span = _cell(cell)
        if body == "fn3":
            text = fn3
        elif span == 1 and pos == COL_G and body == "g":
            text = g_char
        elif span == 1 and pos == COL_H and body == "h":
            text = h_char
        else:
            text = body
        out.append((text, span, pos, body))
        pos += span
    return out


_FIXED = re.compile(r"[01 ]+$")


def _shared_cell(body, pos):
    """A cell that belongs to the joint packet, not to one slot: the opcode
    bits (fn3), the g/h opcode bits, and any fixed bit pattern (incl. the
    prologue/epilogue/jump sentinel)."""
    if body == "fn3":
        return True
    if pos == COL_G and body == "g":
        return True
    if pos == COL_H and body == "h":
        return True
    return bool(_FIXED.match(body))


def render_line(tokens, o5, colwidths, keep=None):
    """Render one encoding line. `keep(base)` decides whether a slot-owned
    field is shown; when None every field shows (the plain form). Shared cells
    and the opcode5/marker tail always show; erased cells render blank."""
    rendered, pos = [], 0
    for text, span, cpos, body in tokens:
        width = _spanned(colwidths, pos, span)
        if keep is None or _shared_cell(body, cpos):
            show = True
        else:
            base = body.split("*")[0].split("[")[0]
            show = keep(base)
        rendered.append(_center(text if show else "", width))
        pos += span
    for token in [o5, MARKER]:                      # opcode5 + marker: shared
        text, span = _cell(token)
        rendered.append(_center(text, _spanned(colwidths, pos, span)))
        pos += span
    return "│" + "│".join(rendered) + "│"


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


def matches(row_cells, tag, a_ops, b_ops, sp_template, has_sp_rows):
    """A row realises a template when every field the row encodes is an operand
    of the template, and (for frames that distinguish them) its SP-relative
    variant agrees."""
    if not row_operands(row_cells) <= (a_ops | b_ops):
        return False
    if has_sp_rows and sp_template != (tag == "SP-relative"):
        return False
    return True


def render_frame_body(frame, colwidths, header):
    """Print the frame's plain form, then, per template, the encoding twice —
    once keeping only the A-slot's fields and once only the B-slot's — with the
    asm instruction on the right. Fields used by both slots survive both copies,
    exposing the shared operands."""
    spec = frame["spec"]
    w = word_chars(frame)
    o5 = " ".join(w[0:5])
    rows = frame_rows(spec)
    has_sp = any(tag == "SP-relative" for _, tag in rows)

    print("\n".join(header))
    for cells, tag in rows:                         # the form as it stands
        line = render_line(_tokens(cells, w), o5, colwidths)
        print(line + (f" ({tag})" if tag else ""))

    for pair in spec["templates"]:
        a_line, b_line = pair[0].strip(), pair[1].strip()
        a_ops, b_ops = line_ops(pair[0]), line_ops(pair[1])
        sp_t = any("(sp)" in ln for ln in pair)
        hits = [(c, t) for c, t in rows
                if matches(c, t, a_ops, b_ops, sp_t, has_sp)]
        approx = False
        if not hits:
            # Contorted frames (e.g. dual-mem) reuse one encoding row across
            # several asm forms, so no row's fields are a strict subset of this
            # template's operands. Fall back to the single best-overlap row.
            cand = [(c, t) for c, t in rows
                    if not (has_sp and sp_t != (t == "SP-relative"))]
            cand.sort(key=lambda ct: -len(row_operands(ct[0]) & (a_ops | b_ops)))
            if cand and row_operands(cand[0][0]) & (a_ops | b_ops):
                hits, approx = [cand[0]], True
        print()
        if not hits:
            print(f"    (no row realises: {a_line} ; {b_line})")
            continue
        if approx:
            print("    (closest-fit encoding — this frame shares rows across forms)")
        for cells, tag in hits:
            rops = row_operands(cells)
            toks = _tokens(cells, w)
            a = render_line(toks, o5, colwidths, keep=lambda base: base in a_ops)
            b = render_line(toks, o5, colwidths, keep=lambda base: base in b_ops)
            print(f"{a}   {specialize(a_line, rops)}")
            print(f"{b}   {specialize(b_line, rops)}")


def main():
    spec = yaml.safe_load(open(os.path.join(ROOT, "encoding.yaml")))
    frames = []
    for node in spec["doc"]:
        if "frame" not in node:
            continue
        f = node["frame"]
        if not f.get("ops"):
            continue
        base = opcode_demand(f.get("ops"))          # a×b combos, before ext
        d = opcode_codepoints(f, spec["grid"])      # real codepoints, ext-aware
        budget = f.get("budget") or d               # reserve current fill if none
        fmt = a_format(f)
        frames.append({
            "name": f["name"], "spec": f, "demand": d, "base": base,
            "budget": budget, "opsel": opsel_bits(budget),
            "fmt": fmt, "a_rank": _FMT_RANK[fmt],
        })

    complaints = []
    for f in frames:
        complaints += lint_frame(f["spec"], spec["grid"])

    order, reserved, W = allocate_blocks(frames)
    overflow = reserved > (1 << WBITS)
    widths = list(spec["grid"]["display"]) + [9, 3]
    header = header_lines(widths)

    if complaints:
        print("## Codepoint-accounting complaints\n")
        for c in complaints:
            print(f"  ✗ {c}")
        print()

    print("# Assigned opcode bit-patterns (variable-length prefix code)\n")
    print(f"Selector word = opcode5(5):funct3(3):g:h = {WBITS} bits, "
          f"{1<<WBITS} codepoints, read MSB->LSB.")
    print("'0'/'1' = frame identifier (constant), 'o' = op-select, "
          "'.' = free/unused.\n")
    print(f"Reserved {reserved}/{1<<WBITS} codepoints across {len(frames)} frames "
          f"({100*reserved/(1<<WBITS):.0f}%), each frame a fixed block sized to its\n"
          f"budget so it can grow into its own free slots without moving the rest.\n")
    print("Each frame prints its form, then per template the encoding twice — the\n"
          "A instruction then the B — with the fields that slot does NOT use erased.\n"
          "A field kept in both copies is shared by both instructions.\n")

    overbudget = []
    for f in order:
        idl, opsel, depth = f["id_len"], f["opsel"], f["depth"]
        block = 1 << opsel
        room = block - f["demand"]
        opc = _FMT_OPC[f["fmt"]]
        opc_s = f"opcode[6:2]≈{opc:05b}" if opc is not None else "opcode[6:2]=mixed"
        tag = ""
        if f["demand"] > block:
            tag = "   [⚠ current fill exceeds its block]"
            overbudget.append(f["name"])
        idbits = f"{f['id_val']:0{idl}b}" if idl else "(none)"
        print(f"## {f['name']}{tag}")
        print(f"    A-slot: {f['fmt']:7} ({opc_s})   "
              f"block {block} (budget {f['budget']}); using {f['demand']}, "
              f"{room} free to grow; identifier {idl} bit(s) = {idbits}; "
              f"depth {depth}/{WBITS}")
        print()
        render_frame_body(f, widths, header)
        print()

    print("─" * 72)
    if overflow:
        print(f"⚠ OVERFLOW: reserved {reserved} > {1<<WBITS} codepoints — the planned\n"
              f"  budgets do not fit the namespace. Shrink some budgets or op-lists.")
    else:
        print(f"Planned budgets FIT: {reserved}/{1<<WBITS} reserved, "
              f"{(1<<WBITS)-reserved} spare. Every frame can grow to its full budget\n"
              f"without overflowing the namespace or perturbing another frame.")
    if overbudget:
        print(f"\n⚠ Frames whose current fill already exceeds their block "
              f"(raise the budget): {', '.join(overbudget)}")
    return 1 if (overflow or overbudget) else 0


if __name__ == "__main__":
    sys.exit(main())

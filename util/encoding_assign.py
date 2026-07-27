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

Because the total demand is fixed but per-frame op-lists differ wildly (2 combos
for prologue, 256 for the 16x16 ALU frames), the identifier length is variable:
big-op frames get short identifiers, small-op frames long ones. This is exactly
the canonical prefix-code construction DEFLATE uses.

Two "nice-to-have" biases are applied when they don't cost feasibility:
  * frames are ORDERED for canonical assignment by their A-slot RISC-V format
    (load / OP-IMM / store / OP / branch / jump), so the leading identifier bits
    — which physically sit in opcode[6:2] — climb in the same order the real
    base ISA opcodes do (bit 5 clear ~ immediate/I-type, set ~ register/R-type;
    bit 6 ~ arithmetic vs control). A hardware A-slot decoder can therefore
    branch on the same bits it already uses.
  * when a frame's word stops before g/h, those freed bits are labelled as the
    A-slot (g) and B-slot (h) wide-immediate extension bits — the "raised for
    the immediate form" convention from encoding.yaml's Overview.

Usage:  python3 util/encoding_assign.py
"""
import math
import os
import re
import sys

import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from encoding_render import _center, _cell, _spanned, header_lines, opcode_demand

WBITS = 10                      # opcode5(5)+funct3(3)+g(1)+h(1)
MARKER = "1 0"
GH_FREE_DEPTH = 8               # word must stop at <= this depth to leave g & h free
H_FREE_DEPTH = 9                # ... to leave just h free

# Grid column indices (encoding.yaml grid.columns): the g bit is column 2, the
# h bit column 0. Used to spot an immediate parked in g/h.
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
    ops = frame.get("ops") or {}
    if "a" in ops:
        return set(ops["a"])
    if "same" in ops:
        return set(ops["same"])
    if "tuples" in ops:
        return {t[0] for t in ops["tuples"]}
    return set()


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


# --- immediate demand on g / h (nice-to-have #2) ---------------------------
def wants_gh(frame):
    """(wants_g, wants_h): does the frame try to carry a wide immediate in the
    g (A-slot) / h (B-slot) bit? True when a row parks an immediate token in
    that column, or the notes say g/h extend/provide an immediate."""
    wg = wh = False
    for row in frame["rows"]:
        cells = row["c"] if isinstance(row, dict) else row
        pos = 0
        for cell in cells:
            body, span = _cell(cell)
            name = body.split("[")[0]
            if name.startswith("imm"):
                if pos <= COL_G < pos + span:
                    wg = True
                if pos <= COL_H < pos + span:
                    wh = True
            pos += span
    note = frame.get("notes", "") or ""
    if re.search(r"`g`[^.]*(extend|provide)|(extend|provide)[^.]*`g`", note):
        wg = True
    if re.search(r"`h`[^.]*(extend|provide)|(extend|provide)[^.]*`h`", note):
        wh = True
    return wg, wh


def opsel_bits(demand):
    return max(0, math.ceil(math.log2(demand))) if demand and demand > 1 else 0


# --- variable-length identifier lengths (Kraft-greedy) ---------------------
def choose_lengths(frames):
    """Pick each frame's identifier length. Baseline: the deepest word (t=10),
    i.e. id_len = 10 - opsel, which claims the fewest codepoints (2**opsel).
    Then spend the leftover namespace promoting immediate-hungry frames to a
    shallower word (t<=8 so g&h stay free), cheapest first, until the 1024
    codepoints run out. Frames that couldn't be promoted keep g/h as opcode
    bits — a reported conflict."""
    used = 0
    for f in frames:
        f["id_len"] = WBITS - f["opsel"]          # t = 10
        f["depth"] = WBITS
        f["promoted"] = False
        used += 1 << f["opsel"]                    # codepoints at t=10

    def target_depth(f):
        if f["wg"]:
            return GH_FREE_DEPTH                   # need g (and thus h) free
        if f["wh"]:
            return H_FREE_DEPTH
        return None

    wanters = []
    for f in frames:
        t = target_depth(f)
        if t is None:
            continue
        idl = t - f["opsel"]
        if idl < 1:                                # op-list too big to ever fit
            f["conflict_hard"] = True
            continue
        cost = (1 << (WBITS - idl)) - (1 << f["opsel"])   # extra codepoints
        wanters.append((cost, f["a_rank"], f["name"], f, idl, t))

    for cost, _rank, _name, f, idl, t in sorted(wanters):
        if used + cost <= (1 << WBITS):
            used += cost
            f["id_len"] = idl
            f["depth"] = t
            f["promoted"] = True
    return used


# --- canonical (DEFLATE) code assignment -----------------------------------
def assign_codes(frames):
    """Standard canonical prefix-code construction: codes are handed out by
    increasing length, and within a length in A-format order, so the leading
    identifier bits track the RISC-V opcode ordering."""
    order = sorted(frames, key=lambda f: (f["id_len"], f["a_rank"], f["name"]))
    bl_count = {}
    for f in order:
        bl_count[f["id_len"]] = bl_count.get(f["id_len"], 0) + 1
    next_code, code = {}, 0
    for bits in range(1, WBITS + 1):
        code = (code + bl_count.get(bits - 1, 0)) << 1
        next_code[bits] = code
    for f in order:
        c = next_code[f["id_len"]]
        next_code[f["id_len"]] += 1
        f["id_val"] = c
    return order


def word_chars(frame):
    """The 10 selector bits MSB->LSB as display chars:
       '0'/'1' identifier, 'o' op-select, then free bits labelled 'g'/'h'
       (wide-immediate extension) or '.' (unused)."""
    idl, opsel = frame["id_len"], frame["opsel"]
    w = []
    for pos in range(WBITS - 1, -1, -1):          # bit 9 (MSB) .. 0
        depth = WBITS - pos                        # 1..10 from the MSB
        if depth <= idl:
            w.append(str((frame["id_val"] >> (idl - depth)) & 1))
        elif depth <= idl + opsel:
            w.append("o")
        else:
            # a free bit toward g/h
            if pos == 1 and frame["wg"]:
                w.append("g")
            elif pos == 0 and frame["wh"]:
                w.append("h")
            else:
                w.append(".")
    return w


def first_row(spec):
    r = spec["rows"][0]
    if isinstance(r, dict):
        return list(r["c"]), r.get("tag")
    return list(r), None


def render_frame_row(frame, colwidths):
    """Render the frame's first row with the selector bits injected: fn3 and
    opcode5 always shown as bits; a discrete g/h/i opcode cell overwritten with
    its bit; spanned-immediate cells left as-is (the ⚠ report covers clashes)."""
    w = word_chars(frame)
    o5 = " ".join(w[0:5])
    fn3 = " ".join(w[5:8])
    g_char, h_char = w[8], w[9]
    cells, tag = first_row(frame["spec"])

    disp, pos = [], 0
    for cell in cells:
        body, span = _cell(cell)
        if span == 1 and body == "fn3":
            disp.append(fn3)
        elif span == 1 and pos == COL_G and body in ("g", "i"):
            disp.append(g_char)
        elif span == 1 and pos == COL_H and body == "h":
            disp.append(h_char)
        else:
            disp.append(cell)
        pos += span

    rendered, pos = [], 0
    for token in disp + [o5, MARKER]:
        text, span = _cell(token)
        rendered.append(_center(text, _spanned(colwidths, pos, span)))
        pos += span
    line = "│" + "│".join(rendered) + "│"
    if tag:
        line += f" ({tag})"
    return line


def main():
    spec = yaml.safe_load(open(os.path.join(ROOT, "encoding.yaml")))
    frames = []
    for node in spec["doc"]:
        if "frame" not in node:
            continue
        f = node["frame"]
        d = opcode_demand(f.get("ops"))
        if not d:
            continue
        wg, wh = wants_gh(f)
        fmt = a_format(f)
        frames.append({
            "name": f["name"], "spec": f, "demand": d, "opsel": opsel_bits(d),
            "wg": wg, "wh": wh, "fmt": fmt, "a_rank": _FMT_RANK[fmt],
            "conflict_hard": False,
        })

    used = choose_lengths(frames)
    order = assign_codes(frames)
    widths = list(spec["grid"]["display"]) + [9, 3]
    header = header_lines(widths)

    print("# Assigned opcode bit-patterns (variable-length prefix code)\n")
    print(f"Selector word = opcode5(5):funct3(3):g:h = {WBITS} bits, "
          f"{1<<WBITS} codepoints, read MSB->LSB.")
    print("'0'/'1' = frame identifier (constant), 'o' = op-select, "
          "'g'/'h' = free wide-immediate bit (A/B slot), '.' = free/unused.\n")
    print(f"Namespace used: {used}/{1<<WBITS} codepoints "
          f"({100*used/(1<<WBITS):.0f}%).  "
          f"{sum(f['promoted'] for f in frames)} frame(s) promoted to keep g/h free.\n")

    conflicts, freed = [], []
    for f in order:
        fr = f["spec"]
        idl, opsel, depth = f["id_len"], f["opsel"], f["depth"]
        gh_used = depth > GH_FREE_DEPTH
        clash = (f["wg"] or f["wh"]) and gh_used
        opc = _FMT_OPC[f["fmt"]]
        opc_s = f"opcode[6:2]≈{opc:05b}" if opc is not None else "opcode[6:2]=mixed"
        tags = []
        if f["promoted"]:
            tags.append("g/h FREE for immediate")
        if clash:
            tags.append("⚠ wants g/h but word consumes them")
            conflicts.append(f["name"])
        if f.get("conflict_hard"):
            tags.append("⚠ op-list too large to ever free g/h")
        if f["promoted"] and not clash:
            freed.append(f["name"])
        tagstr = ("   [" + "; ".join(tags) + "]") if tags else ""

        print(f"## {f['name']}{tagstr}")
        print(f"    A-slot: {f['fmt']:7} ({opc_s})   "
              f"{f['demand']} combos → {opsel} select bit(s); "
              f"identifier {idl} bit(s) = {f['id_val']:0{idl}b}; "
              f"total word depth {depth}/{WBITS}")
        print()
        print("\n".join(header))
        print(render_frame_row(f, widths))
        print()

    print("─" * 72)
    print(f"g/h kept free for immediates ({len(freed)}): "
          f"{', '.join(freed) if freed else 'none'}")
    print(f"\ng/h CLASHES ({len(conflicts)}) — frame wants g/h for a wide "
          f"immediate but its opcode word occupies them:")
    for c in conflicts:
        print(f"    {c}")
    print("\nThe two 16×16 ALU frames (chain-alu-pair, rsd-alu-pair) alone claim\n"
          "512 of the 1024 codepoints, which is what pushes the wide-immediate\n"
          "frames down to depth 10. Shrinking those op-lists (they are the\n"
          "over-committers flagged by --opcodes) frees the budget to promote the\n"
          "clashing frames above g/h.")


if __name__ == "__main__":
    main()

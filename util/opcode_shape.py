#!/usr/bin/env python3
"""
util/opcode_shape.py -- partition the packet's opcode5 field by the A slot's
real operand shape (does the upcoming op read rs1? rs2? draw an immediate?),
the same way real RISC-V's opcode[6:2] already determines instruction FORMAT
(R/I/S/B/U/J) -- and hence which operand ports to activate -- before funct3
picks the specific operation. rd is out of scope (see CLAUDE.md discussion);
this is register-source and immediate readiness only.

Every op's shape is resolved DYNAMICALLY from encoding.yaml itself:
pseudo_ops' base:/encode: pins (a register pinned to a constant, or aliased
to another operand, or an immediate pinned to a literal) and xlen_switchable's
real mnemonic are both read live, so nothing here is a per-op or per-frame
hand-maintained list that could drift when encoding.yaml changes. The one
thing that IS hardcoded is _BASE_FORMAT: genuine base-ISA RV32I/RV64I/M/Zba/
Zicond operand shape, which is external, stable ISA knowledge encoding.yaml
doesn't (and shouldn't have to) restate. Adding a genuinely new base mnemonic
to the corpus means adding one line here; everything else -- new pseudo_ops
entries, new frames, new op combinations -- is picked up automatically on the
next run.

This tool POOLS every frame's A-side table entries by that shape (ignoring
which frame they came from) and REFORMS the opcode5 allocation around the
pooled shapes, using the exact same buddy-allocation discipline as
encoding_assign.allocate_blocks() -- called twice: once to fit the six shapes
into opcode5's 32 values, once more inside each shape to fit that shape's
constituent frames into its allotted capacity. Every entry keeps an explicit
origin tag (frame name, cluster index) throughout, so nothing downstream that
identifies things by frame name is left with nowhere to look.

This is analysis/validation tooling: it does not change ciscv-proto.yml or
any existing output. Rerun after editing encoding.yaml to see the updated
partition, or run --check to fail loudly if something no longer fits.

Usage:
    python3 util/opcode_shape.py            # the partition report
    python3 util/opcode_shape.py --check    # exit nonzero on overflow/unknown op
"""
import argparse
import math
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "util"))

import encoding_assign as ea

OPCODE5_BITS = 5
OPCODE5_VALUES = 1 << OPCODE5_BITS                        # 32
CAP_PER_VALUE = 1 << (ea.WBITS - OPCODE5_BITS)             # 32 (funct3+g+h)

# --- base-ISA operand format (the only hardcoded knowledge here) -----------
# (needs_rs1, needs_rs2, needs_imm), independent of rd. Everything else in
# the corpus resolves down to one of these through encoding.yaml's own
# pseudo_ops/xlen_switchable registries.
_BASE_RR = {                                    # R-type: rs1 + rs2
    "add", "addw", "and", "or", "xor", "sub", "sltu", "maxu",
    "mul", "mulh", "mulhu", "mulhsu",
    "div", "divu", "divw", "divuw", "rem", "remu", "remuw", "remw",
    "sh1add", "sh2add", "sh3add", "czero.eqz", "czero.nez",
}
_BASE_RI = {                                    # I-type: rs1 + imm (ALU-imm and load are the same shape)
    "addi", "addiw", "andi", "slli", "slliw", "srli", "srliw", "sltiu", "xori",
    "lb", "lbu", "lh", "lhu", "lw", "lwu", "ld",
    "jalr",
}
_BASE_RRI = {                                   # S/B-type: rs1 + rs2 + imm
    "sb", "sh", "sw", "sd",
    "beq", "bne", "blt", "bge", "bltu", "bgeu", "bltu_r", "bgeu_r", "bge_r",
}
_BASE_FORMAT = {n: (True, True, False) for n in _BASE_RR}
_BASE_FORMAT.update({n: (True, False, True) for n in _BASE_RI})
_BASE_FORMAT.update({n: (True, True, True) for n in _BASE_RRI})
_BASE_FORMAT["jal"] = (False, False, True)      # UJ-type: imm only

SHAPE_LABELS = {
    (True, False, True): "RI",
    (True, True, False): "RR",
    (True, True, True): "RRI",
    (False, False, True): "IMM",
    (True, False, False): "R1",
    (False, True, False): "R2",
    (False, False, False): "NONE",
}


def resolve_shape(name, pseudo_ops, xlen_switchable, _depth=0):
    """(needs_rs1, needs_rs2, needs_imm) for `name`, resolved through any
    pseudo_ops/xlen_switchable indirection down to base-ISA format. A
    register pinned to a constant (encode: {rs1: x0}) or an immediate pinned
    to a literal (encode: {imm: 1}) clears that operand's live-read
    requirement; an operand ALIASED to another (encode: {rd: rs1}) does not
    -- rs1 is still a real, live read, just the same register rd writes."""
    if _depth > 10:
        raise ValueError(f"pseudo_ops resolution too deep at {name!r} "
                          f"(possible base:/encode: cycle)")
    if name in xlen_switchable:
        variant = next(iter(xlen_switchable[name].values()))
        return resolve_shape(variant, pseudo_ops, xlen_switchable, _depth + 1)
    if name in pseudo_ops:
        entry = pseudo_ops[name]
        encode = entry.get("encode") or {}
        real_op = encode.get("op", entry["base"])
        r1, r2, im = resolve_shape(real_op, pseudo_ops, xlen_switchable, _depth + 1)
        if "rs1" in encode:
            r1 = False
        if "rs2" in encode:
            r2 = False
        if "imm" in encode and isinstance(encode["imm"], (int, float)):
            im = False
        return r1, r2, im
    if name not in _BASE_FORMAT:
        raise ValueError(f"unknown base mnemonic {name!r} -- add it to "
                          f"_BASE_RR/_BASE_RI/_BASE_RRI in {__file__}")
    return _BASE_FORMAT[name]


def a_shape(op_name, pseudo_ops, xlen_switchable):
    return SHAPE_LABELS[resolve_shape(op_name, pseudo_ops, xlen_switchable)]


# --- catalog: every A-side table entry, with origin ------------------------
def catalog(frames, pseudo_ops, xlen_switchable):
    """[{frame, cluster, op, n, shape}] for every A-side entry actually
    reachable, weighted by real codepoint coverage (n_a * n_b). `frame` and
    `cluster` are the origin -- nothing here discards which frame an entry
    came from."""
    entries = []
    for f in frames:
        if f.get("host"):
            continue
        for ci, c in enumerate(f["tables"]):
            if c["diagonal"]:
                for p in c["pairs"]:
                    shape = a_shape(p["a"]["op"], pseudo_ops, xlen_switchable)
                    entries.append({"frame": f["name"], "cluster": ci,
                                     "op": p["a"]["op"], "n": p["n"], "shape": shape})
            else:
                b_total = sum(eb["n"] for eb in c["b"])
                for ea_ in c["a"]:
                    shape = a_shape(ea_["op"], pseudo_ops, xlen_switchable)
                    entries.append({"frame": f["name"], "cluster": ci,
                                     "op": ea_["op"], "n": ea_["n"] * b_total,
                                     "shape": shape})
    return entries


# --- partition: buddy-allocate opcode5 across shapes, then frames within ---
def _pseudo_frame(name, opsel, a_rank=0):
    return {"name": name, "opsel": opsel, "a_rank": a_rank}


def partition(entries):
    """(shape_order, frame_order_by_shape) -- opcode5 allocated across shapes
    by real weight, then, within each shape's block, the constituent frames
    allocated within its capacity. Both levels reuse
    encoding_assign.allocate_blocks() unchanged -- this is the SAME
    buddy-allocation discipline the existing frame/cluster allocation uses,
    applied one level up and then nested one level down."""
    by_shape = {}
    for e in entries:
        by_shape.setdefault(e["shape"], 0)
        by_shape[e["shape"]] += e["n"]

    shape_frames = []
    for shape, w in by_shape.items():
        need_values = max(1, math.ceil(w / CAP_PER_VALUE))
        shape_frames.append(_pseudo_frame(shape, ea.opsel_bits(need_values)))
    shape_order, shape_reserved, shape_W = ea.allocate_blocks(shape_frames)
    overflow = shape_reserved > OPCODE5_VALUES

    frame_order_by_shape = {}
    for sf in shape_order:
        shape = sf["name"]
        by_frame = {}
        for e in entries:
            if e["shape"] != shape:
                continue
            by_frame.setdefault(e["frame"], 0)
            by_frame[e["frame"]] += e["n"]
        members = [_pseudo_frame(name, ea.opsel_bits(w)) for name, w in by_frame.items()]
        capacity = (1 << sf["opsel"]) * CAP_PER_VALUE
        order, reserved, W = ea.allocate_blocks(members)
        sf["_frame_weight"] = by_frame
        sf["_frame_order"] = order
        sf["_frame_reserved"] = reserved
        sf["_capacity"] = capacity
        sf["_overflow"] = reserved > capacity
        frame_order_by_shape[shape] = order

    return shape_order, shape_reserved, shape_W, overflow, frame_order_by_shape


# --- report ------------------------------------------------------------
def report(frames, info):
    pseudo_ops, xlen_switchable = info["pseudo_ops"], info["opsets"].get("xlen_switchable") or {}
    entries = catalog(frames, pseudo_ops, xlen_switchable)
    total = sum(e["n"] for e in entries)
    shape_order, shape_reserved, shape_W, overflow, frame_order_by_shape = partition(entries)

    print(f"Slot A operand shape -- rs1/rs2/imm readiness, opcode5-partitioned\n")
    print(f"{total} codepoints catalogued across "
          f"{len({e['frame'] for e in entries})} frames.\n")
    print(f"opcode5 values used: {shape_reserved}/{OPCODE5_VALUES}"
          + ("  *** OVERFLOW ***" if overflow else "") + "\n")

    for sf in shape_order:
        block = 1 << sf["opsel"]
        idl = sf["id_len"]
        prefix = format(sf["id_val"], f"0{idl}b") if idl else ""
        w = sum(e["n"] for e in entries if e["shape"] == sf["name"])
        tag = "  *** OVERFLOW ***" if sf["_overflow"] else ""
        print(f"## {sf['name']:6} opcode5={prefix}{'x' * (5 - idl)}  "
              f"block={block:>2}/32 values ({block * CAP_PER_VALUE} codepoints, "
              f"{sf['_capacity'] - sf['_frame_reserved']} spare)  "
              f"content={w}{tag}")
        for mf in sf["_frame_order"]:
            fidl = mf["id_len"]
            fprefix = format(mf["id_val"], f"0{fidl}b") if fidl else ""
            fw = sf["_frame_weight"][mf["name"]]
            print(f"      {mf['name']:24} weight={fw:>4}  "
                  f"sub-id={fprefix or '(none)'}")
        print()

    return overflow or any(sf["_overflow"] for sf in shape_order)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--yaml", default=os.path.join(ROOT, "encoding.yaml"))
    ap.add_argument("--check", action="store_true",
                     help="exit nonzero on overflow or an unclassifiable op; print nothing on success")
    args = ap.parse_args()

    frames, info = ea.load(args.yaml)
    try:
        if args.check:
            pseudo_ops = info["pseudo_ops"]
            xlen_switchable = info["opsets"].get("xlen_switchable") or {}
            entries = catalog(frames, pseudo_ops, xlen_switchable)
            shape_order, _, _, overflow, _ = partition(entries)
            bad = overflow or any(sf["_overflow"] for sf in shape_order)
            return 1 if bad else 0
        else:
            return 1 if report(frames, info) else 0
    except ValueError as e:
        print(f"  ✗ {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())

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


def bin_pack(remainders, cap):
    """Minimum number of cap-sized bins needed to pack these leftover amounts
    together -- exact via full search (the item counts here are always tiny,
    at most a handful of B-shapes per A-shape). This is what makes a funct3
    code shared/asymmetric: two B-shapes' leftovers, each too small alone to
    justify a whole code, packed into ONE code together when their sum still
    fits its capacity, rather than each rounding up to its own full code."""
    best = len(remainders)
    def search(items, bins):
        nonlocal best
        if not items:
            best = min(best, len(bins))
            return
        if len(bins) >= best:
            return
        item, rest = items[0], items[1:]
        for i, b in enumerate(bins):
            if b + item <= cap:
                search(rest, bins[:i] + [b + item] + bins[i + 1:])
        search(rest, bins + [item])
    search(sorted((r for r in remainders if r), reverse=True), [])
    return best

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
    # Single-operand and no-operand modes fold into RI: an implementation
    # can just ignore the argument it doesn't need. Preferring an unused
    # immediate over an unused register (rather than folding into RR) keeps
    # the folded-away slot free of any real register-file read -- an unused
    # immediate is just a wire nobody looks at, but an unused register slot
    # is still a register index someone has to decide not to act on.
    (False, False, True): "RI",   # imm-only (li, addi4spn): rs1 unused
    (True, False, False): "RI",   # rs1-only (inc, dec): imm unused
    (False, True, False): "RI",   # rs2-only (mv): imm unused
    (False, False, False): "RI",  # no operands at all (ret): both unused
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


def catalog_b(frames, pseudo_ops, xlen_switchable):
    """[{frame, cluster, op, n, shape}] for every B-side entry, mirroring
    catalog() exactly but for B -- its own shape, independent of which A op
    it happens to be paired with."""
    entries = []
    for f in frames:
        if f.get("host"):
            continue
        for ci, c in enumerate(f["tables"]):
            if c["diagonal"]:
                for p in c["pairs"]:
                    shape = a_shape(p["b"]["op"], pseudo_ops, xlen_switchable)
                    entries.append({"frame": f["name"], "cluster": ci,
                                     "op": p["b"]["op"], "n": p["n"], "shape": shape})
            else:
                a_total = sum(ea_["n"] for ea_ in c["a"])
                for eb in c["b"]:
                    shape = a_shape(eb["op"], pseudo_ops, xlen_switchable)
                    entries.append({"frame": f["name"], "cluster": ci,
                                     "op": eb["op"], "n": eb["n"] * a_total,
                                     "shape": shape})
    return entries


def catalog_pairs(frames, pseudo_ops, xlen_switchable):
    """[{frame, cluster, a_op, b_op, n, a_shape, b_shape}] for every reachable
    (opA, opB) combination, weighted by real codepoint coverage -- the joint
    catalog catalog()/catalog_b() each aggregate away one side of. This is
    what lets B's shape be measured against A's, not just on its own."""
    entries = []
    for f in frames:
        if f.get("host"):
            continue
        for ci, c in enumerate(f["tables"]):
            if c["diagonal"]:
                for p in c["pairs"]:
                    entries.append({
                        "frame": f["name"], "cluster": ci,
                        "a_op": p["a"]["op"], "b_op": p["b"]["op"], "n": p["n"],
                        "a_shape": a_shape(p["a"]["op"], pseudo_ops, xlen_switchable),
                        "b_shape": a_shape(p["b"]["op"], pseudo_ops, xlen_switchable),
                    })
            else:
                for ea_ in c["a"]:
                    for eb in c["b"]:
                        entries.append({
                            "frame": f["name"], "cluster": ci,
                            "a_op": ea_["op"], "b_op": eb["op"],
                            "n": ea_["n"] * eb["n"],
                            "a_shape": a_shape(ea_["op"], pseudo_ops, xlen_switchable),
                            "b_shape": a_shape(eb["op"], pseudo_ops, xlen_switchable),
                        })
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
        # `reserved` (buddy-rounded, one power-of-2 block per frame) is what a
        # canonical-prefix decoder would need; it's not what a flat lookup/mux
        # needs. Nothing outside this A-shape's own block competes for these
        # bits, so there's no reason each frame's slice has to be its own
        # power-of-2-aligned region -- a mux just routes however many raw
        # index values a frame's content needs to its input, repeated as
        # necessary, boundaries anywhere. The real constraint is the SUM of
        # real content fitting the capacity, not the sum of rounded blocks.
        exact = sum(by_frame.values())
        sf["_frame_weight"] = by_frame
        sf["_frame_order"] = order
        sf["_frame_reserved"] = reserved
        sf["_frame_exact"] = exact
        sf["_capacity"] = capacity
        sf["_overflow_buddy"] = reserved > capacity
        sf["_overflow"] = exact > capacity
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
        buddy_tag = " (buddy-rounded sub-alloc overflows too)" if sf["_overflow_buddy"] and not sf["_overflow"] else ""
        print(f"## {sf['name']:6} opcode5={prefix}{'x' * (5 - idl)}  "
              f"block={block:>2}/32 values ({block * CAP_PER_VALUE} codepoints, "
              f"{sf['_capacity'] - sf['_frame_exact']} spare exact / "
              f"{sf['_capacity'] - sf['_frame_reserved']} spare buddy-rounded)  "
              f"content={w}{tag}{buddy_tag}")
        for mf in sf["_frame_order"]:
            fidl = mf["id_len"]
            fprefix = format(mf["id_val"], f"0{fidl}b") if fidl else ""
            fw = sf["_frame_weight"][mf["name"]]
            print(f"      {mf['name']:24} weight={fw:>4}  "
                  f"sub-id={fprefix or '(none)'}")
        print()

    print_b_and_contention(frames, pseudo_ops, xlen_switchable, shape_order)
    return overflow or any(sf["_overflow"] for sf in shape_order)


def print_b_and_contention(frames, pseudo_ops, xlen_switchable, a_shape_order):
    """B's own shape distribution (same resolver, applied to B-side ops,
    independent of which A op it's paired with), then the actual contention
    figure: how many distinct B-shapes show up WITHIN each A-shape's already-
    claimed opcode5 partition, and whether pinning B's shape down fits in
    THAT A-shape's remaining opcode5 bits or has to spill into funct3 --
    which is what decides whether "where do I find B's shape" can be one
    uniform rule or has to vary by A-shape."""
    pairs = catalog_pairs(frames, pseudo_ops, xlen_switchable)
    total = sum(e["n"] for e in pairs)

    b_weight = {}
    for e in pairs:
        b_weight[e["b_shape"]] = b_weight.get(e["b_shape"], 0) + e["n"]
    print("Slot B operand shape, independent of A (marginal):\n")
    for shape, w in sorted(b_weight.items(), key=lambda kv: -kv[1]):
        print(f"  {shape:6} weight={w:>4}  ({100*w/total:.1f}%)")
    print()

    b_only = catalog_b(frames, pseudo_ops, xlen_switchable)
    b_shape_order, b_reserved, _, b_overflow, _ = partition(b_only)
    print(f"If B got first pick of its own 32-value field (a reference point,\n"
          f"not something that physically exists): {b_reserved}/{OPCODE5_VALUES} "
          f"values" + ("  *** OVERFLOW ***" if b_overflow else "") + "\n")

    a_weight, joint = {}, {}
    for e in pairs:
        a_weight[e["a_shape"]] = a_weight.get(e["a_shape"], 0) + e["n"]
        key = (e["a_shape"], e["b_shape"])
        joint[key] = joint.get(key, 0) + e["n"]

    print("Contention: funct3 is ALWAYS B's shape field, full stop -- opcode5's\n"
          "leftover bits and g/h are only for whatever a funct3 code's own\n"
          "content doesn't fit, not for B's shape itself. Modeled as a real\n"
          "2-stage mux: funct3 selects one of 8 codes, each code's own inner\n"
          "mux is uniformly (opcode5-leftover + g + h) wide. Each B-shape\n"
          "claims whole codes for its bulk; where two B-shapes both have a\n"
          "leftover too small alone to justify a full code, they SHARE one\n"
          "(an asymmetric/split code, marked *) rather than each rounding up\n"
          "-- the busiest B-shape's own full codes stay simple and uniform,\n"
          "only the small remainder has to be irregular:\n")
    a_idl = {sf["name"]: sf["id_len"] for sf in a_shape_order}
    for a_shp in sorted(a_weight, key=lambda s: -a_weight[s]):
        sub = {b: w for (a, b), w in joint.items() if a == a_shp}
        opcode5_left = OPCODE5_BITS - a_idl[a_shp]
        per_code_cap = 1 << (opcode5_left + 2)          # opcode5-leftover + g + h
        full = {b: w // per_code_cap for b, w in sub.items()}
        rem = {b: w % per_code_cap for b, w in sub.items()}
        baseline = sum(full.values())
        shared = bin_pack(list(rem.values()), per_code_cap)
        total_codes = baseline + shared
        fits = total_codes <= 8
        verdict = (f"fits ({8 - total_codes} funct3 code(s) spare)" if fits else
                   f"does NOT fit -- short by {total_codes - 8} funct3 code(s) "
                   f"even with sharing")
        print(f"  A={a_shp:6} weight={a_weight[a_shp]:>5}   per-funct3-code capacity="
              f"{per_code_cap}   {baseline} full + {shared} shared* = "
              f"{total_codes}/8 funct3 codes -> {verdict}")
        for b_shp, w in sorted(sub.items(), key=lambda kv: -kv[1]):
            note = f" + shares a code* ({rem[b_shp]} left over)" if rem[b_shp] else ""
            print(f"      B={b_shp:6} weight={w:>5}  {full[b_shp]} full code(s)"
                  f"{note}  ({100*w/a_weight[a_shp]:.1f}% of this A-shape)")
    print()


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

"""
analysis/alu_pair_cooccurrence.py — build the A-op x B-op co-occurrence table
for pairable arithmetic adjacencies, for feeding util/biclique_tiling.py.

Row = A-slot opcode, col = B-slot opcode.  A cell counts how often that opcode
combination occurs as a *structurally pairable* arithmetic adjacency in the
LIST-scheduled corpus — independent (distinct dests, no cross-feed) OR a
producer/consumer chain (B consumes A's result, dead after B).  The opcode
allowlist and the register/immediate range limits are deliberately removed so
the table measures pure opcode demand, not what the current rules already pass.

Usage:
    python3 -m analysis.alu_pair_cooccurrence file1.s [file2.s ...] \
            --out-csv table.csv --out-ops ops.json
then:
    python3 util/biclique_tiling.py --table table.csv --N 8
"""

import sys
import os
import json
import argparse
from collections import Counter

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from analysis.parser import parse_file
from analysis.liveness import compute_global_liveness, compute_local_liveness
from analysis.depgraph import build_dep_graph
from scheduler.reorder import schedule, ScheduleMode
from scheduler.pairing import stamp_slot_eligibility

_NON_ALU_PREFIX = ("f", "v", "amo", "lr.", "sc.", "c.")
_NON_ALU = {
    "jal", "jalr", "j", "ret", "call", "tail", "auipc", "lui", "ecall",
    "ebreak", "fence", "fence.i", "csrr", "pause", "unimp", "nop",
    "beqz", "bnez", "beq", "bne", "blt", "bge", "bltu", "bgeu",
    "bltz", "bgez", "blez", "bgtz",
}


def is_alu(i):
    """A GPR-writing integer ALU op (no memory/branch/jump/fp/vector/system)."""
    m = i.mnemonic
    if i.rd is None or i.rd == 0:
        return False
    if i.reads_memory or i.writes_memory or i.has_mem_operand:
        return False
    if i.is_unknown or m in _NON_ALU:
        return False
    if any(m.startswith(p) for p in _NON_ALU_PREFIX):
        return False
    return True


def pairable(a, b):
    """Structurally packable as a pair: independent, or a dead-after chain."""
    if a.rd == b.rd:
        indep = False
    else:
        indep = (a.rd not in b.uses_regs) and (b.rd not in a.uses_regs)
    chain = (a.rd is not None and (b.rs1 == a.rd or b.rs2 == a.rd)
             and (b.rd == a.rd or a.rd not in b.live_out))
    return indep or chain


def build(paths):
    co = Counter()
    for path in paths:
        _b, fns = parse_file(open(path).read())
        for fn in fns:
            for bl in fn.blocks:
                stamp_slot_eligibility(bl.instructions)
            gl = compute_global_liveness(fn.blocks)
            for bl in fn.blocks:
                if not bl.instructions:
                    continue
                compute_local_liveness(bl, gl)
                g = build_dep_graph(bl)
                bl.instructions = schedule(bl, g, ScheduleMode.LIST)
                compute_local_liveness(bl, gl)
                ins = bl.instructions
                for k in range(len(ins) - 1):
                    a, b = ins[k], ins[k + 1]
                    if is_alu(a) and is_alu(b) and pairable(a, b):
                        co[(a.mnemonic, b.mnemonic)] += 1
    return co


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("files", nargs="+")
    ap.add_argument("--out-csv", default="alu_pairs.csv")
    ap.add_argument("--out-ops", default="alu_ops.json")
    args = ap.parse_args()

    co = build(args.files)
    ops = sorted(set([a for a, _ in co] + [b for _, b in co]))
    idx = {m: k for k, m in enumerate(ops)}
    V = len(ops)
    rows = [[0] * V for _ in range(V)]
    for (a, b), n in co.items():
        rows[idx[a]][idx[b]] = n

    with open(args.out_csv, "w") as f:
        for r in rows:
            f.write(",".join(str(x) for x in r) + "\n")
    json.dump(ops, open(args.out_ops, "w"))

    total = sum(co.values())
    print(f"V={V} ops, total pair mass={total}, wrote {args.out_csv} / {args.out_ops}")
    for (a, b), n in co.most_common(15):
        print(f"  {n:>6}  {a} -> {b}")


if __name__ == "__main__":
    main()

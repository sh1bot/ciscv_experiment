"""
analysis/alu_pair_cooccurrence.py — build A-op x B-op co-occurrence tables
for pairable arithmetic adjacencies, for feeding util/biclique_tiling.py.

Three analysis variants are produced:

1. ALL pairable pairs — independent (distinct dests, no cross-feed) OR
   producer/consumer chain (B consumes A's rd, dead after B).
   addi is split into subforms; immediate constraints applied:
     mv         (addi, imm==0, rs1!=0)  — no immediate constraint
     li         (addi, rs1==0)          — 10-bit immediate
     addi4spn   (addi, rs1==sp, rd in x8-x15) — 10-bit unsigned immediate
     addi_rsd   (addi, rd==rs1, else)  — 5-bit signed immediate
     addi_other (addi, rd!=rs1)        — 5-bit signed immediate

2. CHAIN pairs only — B consumes A's rd, A's rd dead after B.
   Free register and immediate choice (no filtering).
   addi subforms still separated.

3. INDEPENDENT pairs with RSD input operands — rd==rs1 for both A and B.
   li and addi4spn are included but restricted to 5-bit unsigned immediate.
   mv is included (no immediate constraint).

Usage:
    python3 -m analysis.alu_pair_cooccurrence file1.s [file2.s ...] \\
            --out-dir results/
then:
    python3 util/biclique_tiling.py --table results/all_pairs.csv --N 8
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

_SP = 2  # x2

_ADDI4SPN_DEST = frozenset(range(8, 16))  # x8-x15 (s0-a5)


def _fits_signed(v, bits):
    """True if v fits in a signed `bits`-bit immediate."""
    if v is None:
        return True
    lo = -(1 << (bits - 1))
    hi = (1 << (bits - 1)) - 1
    return lo <= v <= hi


def _fits_unsigned(v, bits):
    """True if v fits in an unsigned `bits`-bit immediate."""
    if v is None:
        return True
    return 0 <= v < (1 << bits)


def addi_subform(i):
    """
    Return the addi subform key for instruction i, or None if not addi.
    Keys: 'mv', 'li', 'addi4spn', 'addi_rsd', 'addi_other'
    """
    if i.mnemonic != "addi":
        return None
    imm = i.imm if i.imm is not None else 0
    rd, rs1 = i.rd, i.rs1
    if rs1 == 0:
        return "li"
    if imm == 0:
        return "mv"
    if rs1 == _SP and rd in _ADDI4SPN_DEST:
        return "addi4spn"
    if rd == rs1:
        return "addi_rsd"
    return "addi_other"


def opcode_key(i):
    """Canonical opcode key, splitting addi into subforms."""
    sf = addi_subform(i)
    if sf is not None:
        return sf
    return i.mnemonic


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


def imm_ok_default(i):
    """Immediate constraint for the default (all-pairs) analysis."""
    sf = addi_subform(i)
    if sf is None:
        return True  # non-addi: no constraint
    imm = i.imm if i.imm is not None else 0
    if sf == "mv":
        return True
    if sf in ("li", "addi4spn"):
        return _fits_signed(imm, 10)
    # addi_rsd, addi_other: 5-bit signed
    return _fits_signed(imm, 5)


def imm_ok_indep_rsd(i):
    """
    Immediate constraint for the independent-RSD analysis.
    li and addi4spn restricted to 5-bit unsigned; mv free; others 5-bit signed.
    """
    sf = addi_subform(i)
    if sf is None:
        return True
    imm = i.imm if i.imm is not None else 0
    if sf == "mv":
        return True
    if sf in ("li", "addi4spn"):
        return _fits_unsigned(imm, 5)
    return _fits_signed(imm, 5)


def is_rsd(i):
    """True if this instruction reads rd as a source (rd == rs1)."""
    return i.rd is not None and i.rs1 is not None and i.rd == i.rs1


def is_independent(a, b):
    """True if a and b have distinct dests with no cross-dependency."""
    if a.rd == b.rd:
        return False
    return (a.rd not in b.uses_regs) and (b.rd not in a.uses_regs)


def is_chain(a, b):
    """True if B consumes A's rd and A's rd is dead after B."""
    if a.rd is None:
        return False
    if b.rs1 != a.rd and b.rs2 != a.rd:
        return False
    return b.rd == a.rd or a.rd not in b.live_out


def build_tables(paths):
    """
    Returns three Counters: (co_all, co_chain, co_indep_rsd).
    Keys are (opcode_key(a), opcode_key(b)) pairs.
    """
    co_all = Counter()
    co_chain = Counter()
    co_indep_rsd = Counter()

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
                    if not (is_alu(a) and is_alu(b)):
                        continue
                    ka, kb = opcode_key(a), opcode_key(b)

                    chain = is_chain(a, b)
                    indep = is_independent(a, b)

                    # --- all-pairs analysis (imm constraints, both independent and chain)
                    if (chain or indep) and imm_ok_default(a) and imm_ok_default(b):
                        co_all[(ka, kb)] += 1

                    # --- chain-only, free registers and immediates
                    if chain:
                        co_chain[(ka, kb)] += 1

                    # --- independent RSD pairs
                    if indep and is_rsd(a) and is_rsd(b) and imm_ok_indep_rsd(a) and imm_ok_indep_rsd(b):
                        co_indep_rsd[(ka, kb)] += 1

    return co_all, co_chain, co_indep_rsd


def write_table(co, path_csv, path_ops):
    ops = sorted(set([a for a, _ in co] + [b for _, b in co]))
    idx = {m: k for k, m in enumerate(ops)}
    V = len(ops)
    rows = [[0] * V for _ in range(V)]
    for (a, b), n in co.items():
        rows[idx[a]][idx[b]] = n
    with open(path_csv, "w") as f:
        for r in rows:
            f.write(",".join(str(x) for x in r) + "\n")
    json.dump(ops, open(path_ops, "w"))
    return ops


def print_summary(name, co, top=15):
    total = sum(co.values())
    V = len(set([a for a, _ in co] + [b for _, b in co]))
    print(f"\n=== {name} ===")
    print(f"V={V} ops, total pair mass={total}")
    for (a, b), n in co.most_common(top):
        print(f"  {n:>6}  {a} -> {b}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("files", nargs="+")
    ap.add_argument("--out-dir", default=".")
    args = ap.parse_args()

    d = args.out_dir
    os.makedirs(d, exist_ok=True)

    co_all, co_chain, co_indep_rsd = build_tables(args.files)

    ops_all = write_table(co_all,
                          os.path.join(d, "all_pairs.csv"),
                          os.path.join(d, "all_pairs_ops.json"))
    ops_chain = write_table(co_chain,
                            os.path.join(d, "chain_pairs.csv"),
                            os.path.join(d, "chain_pairs_ops.json"))
    ops_rsd = write_table(co_indep_rsd,
                          os.path.join(d, "indep_rsd_pairs.csv"),
                          os.path.join(d, "indep_rsd_pairs_ops.json"))

    print_summary("ALL pairable pairs (addi subforms, imm limits)", co_all)
    print_summary("CHAIN pairs (free regs/imm)", co_chain)
    print_summary("INDEPENDENT RSD pairs (5-bit imm for li/addi4spn)", co_indep_rsd)

    print(f"\nWrote CSVs to {d}/")
    print(f"  all_pairs.csv ({len(ops_all)} ops)")
    print(f"  chain_pairs.csv ({len(ops_chain)} ops)")
    print(f"  indep_rsd_pairs.csv ({len(ops_rsd)} ops)")


if __name__ == "__main__":
    main()

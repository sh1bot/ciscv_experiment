"""
analysis/load_load_chain_stats.py — opportunity stats for a hypothetical
generalised load-load-chain rule with no register/immediate/size constraints.

Pattern:
    A:  load rtmp, immA(rb)
    B:  load rd,   immB(rtmp)       # rtmp dead after B

Both immA and immB are unconstrained (any sign, any width); rb/rtmp/rd are
unconstrained; the data size of either load is unconstrained.  This generalises
deref-chain (immB==0) and base-chain (immA==0) into one rule and measures how
many candidate pairs the corpus actually contains, plus the distributions of:

  * immA and immB by sign, and the number of *signed* bits each needs
  * the data size (access width) of the second load

Candidate definition (per scheduled block, semantic — independent of whether
the current scheduler happened to place them adjacently):
    - A and B are both loads (reads_memory)
    - B.rs1 == A.rd        (B dereferences A's loaded pointer)
    - A.rd is not None and != 0
    - A.rd is dead after B  (B.rd == A.rd, OR A.rd not in B.live_out)
    - between A and B no other instruction writes A.rd and no instruction other
      than B reads A.rd, and A.rd is not reused as a base by anything else
      (clean single-use chain)
    - A.rd != A.rs1 is not required, but A must not be clobbered before B reads
      it (guaranteed by the no-intervening-write rule)
"""

import sys
import os
from collections import Counter, defaultdict

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from analysis.parser import parse_file
from analysis.liveness import compute_global_liveness, compute_local_liveness
from analysis.depgraph import build_dep_graph
from scheduler.reorder import schedule, ScheduleMode
from scheduler.pairing import stamp_slot_eligibility


def _signed_bits(v):
    """Number of bits needed to represent v as a two's-complement signed value.

    0 -> 1, -1 -> 1, 1 -> 2, -64..63 -> 7, etc.
    """
    if v == 0:
        return 1
    if v > 0:
        return v.bit_length() + 1          # sign bit on top of magnitude
    return (-v - 1).bit_length() + 1       # -v-1 is the magnitude of ~v


def find_candidates(path, mode=ScheduleMode.LIST):
    with open(path) as f:
        source = f.read()
    _blocks, functions = parse_file(source)

    candidates = []  # (a, b)
    for fn in functions:
        for block in fn.blocks:
            stamp_slot_eligibility(block.instructions)
        global_result = compute_global_liveness(fn.blocks)
        for block in fn.blocks:
            if not block.instructions:
                continue
            compute_local_liveness(block, global_result)
            if mode != ScheduleMode.FORWARD:
                graph = build_dep_graph(block)
                block.instructions = schedule(block, graph, mode)
                compute_local_liveness(block, global_result)
            insns = block.instructions
            n = len(insns)
            for i, a in enumerate(insns):
                if not a.reads_memory:
                    continue
                if a.rd is None or a.rd == 0:
                    continue
                rtmp = a.rd
                # find the first later instruction that touches rtmp
                consumer = None
                clobbered = False
                for j in range(i + 1, n):
                    c = insns[j]
                    reads = rtmp in c.uses_regs
                    writes = (c.rd is not None and c.rd == rtmp)
                    if reads:
                        consumer = c
                        consumer_idx = j
                        break
                    if writes:
                        clobbered = True
                        break
                if consumer is None or clobbered:
                    continue
                b = consumer
                # B must be a load dereferencing rtmp as its base
                if not b.reads_memory:
                    continue
                if b.rs1 != rtmp:
                    continue
                # rtmp must be the only thing B uses it for, and dead after B
                if rtmp in b.live_out and b.rd != rtmp:
                    continue
                candidates.append((a, b))
    return candidates


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else os.path.join(
        os.path.dirname(__file__), "..", "tests", "godot.s")

    cands = find_candidates(path)
    total = len(cands)

    immA_sign = Counter()
    immB_sign = Counter()
    immA_bits = Counter()
    immB_bits = Counter()
    b_width = Counter()
    a_width = Counter()
    immA_zero = immB_zero = 0
    both_nonzero = 0

    for a, b in cands:
        ia = a.imm if a.imm is not None else 0
        ib = b.imm if b.imm is not None else 0
        immA_sign["zero" if ia == 0 else ("pos" if ia > 0 else "neg")] += 1
        immB_sign["zero" if ib == 0 else ("pos" if ib > 0 else "neg")] += 1
        immA_bits[_signed_bits(ia)] += 1
        immB_bits[_signed_bits(ib)] += 1
        if ia == 0:
            immA_zero += 1
        if ib == 0:
            immB_zero += 1
        if ia != 0 and ib != 0:
            both_nonzero += 1
        b_width[b.access_width] += 1
        a_width[a.access_width] += 1

    print("=" * 72)
    print(f"LOAD-LOAD-CHAIN opportunity  ({os.path.basename(path)})")
    print("  A: load rtmp, immA(rb)   B: load rd, immB(rtmp)   rtmp dead after B")
    print("=" * 72)
    print(f"  candidate pairs : {total}")
    print(f"    immB == 0 (pure deref-chain)      : {immB_zero}  ({pct(immB_zero,total)})")
    print(f"    immA == 0 (pure base-chain)       : {immA_zero}  ({pct(immA_zero,total)})")
    print(f"    both offsets nonzero (new ground) : {both_nonzero}  ({pct(both_nonzero,total)})")

    print(f"\n  immA sign:")
    for k in ("neg", "zero", "pos"):
        print(f"    {k:<5} {immA_sign[k]:>7}  ({pct(immA_sign[k],total)})")
    print(f"  immB sign:")
    for k in ("neg", "zero", "pos"):
        print(f"    {k:<5} {immB_sign[k]:>7}  ({pct(immB_sign[k],total)})")

    print(f"\n  immA signed-bit-width (bits incl. sign):")
    _hist(immA_bits, total)
    print(f"  immB signed-bit-width (bits incl. sign):")
    _hist(immB_bits, total)

    print(f"\n  Second-load (B) data size:")
    _width_hist(b_width, total)
    print(f"  First-load (A) data size:")
    _width_hist(a_width, total)

    # joint: bits needed if a single shared signed field had to hold the larger
    print(f"\n  max(immA_bits, immB_bits) — width of a single shared signed field:")
    joint = Counter()
    for a, b in cands:
        ia = a.imm if a.imm is not None else 0
        ib = b.imm if b.imm is not None else 0
        joint[max(_signed_bits(ia), _signed_bits(ib))] += 1
    _hist(joint, total)


def pct(n, d):
    return f"{100*n/d:.1f}%" if d else "0.0%"


def _hist(counter, total):
    cum = 0
    for bits in sorted(counter):
        cum += counter[bits]
        print(f"    {bits:>2} bits : {counter[bits]:>7}  ({pct(counter[bits],total)})"
              f"   cum {pct(cum,total)}")


def _width_hist(counter, total):
    for w in sorted(counter, key=lambda x: (x is None, x)):
        label = f"{w}B" if w is not None else "unknown"
        print(f"    {label:<8} {counter[w]:>7}  ({pct(counter[w],total)})")


if __name__ == "__main__":
    main()

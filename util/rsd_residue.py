"""
util/rsd_residue.py — what is left for rsd-alu-pair once every other frame
has taken what it can?

WHY THIS EXISTS.  `rsd-alu-pair` is `RULES[0]` -- it sees every pair before any
other frame does -- and it costs 256 codepoints, 30% of the whole namespace and
more than any other frame.  The biclique tiling that chose its op set was fed
by `analysis/alu_pair_cooccurrence.py` variant 3, which censuses pairable RSD
adjacencies WITHOUT asking whether another frame already encodes them.  So the
op set was chosen against a population that includes work `dual-setup-pair`
covers for 17 codepoints: of the 34927 pairs rsd-alu-pair is credited with,
`util/rule_overlap.py` measures 10221 as also acceptable to dual-setup-pair.
Roughly a third of the evidence for a 256-codepoint block is work a 17-codepoint
frame does anyway.

WHAT THIS MEASURES INSTEAD.  Two changes to the rule, then the real scheduler
and pairer:

  * BROADEN it -- drop the immediate-range gates entirely and drop the nine-op
    mnemonic set, so the census is not pre-filtered by decisions the tiling was
    supposed to make.  The structural gates stay: RSD-or-li form, the swappable
    rule, x0..x15 registers, distinct destinations.  Those are what the frame
    IS; the op set and the immediate widths are what is being chosen.
  * DEMOTE it to the END of `RULES`, so every other frame claims what it can
    first.  What the broadened rule then takes is exactly the residue: pairs
    that only this frame can encode.  This is the "exclusion of what other
    frames already handle" the old census skipped, and doing it by rule order
    rather than by a hand-written exclusion list means the exclusion is
    whatever the frames actually are today, not a snapshot of them.

The output is an (opA, opB) co-occurrence matrix over the residue, in the
format `util/biclique_tiling.py` reads, so the op set can be re-chosen against
what the frame is uniquely for.

THREE POPULATIONS are reported, because the difference between them is the
whole point:

  narrow-first   the rule as it ships, at RULES[0]           -- today's 34927
  narrow-last    the rule as it ships, demoted               -- what it alone buys
  broad-last     broadened and demoted                       -- the residue to tile

narrow-first minus narrow-last is the priority artefact.  broad-last minus
narrow-last is what the current op set and immediate widths are turning away.

Usage:  python3 util/rsd_residue.py [--out-dir results] [--corpora a,b]
"""
import argparse
import os
import sys
from collections import Counter
from concurrent.futures import ProcessPoolExecutor

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "util"))

from isa.xlen import detect_xlen
from rule_hits import CORPORA
from rule_overlap import driver

TARGET = "rsd-alu-pair"
# The two axes being relaxed are separated so the residue can be attributed:
# widening the OP SET and widening the IMMEDIATES are different decisions with
# different costs, and "broad-last - narrow-last" alone conflates them.
MODES = ("narrow-first", "narrow-last", "wideimm-last", "wideops-last",
         "broad-last")


def _is_alu():
    """`alu_pair_cooccurrence.is_alu` -- a GPR-writing integer ALU op, with no
    memory, branch, jump, fp, vector or system op.

    Reused rather than re-derived.  A hand-rolled version here let loads in:
    `lw a0, 0(a0)` satisfies `is_rsd`, so without the memory test the residue
    alphabet came back containing lw/lh/lhu/lbu and the tiling would have been
    choosing ALU opcodes against a population that was partly loads."""
    from analysis.alu_pair_cooccurrence import is_alu
    return is_alu


def install(mode):
    """Rebuild RULES for one of the three populations.  Idempotent per worker."""
    import scheduler.rules as rules
    from scheduler.rules import PairingRule, NotPair

    if getattr(rules, "_RESIDUE_MODE", None) == mode:
        return
    if not hasattr(rules, "_RESIDUE_PRISTINE"):
        rules._RESIDUE_PRISTINE = list(rules.RULES)
    base = list(rules._RESIDUE_PRISTINE)
    orig = next(r for r in base if r.name == TARGET)
    others = [r for r in base if r.name != TARGET]

    if mode == "narrow-first":
        rules.RULES[:] = base
        rules._RESIDUE_MODE = mode
        return
    if mode == "narrow-last":
        rules.RULES[:] = others + [orig]
        rules._RESIDUE_MODE = mode
        return

    is_alu = _is_alu()
    wide_imm = mode in ("wideimm-last", "broad-last")
    wide_ops = mode in ("wideops-last", "broad-last")
    if mode in ("wideimm-last", "wideops-last", "broad-last"):

        # Every structural gate the frame has.  The two immediate-range gates
        # are applied only when this mode keeps them, so dropping them is
        # visible here rather than hidden in a wrapper.
        @rules.a_is_rsd_or_li
        @rules.b_is_rsd_or_li
        @rules.a_rsd_swappable
        @rules.b_rsd_swappable
        @rules.uses_low_regs
        @rules.exclusive_rd
        def _probe(a, b):
            if wide_ops:
                if not (is_alu(a) and is_alu(b)):
                    raise NotPair("not-alu")
            else:
                if (a.mnemonic not in rules._RSD_ALU_MN
                        or b.mnemonic not in rules._RSD_ALU_MN):
                    raise NotPair("outside-declared-op-set")
            if not wide_imm:
                rules._imm_in_range(a)
                rules._imm_in_range(b)
            return None

        # mnemonic_set None: the op set is what we are trying to CHOOSE, so it
        # must not be a precondition of the census even when this mode narrows
        # it again inside the check.
        rules.RULES[:] = others + [PairingRule(name=TARGET, a_mnemonic_set=None,
                                               b_mnemonic_set=None, check=_probe)]
        rules._RESIDUE_MODE = mode
        return
    raise SystemExit(f"unknown mode {mode}")


def chunk(args):
    """Schedule+pair one chunk; return (opA, opB) counts for the target rule."""
    src, xlen, mode = args
    from scheduler.reorder import ScheduleMode
    from analysis.encoding_budget import subform
    install(mode)
    counts = Counter()
    for tag, packets in driver()._process_chunk(src, False, ScheduleMode.LIST,
                                                None, 0, xlen):
        if tag == "pad_nops":
            continue
        for item in packets:
            if item[0] == "pair" and item[3] == TARGET:
                counts[(subform(item[1]), subform(item[2]))] += 1
    return counts


def measure(names, mode):
    by_base = {32: Counter(), 64: Counter()}
    for name in names:
        source = open(os.path.join(ROOT, "tests", f"{name}.s")).read()
        xlen, _ = detect_xlen(source[:8192])
        chunks = driver()._split_source(source)
        with ProcessPoolExecutor() as pool:
            for c in pool.map(chunk, [(s, xlen, mode) for s in chunks]):
                by_base[xlen].update(c)
    return by_base


def write_table(co, path_csv, path_ops):
    """Square matrix + op index, the format biclique_tiling.py reads."""
    import json
    ops = sorted({o for pair in co for o in pair})
    idx = {o: i for i, o in enumerate(ops)}
    m = [[0] * len(ops) for _ in ops]
    for (a, b), n in co.items():
        m[idx[a]][idx[b]] = n
    with open(path_csv, "w") as fh:
        for row in m:
            fh.write(",".join(str(v) for v in row) + "\n")
    with open(path_ops, "w") as fh:
        json.dump(ops, fh)
    return ops


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out-dir", default=os.path.join(ROOT, "results"))
    ap.add_argument("--corpora", default=None)
    args = ap.parse_args()
    names = args.corpora.split(",") if args.corpora else CORPORA

    print("# rsd-alu-pair: what survives exclusion by every other frame.")
    print("# Generated by util/rsd_residue.py.\n")
    results = {}
    for mode in MODES:
        by_base = measure(names, mode)
        tot = Counter()
        tot.update(by_base[32])
        tot.update(by_base[64])
        results[mode] = (by_base, tot)
        print(f"{mode:14} RV32 {sum(by_base[32].values()):7}   "
              f"RV64 {sum(by_base[64].values()):7}   "
              f"total {sum(tot.values()):8}   "
              f"distinct (opA,opB) {len(tot):5}", file=sys.stderr)

    nf = sum(results["narrow-first"][1].values())
    nl = sum(results["narrow-last"][1].values())
    bl = sum(results["broad-last"][1].values())
    print(f"{'population':16}{'RV32':>9}{'RV64':>9}{'total':>10}{'combos':>9}")
    print("-" * 53)
    for mode in MODES:
        by_base, tot = results[mode]
        print(f"{mode:16}{sum(by_base[32].values()):9}"
              f"{sum(by_base[64].values()):9}{sum(tot.values()):10}{len(tot):9}")
    print("-" * 53)
    print(f"\npriority artefact  (narrow-first - narrow-last): {nf - nl:+7}"
          f"   {100 * (nf - nl) / nf:5.1f}% of today's credited hits are pairs")
    print(f"                                                            "
          f"another frame would have taken anyway")
    print(f"turned away by the op set and immediate widths"
          f"\n                   (broad-last - narrow-last): {bl - nl:+7}")

    for mode in MODES:
        by_base, tot = results[mode]
        stem = os.path.join(args.out_dir, f"rsd_{mode.replace('-', '_')}")
        ops = write_table(tot, stem + ".csv", stem + "_ops.json")
        if mode == "broad-last":
            print(f"\nresidue op alphabet ({len(ops)}): {', '.join(ops)}")
            print(f"\ntop residue (opA, opB) combinations:")
            for (a, b), n in tot.most_common(25):
                print(f"  {n:7}  {a:12} {b}")
    print(f"\nwrote {args.out_dir}/rsd_*.csv (+_ops.json). Tile with:")
    print(f"  python3 util/biclique_tiling.py "
          f"--table {args.out_dir}/rsd_broad_last.csv --N 8")


if __name__ == "__main__":
    main()

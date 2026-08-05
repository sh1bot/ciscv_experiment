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
    rule, distinct destinations.  Those are what the frame
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
         "broad-last", "imm5-last", "imm6-last", "imm7-last")


def _is_alu():
    """`alu_pair_cooccurrence.is_alu` -- a GPR-writing integer ALU op, with no
    memory, branch, jump, fp, vector or system op.

    Reused rather than re-derived.  A hand-rolled version here let loads in:
    `lw a0, 0(a0)` satisfies `is_rsd`, so without the memory test the residue
    alphabet came back containing lw/lh/lhu/lbu and the tiling would have been
    choosing ALU opcodes against a population that was partly loads."""
    from analysis.alu_pair_cooccurrence import is_alu
    return is_alu


def _imm_fits(insn, bits):
    """Does this op's immediate fit a uniform `bits`-wide field?

    Signedness comes from `analysis.imm_traits`, the project's single source of
    truth for immediate semantics by subform -- arithmetic/compare/li signed,
    shift amounts unsigned -- rather than being re-decided here.  A
    register-register op has no immediate and always fits.
    """
    from analysis.encoding_budget import subform
    from analysis.imm_traits import signed
    if insn.imm is None:
        return True
    sf = subform(insn)
    if signed(sf):
        return -(1 << (bits - 1)) <= insn.imm <= (1 << (bits - 1)) - 1
    return 0 <= insn.imm <= (1 << bits) - 1


def _order_free(a, b):
    """Could these two be emitted in either order?

    rsd-alu-pair packs two INDEPENDENT results, but nothing in the rule stops B
    reading A's destination, so order is free only when neither reads the
    other's.  Where it IS free the frame need not encode both orientations --
    the A and B op sets can be chosen so that whichever orientation is
    encodable is the one emitted.
    """
    return (a.rd not in b.uses_regs) and (b.rd not in a.uses_regs)


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
    wide_ops = mode not in ("wideimm-last",)
    # immN-last: broad ops, but a UNIFORM N-bit immediate instead of the
    # per-subform contract.  This is the axis the unlimited-immediate residue
    # could not price -- it counted pairs no 32-bit packet could hold.
    imm_bits = int(mode[3]) if mode.startswith("imm") else None
    if mode in ("wideimm-last", "wideops-last", "broad-last") or imm_bits:

        # Every structural gate the frame has.  The two immediate-range gates
        # are applied only when this mode keeps them, so dropping them is
        # visible here rather than hidden in a wrapper.
        @rules.a_is_rsd_or_li
        @rules.b_is_rsd_or_li
        @rules.a_rsd_swappable
        @rules.b_rsd_swappable
        @rules.exclusive_rd
        def _probe(a, b):
            if wide_ops:
                if not (is_alu(a) and is_alu(b)):
                    raise NotPair("not-alu")
            else:
                if (a.mnemonic not in rules._RSD_ALU_MN
                        or b.mnemonic not in rules._RSD_ALU_MN):
                    raise NotPair("outside-declared-op-set")
            if imm_bits is not None:
                for i in (a, b):
                    if not _imm_fits(i, imm_bits):
                        raise NotPair("big-imm")
            elif not wide_imm:
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
                counts[(subform(item[1]), subform(item[2]),
                        _order_free(item[1], item[2]))] += 1
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
    ops = sorted({o for k in co for o in k[:2]})
    idx = {o: i for i, o in enumerate(ops)}
    m = [[0] * len(ops) for _ in ops]
    for (a, b, _free), n in co.items():
        m[idx[a]][idx[b]] += n
    with open(path_csv, "w") as fh:
        for row in m:
            fh.write(",".join(str(v) for v in row) + "\n")
    with open(path_ops, "w") as fh:
        json.dump(ops, fh)
    return ops


def coverage(pairs, A, B):
    """Mass an A-set x B-set frame covers.

    A forced pair needs its own orientation.  An order-free pair needs EITHER,
    because the scheduler can emit whichever one the sets encode -- that is the
    whole benefit of choosing A and B separately rather than symmetrically.
    """
    tot = 0
    for (x, y, free), n in pairs.items():
        if (x in A and y in B) or (free and y in A and x in B):
            tot += n
    return tot


def optimise(pairs, na, nb, rounds=40):
    """Best (A, B) op sets of sizes na, nb by alternating maximisation.

    Alternating rather than exhaustive: choosing 16 of 83 ops for each side is
    C(83,16)^2, so the sets are re-picked greedily against the other side until
    they stop moving, from several starts to blunt the local optimum.
    """
    ops = sorted({o for k in pairs for o in k[:2]})
    row = Counter()
    col = Counter()
    for (x, y, _f), n in pairs.items():
        row[x] += n
        col[y] += n

    best = (0, (), ())
    starts = [(set(o for o, _ in row.most_common(na)),
               set(o for o, _ in col.most_common(nb))),
              (set(o for o, _ in col.most_common(na)),
               set(o for o, _ in row.most_common(nb)))]
    for A, B in starts:
        for _ in range(rounds):
            # re-pick A against fixed B, by exact marginal contribution
            gain = Counter()
            for (x, y, free), n in pairs.items():
                if y in B:
                    gain[x] += n
                elif free and x in B:
                    gain[y] += n
            newA = set(o for o, _ in gain.most_common(na))
            gain = Counter()
            for (x, y, free), n in pairs.items():
                if x in newA:
                    gain[y] += n
                elif free and y in newA:
                    gain[x] += n
            newB = set(o for o, _ in gain.most_common(nb))
            if (newA, newB) == (A, B):
                break
            A, B = newA, newB
        c = coverage(pairs, A, B)
        if c > best[0]:
            best = (c, tuple(sorted(A)), tuple(sorted(B)))
    return best


def report_sets(pairs, label, budgets, out=sys.stdout):
    total = sum(pairs.values())
    free = sum(n for k, n in pairs.items() if k[2])
    print(f"\n### {label}   {total} pairs, {100*free/total:.1f}% order-free",
          file=out)
    print(f"{'|A|x|B|':>9}{'cp':>6}{'covered':>10}{'%':>8}   ops", file=out)
    for na, nb in budgets:
        c, A, B = optimise(pairs, na, nb)
        print(f"{na:4}x{nb:<4}{na*nb:6}{c:10}{100*c/total:7.1f}%", file=out)
        print(f"           A: {', '.join(A)}", file=out)
        print(f"           B: {', '.join(B)}", file=out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out-dir", default=os.path.join(ROOT, "results"))
    ap.add_argument("--corpora", default=None)
    ap.add_argument("--modes", default=None,
                    help="comma-separated subset of MODES")
    args = ap.parse_args()
    names = args.corpora.split(",") if args.corpora else CORPORA
    modes = args.modes.split(",") if args.modes else list(MODES)
    BUDGETS = [(4, 4), (8, 4), (4, 8), (8, 8), (16, 8), (8, 16), (16, 16)]

    print("# rsd-alu-pair: what survives exclusion by every other frame.")
    print("# Generated by util/rsd_residue.py.\n")
    results = {}
    for mode in modes:
        by_base = measure(names, mode)
        tot = Counter()
        tot.update(by_base[32])
        tot.update(by_base[64])
        results[mode] = (by_base, tot)
        print(f"{mode:14} RV32 {sum(by_base[32].values()):7}   "
              f"RV64 {sum(by_base[64].values()):7}   "
              f"total {sum(tot.values()):8}   "
              f"distinct (opA,opB) {len({k[:2] for k in tot}):5}",
              file=sys.stderr)

    have = lambda m: m in results
    nf = sum(results["narrow-first"][1].values()) if have("narrow-first") else 0
    nl = sum(results["narrow-last"][1].values()) if have("narrow-last") else 0
    bl = sum(results["broad-last"][1].values()) if have("broad-last") else 0
    print(f"{'population':16}{'RV32':>9}{'RV64':>9}{'total':>10}{'combos':>9}")
    print("-" * 53)
    for mode in modes:
        by_base, tot = results[mode]
        print(f"{mode:16}{sum(by_base[32].values()):9}"
              f"{sum(by_base[64].values()):9}{sum(tot.values()):10}{len({k[:2] for k in tot}):9}")
    print("-" * 53)
    if nf and nl:
        print(f"\npriority artefact  (narrow-first - narrow-last): {nf - nl:+7}"
              f"   {100 * (nf - nl) / nf:5.1f}% of today's credited hits are pairs")
        print(f"                                                            "
              f"another frame would have taken anyway")
    if bl and nl:
        print(f"turned away by the op set and immediate widths"
              f"\n                   (broad-last - narrow-last): {bl - nl:+7}")

    print("\n\n## A and B op sets chosen SEPARATELY")
    print("An order-free pair needs only ONE of its two orientations encodable,")
    print("so the A set and the B set need not match.  |A|x|B| is the codepoint")
    print("cost, and these are single-tile: compare against biclique_tiling's")
    print("b>0 splits, which buy shape at the price of block structure.")
    for mode in modes:
        if mode in ("narrow-first", "narrow-last"):
            continue
        _by, tot = results[mode]
        report_sets(tot, mode, BUDGETS)

    for mode in modes:
        by_base, tot = results[mode]
        stem = os.path.join(args.out_dir, f"rsd_{mode.replace('-', '_')}")
        ops = write_table(tot, stem + ".csv", stem + "_ops.json")
        with open(stem + "_pairs.json", "w") as fh:
            import json as _j
            _j.dump([[a, b, f, n] for (a, b, f), n in tot.items()], fh)
        if mode == "broad-last":
            print(f"\nresidue op alphabet ({len(ops)}): {', '.join(ops)}")
            print(f"\ntop residue (opA, opB) combinations:")
            flat = Counter()
            for (a, b, _f), n in tot.items():
                flat[(a, b)] += n
            for (a, b), n in flat.most_common(25):
                print(f"  {n:7}  {a:12} {b}")
    print(f"\nwrote {args.out_dir}/rsd_*.csv (+_ops.json). Tile with:")
    print(f"  python3 util/biclique_tiling.py "
          f"--table {args.out_dir}/rsd_broad_last.csv --N 8")


if __name__ == "__main__":
    main()

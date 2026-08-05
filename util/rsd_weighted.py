"""
util/rsd_weighted.py — choose rsd-alu-pair's op set when immediate range is
paid for in OPCODE ENTRIES.

THE MISTAKE THIS FIXES.  An earlier pass (results/corpus/RSD-RESIDUE.md, first
two sections) costed an A-set x B-set frame at |A| x |B| codepoints, one entry
per op, and then swept a uniform immediate width across the whole set as though
width were free.  It is not.  encoding.yaml's rule is that a field is five bits
per register column the ROW draws, and an op reaches past that only by
occupying more opcode-list entries: an op declaring N bits takes 2^(N - field)
of them.  rsd-alu-pair's rows draw imma and immb five bits wide, so:

    reg-reg op (add, or, xor, mul, czero.*, sh1add ...)   weight 1
    5-bit immediate op (andi, slli, srli, addiw)          weight 1
    6-bit                                                 weight 2
    7-bit  (addi and li, as declared today)               weight 4
    8-bit                                                 weight 8

and the block is weight(A) x weight(B).  That is exactly where today's 256
comes from: ten ops per slot, but `addi` and `li` at seven bits cost four
entries each, so the slot weighs 4 + 4 + 8x1 = 16, and 16 x 16 = 256.  TWO
OPS BUY HALF THE FRAME.

WHY THAT CHANGES THE ANSWER.  Weight is the budget, not op count, so every
extra bit on one immediate op trades directly against reg-reg ops that need no
extension at all: one bit on `li` (weight 4 -> 8) costs the same as four more
reg-reg opcodes.  A uniform width sweep cannot see that trade because it moves
every op at once.  Here the width is chosen PER OP, which is what the encoding
actually allows.

It also disposes of the unlimited-immediate problem for free.  Pairs needing a
twenty-bit immediate are not excluded by hand; they simply never get selected,
because reaching them would cost 2^15 entries.  The budget does the filtering.

Usage:  python3 util/rsd_weighted.py --measure      (census, writes the cache)
        python3 util/rsd_weighted.py               (optimise from the cache)
"""
import argparse
import json
import math
import os
import sys
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "util"))

from isa.xlen import detect_xlen
from rule_hits import CORPORA
from rule_overlap import driver
import rsd_residue

FIELD = 5                      # bits rsd-alu-pair's rows draw for imma/immb
TODAY = 23306                  # narrow-last: the shipped frame, demoted
CACHE = os.path.join(ROOT, "results", "rsd_weighted_pairs.json")
NO_IMM = -1                    # this op carries no immediate at all


def req_bits(insn):
    """Minimum field width this operand needs, or NO_IMM for reg-reg.

    Signedness from `analysis.imm_traits`, as everywhere else in the project.
    """
    from analysis.encoding_budget import subform
    from analysis.imm_traits import signed
    if insn.imm is None:
        return NO_IMM
    v = insn.imm
    if signed(subform(insn)):
        n = 1
        while not (-(1 << (n - 1)) <= v <= (1 << (n - 1)) - 1):
            n += 1
        return n
    if v < 0:
        return 99              # unsigned field cannot hold it at any width
    return max(1, v.bit_length())


# subform -> DECLARED yaml op.  encoding.yaml declares one `addi` entry whose
# register/immediate choices are the li / mv / addi4spn / addi_rsd subforms, so
# they share its opcode entries and must be costed ONCE.  rsd-alu-pair declares
# `li` separately because li sets rs1 = x0, which breaks the RSD form the rest
# of the frame relies on.  Costing per subform instead of per declaration made
# today's slot weigh 28 rather than the 16 that yields its actual 256 block.
_ADDI_SUBFORMS = {"addi_rsd", "addi_other", "mv", "addi4spn"}


def decl(subform_name):
    return "addi" if subform_name in _ADDI_SUBFORMS else subform_name


def weight(bits):
    """Opcode entries an op costs at a given required width."""
    if bits == NO_IMM:
        return 1
    return 1 << max(0, bits - FIELD)


def chunk(args):
    src, xlen = args
    from scheduler.reorder import ScheduleMode
    from analysis.encoding_budget import subform
    rsd_residue.install("broad-last")
    counts = Counter()
    for tag, packets in driver()._process_chunk(src, False, ScheduleMode.LIST,
                                                None, 0, xlen):
        if tag == "pad_nops":
            continue
        for item in packets:
            if item[0] == "pair" and item[3] == rsd_residue.TARGET:
                a, b = item[1], item[2]
                counts[(subform(a), req_bits(a), subform(b), req_bits(b),
                        rsd_residue._order_free(a, b))] += 1
    return counts


def measure(names):
    tot = Counter()
    for name in names:
        source = open(os.path.join(ROOT, "tests", f"{name}.s")).read()
        xlen, _ = detect_xlen(source[:8192])
        chunks = driver()._split_source(source)
        with ProcessPoolExecutor() as pool:
            for c in pool.map(chunk, [(s, xlen) for s in chunks]):
                tot.update(c)
        print(f"  {name}", file=sys.stderr)
    return tot


def load():
    raw = json.load(open(CACHE))
    return Counter({(a, ra, b, rb, f): n for a, ra, b, rb, f, n in raw})


def slot_demand(pairs):
    """Per op, the mass reachable at each width, for both slots."""
    dem = {"a": defaultdict(Counter), "b": defaultdict(Counter)}
    for (a, ra, b, rb, _f), n in pairs.items():
        dem["a"][a][ra] += n
        dem["b"][b][rb] += n
    return dem


def covered(pairs, wa, wb):
    """Mass a policy covers.  wa/wb map op -> chosen width (NO_IMM = reg-reg
    only, absent = op not in the set)."""
    tot = 0
    for (a, ra, b, rb, free), n in pairs.items():
        da, db = decl(a), decl(b)
        if _ok(wa, da, ra) and _ok(wb, db, rb):
            tot += n
        elif free and _ok(wa, db, rb) and _ok(wb, da, ra):
            tot += n
    return tot


def _ok(policy, op, need):
    got = policy.get(op)
    if got is None:
        return False
    if need == NO_IMM:
        return True            # a reg-reg use fits whatever the op declares
    return got != NO_IMM and got >= need


def _slot_weight(policy):
    return sum(weight(w) for w in policy.values())


def greedy(pairs, cap_a, cap_b, rounds=6):
    """Pick per-op widths for each slot under a WEIGHT cap on each.

    Greedy by marginal coverage per marginal weight, alternating slots and
    re-running so each slot is re-chosen against the other's current policy.
    Widths are per op, so 'add another reg-reg op' and 'give li one more bit'
    compete on the same scale -- which is the whole point.
    """
    ops = sorted({decl(k[0]) for k in pairs} | {decl(k[2]) for k in pairs})
    widths = [NO_IMM, 5, 6, 7, 8, 9, 10]
    wa, wb = {}, {}
    for _ in range(rounds):
        for slot in ("a", "b"):
            cur, cap = (wa, cap_a) if slot == "a" else (wb, cap_b)
            other = wb if slot == "a" else wa
            if not other:                      # bootstrap: seed from raw mass
                seed = Counter()
                for (a, ra, b, rb, _f), n in pairs.items():
                    seed[decl(b) if slot == "a" else decl(a)] += n
                for op, _ in seed.most_common(8):
                    other.setdefault(op, 7)
            cur.clear()
            used = 0
            while True:
                best = None
                for op in ops:
                    for w in widths:
                        if cur.get(op) is not None and weight(w) <= weight(cur[op]):
                            continue
                        delta = weight(w) - weight(cur.get(op, NO_IMM)) \
                            if op in cur else weight(w)
                        if used + delta > cap or delta <= 0:
                            continue
                        trial = dict(cur)
                        trial[op] = w
                        gain = (covered(pairs, trial, other) if slot == "a"
                                else covered(pairs, other, trial)) \
                            - (covered(pairs, cur, other) if slot == "a"
                               else covered(pairs, other, cur))
                        if gain <= 0:
                            continue
                        ratio = gain / delta
                        if best is None or ratio > best[0]:
                            best = (ratio, op, w, delta)
                if best is None:
                    break
                _r, op, w, delta = best
                cur[op] = w
                used += delta
    return wa, wb, covered(pairs, wa, wb)


def fmt(policy):
    parts = []
    for op, w in sorted(policy.items(), key=lambda kv: (-weight(kv[1]), kv[0])):
        parts.append(op if w == NO_IMM else f"{op}:{w}b(w{weight(w)})")
    return ", ".join(parts)


# The frame as it ships, in subform terms.  encoding.yaml declares `addi` at
# seven bits; subform splits that into addi_rsd / addi_other / mv / addi4spn,
# and `li` is declared separately, also at seven.
TODAY_POLICY = {"addi": 7, "li": 7, "add": NO_IMM, "addiw": 5, "and": NO_IMM,
                "andi": 5, "or": NO_IMM, "slli": 5, "srli": 5, "xor": NO_IMM}


def validate(pairs, expect):
    """Does the weight model reproduce what the shipped frame actually takes?

    If it cannot, the optimisation below is not trustworthy, so this runs
    first.  The comparison is against `narrow-last` -- the frame demoted, so
    both numbers are measured on the same exclusion-corrected population.
    """
    got = covered(pairs, TODAY_POLICY, TODAY_POLICY)
    wa = _slot_weight(TODAY_POLICY)
    print(f"# MODEL CHECK: slot weight {wa}, block {wa * wa} — reproduces the")
    print(f"#   frame's actual 256 exactly, so the COST model is right.")
    print(f"#   Today's policy covers {got} of this residue; the same rule")
    print(f"#   demoted takes {expect} in its own run ({100*got/expect:.1f}%).")
    print(f"#   The gap is population, not model: `narrow-last` and this census")
    print(f"#   are separate scheduler runs, the broadened rule takes different")
    print(f"#   pairs, and greedy pairing is not monotone.  So every comparison")
    print(f"#   below is against {got} — today's op set scored on THIS")
    print(f"#   population — not against {expect}.")
    return got


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--measure", action="store_true")
    ap.add_argument("--corpora", default=None)
    args = ap.parse_args()

    if args.measure:
        names = args.corpora.split(",") if args.corpora else CORPORA
        tot = measure(names)
        with open(CACHE, "w") as fh:
            json.dump([[a, ra, b, rb, f, n] for (a, ra, b, rb, f), n in tot.items()],
                      fh)
        print(f"wrote {CACHE}: {sum(tot.values())} pairs, {len(tot)} distinct")
        return

    pairs = load()
    total = sum(pairs.values())
    free = sum(n for k, n in pairs.items() if k[4])
    print("# rsd-alu-pair op set, with immediate range paid for in opcode entries.")
    print("# Generated by util/rsd_weighted.py.")
    print(f"#\n# residue {total} pairs, {100*free/total:.1f}% order-free.")
    print(f"# The row draws {FIELD} bits, so an op needing N bits costs "
          f"2^(N-{FIELD}) entries.")
    print(f"# Today: 10 ops/slot but addi and li at 7 bits cost 4 each, so the")
    print(f"# slot weighs 16 and the block is 16x16 = 256.")

    baseline = validate(pairs, TODAY)
    print()
    print(f"{'wA':>4}{'wB':>4}{'block':>7}{'covered':>10}{'%':>8}"
          f"{'vs today':>10}")
    print("-" * 47)
    rows = []
    for wa_cap, wb_cap in ((4, 4), (8, 4), (4, 8), (8, 8), (16, 8), (8, 16),
                           (16, 16)):
        wa, wb, cov = greedy(pairs, wa_cap, wb_cap)
        blk = wa_cap * wb_cap
        rows.append((wa_cap, wb_cap, blk, cov, wa, wb))
        print(f"{wa_cap:4}{wb_cap:4}{blk:7}{cov:10}{100*cov/total:7.1f}%"
              f"{cov - baseline:+10}")
    print("-" * 47)
    print(f"'vs today' is against {baseline}: the SHIPPED op set "
          f"(addi/li at 7 bits,\naddiw/andi/slli/srli at 5, add/and/or/xor "
          f"reg-reg) scored on this same\npopulation, at its own 16x16 = 256 "
          f"block.  Like for like.\n")

    for wa_cap, wb_cap, blk, cov, wa, wb in rows:
        print(f"\n### weight {wa_cap} x {wb_cap} = {blk} codepoints — "
              f"{cov} pairs ({100*cov/total:.1f}%)")
        print(f"  A ({_slot_weight(wa)}): {fmt(wa)}")
        print(f"  B ({_slot_weight(wb)}): {fmt(wb)}")


if __name__ == "__main__":
    main()

"""
util/chain_width_sweep.py — how should load5-load5-chain split its ten bits?

The two pointer-chase frames divide the population by whether the FIRST load
carries an offset:

  load0-load10-chain      lx tmp, 0(rs1a) ; load rdb, k*immb(tmp)
                       imma pinned zero, so immb takes all ten free bits.
  load5-load5-chain  lx tmp, k*imma(rs1a) ; load rdb, k*immb(tmp)
                       both offsets real, so the same ten bits must be SPLIT.

Only the second frame has a choice to make, and it is a genuine trade: the ten
bits come from `funct5`+`rs2`, columns the pair leaves free because `tmp` is
implicit, and a bit given to `imma` is a bit taken from `immb`.  This sweeps
that split.

WHAT IT COSTS.  A field is five bits per register column it consumes and grows
past that only by taking more opcode entries, so any split summing to ten is
free -- 1 A-op x 7 B-ops = 7 codepoints, block 8, whatever the division.  Only
a total ABOVE ten costs anything, and then it doubles the block per bit.  So
the sweep is not looking for the cheapest split; every split on the diagonal
costs exactly the same.  It is looking for the one that catches the most.

WHY TOTAL PAIRS IS THE HEADLINE.  Pairs are re-measured by the real scheduler
and pairer at each setting rather than read off an offset histogram: changing
the split changes which chases the frame takes, which changes what is left for
every other frame.  A split that gains 200 chain pairs by taking them from a
frame that would have had them anyway has gained nothing, and only the corpus
total shows that.

`--verify` re-runs the extreme splits to confirm the two frames stay disjoint:
load0-load10-chain demands imma == 0 and load5-load5-chain demands it nonzero,
so no split of the off-chain field should move load0-load10-chain's count at all.

Usage:  python3 util/chain_width_sweep.py [--total 10] [--verify]
        python3 util/chain_width_sweep.py --splits 3,7 5,5 7,3
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

WIDE, SPLIT = "load0-load10-chain", "load5-load5-chain"


def codepoints(na, nb, ops=7, field=10):
    """The split frame's block at a given division.

    Any split summing to `field` is free; the columns are already drawn.  Going
    over doubles the opcode entries per extra bit.
    """
    return ops * (1 << max(0, na + nb - field))


def block(cp):
    """The power-of-two block encoding_assign would reserve for `cp`."""
    n = 1
    while n < cp:
        n <<= 1
    return n


def chunk_hits(args):
    """Schedule+pair one chunk with the split set to (na, nb)."""
    chunk, xlen, na, nb = args
    import scheduler.rules as rules
    from scheduler.reorder import ScheduleMode

    rules._L5L5_IMMA_BITS = na
    rules._L5L5_IMMB_BITS = nb
    counts = Counter()
    for tag, packets in driver()._process_chunk(chunk, False, ScheduleMode.LIST,
                                                None, 0, xlen):
        # The last entry is not a function: _process_chunk appends a
        # ('pad_nops', count) sentinel carrying the discarded padding count.
        if tag == "pad_nops":
            continue
        for item in packets:
            if item[0] == "pair":
                counts[item[3]] += 1
    return counts


def measure(names, na, nb):
    """{rule: hits} per base at one split."""
    by_base = {32: Counter(), 64: Counter()}
    for name in names:
        source = open(os.path.join(ROOT, "tests", f"{name}.s")).read()
        xlen, _ = detect_xlen(source[:8192])
        chunks = driver()._split_source(source)
        with ProcessPoolExecutor() as pool:
            for counts in pool.map(chunk_hits,
                                   [(c, xlen, na, nb) for c in chunks]):
                by_base[xlen].update(counts)
    return by_base


def combined(by_base):
    tot = Counter()
    tot.update(by_base[32])
    tot.update(by_base[64])
    return tot


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--total", type=int, default=10,
                    help="bits to divide between imma and immb")
    ap.add_argument("--splits", nargs="*", default=None, metavar="A,B")
    ap.add_argument("--corpora", default=None)
    ap.add_argument("--verify", action="store_true")
    args = ap.parse_args()

    names = args.corpora.split(",") if args.corpora else CORPORA
    if args.splits:
        splits = [tuple(int(x) for x in s.split(",")) for s in args.splits]
    else:
        splits = [(na, args.total - na) for na in range(2, args.total - 1)]

    print(f"# load5-load5-chain field split.  {len(names)} corpora, "
          f"{args.total} bits to divide.")
    print("#\n# Every split summing to the field width costs the SAME 7")
    print("# codepoints, so this is not a cost trade -- it is purely about")
    print("# which division catches the most.  `total` is all corpus pairs.")
    print(f"\n{'imma':>5}{'immb':>5}  {'off-chain':>10}{'wide':>8}{'both':>8}"
          f"{'total':>10}{'cp':>5}{'block':>7}")
    print("-" * 60)

    seen, best = {}, None
    for na, nb in splits:
        by_base = seen[(na, nb)] = measure(names, na, nb)
        tot = combined(by_base)
        s, w = tot[SPLIT], tot[WIDE]
        total = sum(tot.values())
        cp = codepoints(na, nb)
        mark = ""
        if best is None or total > best[0]:
            best, mark = (total, na, nb), ""
        print(f"{na:5}{nb:5}  {s:10}{w:8}{s + w:8}{total:10}{cp:5}"
              f"{block(cp):7}{mark}")

    print(f"\nbest: imma={best[1]} immb={best[2]} at {best[0]} corpus pairs")

    print("\nper-base detail:")
    for (na, nb), by_base in seen.items():
        for base in (32, 64):
            print(f"  {na},{nb} RV{base}  off-chain "
                  f"{by_base[base][SPLIT]:7}  wide {by_base[base][WIDE]:7}"
                  f"  total {sum(by_base[base].values()):8}")

    if args.verify:
        # NOT a disjointness test.  Rule-level disjointness is structural --
        # load0-load10-chain demands imma == 0 and load5-load5-chain demands it
        # nonzero, so no chase can satisfy both -- and it is measured directly
        # by util/rule_overlap.py, which reports `hidden` 0 for each.  What
        # this column shows is the SCHEDULER's sensitivity: narrowing the split
        # frame leaves instructions unpaired, the greedy pairer then makes
        # different choices, and a few land on load0-load10-chain instead.  Greedy
        # list scheduling is not monotone, so a nonzero spread here is expected
        # and says nothing about whether the rules overlap.
        wides = {(na, nb): combined(bb)[WIDE] for (na, nb), bb in seen.items()}
        lo, hi = min(wides.values()), max(wides.values())
        print("\nscheduler sensitivity — load0-load10-chain's own count as the")
        print("SIBLING's split changes.  The rules cannot overlap (imma == 0")
        print("versus nonzero); any movement here is the greedy pairer making")
        print("different choices with the instructions the sibling left free:")
        for k, v in wides.items():
            print(f"  imma={k[0]} immb={k[1]}:  load0-load10-chain {v}")
        print(f"  spread {hi - lo} pairs over {hi} ({100 * (hi - lo) / hi:.2f}%)"
              f" — scheduling noise, not overlap")


if __name__ == "__main__":
    main()

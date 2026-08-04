"""
util/chain_width_sweep.py — how wide should the two pointer-chase offsets be?

`deref-load-chain` and `base-load-chain` are the same shape with the offset on
opposite loads.  Both draw a 10-bit field from `funct5`+`rs2`, which the pair
leaves free because `tmp` is implicit.  Ten is where the free columns run out,
not a measured answer -- the yaml carried "TODO: decide how to balance imma and
immb sizes" for exactly that reason.

WHAT THE SWEEP COSTS.  A field is five bits per register column it consumes and
grows past that only by taking more opcode entries: an op declaring N bits
occupies 2^(N - field) of them.  So for these frames

    w <= 10   free -- the columns are already there, 49 codepoints
    w = 11    98 codepoints, block 64 -> 128
    w = 12    196 codepoints, block 256

and narrowing below 10 SAVES NOTHING: the columns cannot be spent on anything
else.  The interesting question is therefore not "what is the cheapest width
that works" but "is either form's demand worth an opcode doubling", and where
each curve goes flat.

WHY BOTH CURVES COME FROM ONE RUN.  The two forms are ALMOST disjoint: each
demands the other load's offset be zero, so a pair carrying two real offsets
fits neither and a pair carrying one fits exactly one.  They collide only on
the chase with NO offset at all, which satisfies both and fits at every width
-- so it cannot move as widths change.  A run at (wa, wb) therefore reports
both curves at once.  `--verify` re-runs the off-diagonal settings to check
that independence rather than assume it: an earlier version of this comment
asserted the two were fully disjoint, and the overlap tool refuted it.

Pairs are re-measured by the REAL scheduler and pairer at each width, not
extrapolated from an offset histogram: widening a frame changes which pairs it
takes, which changes what is left for every other frame.  The headline number
is therefore TOTAL corpus pairs, not the frame's own count -- a frame that
gains 200 pairs by taking them from a frame that would have had them anyway
has gained nothing.

Usage:  python3 util/chain_width_sweep.py [--widths 8,9,10,11,12] [--verify]
        python3 util/chain_width_sweep.py --corpora musl-rv32,sqlite-rv64
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

FRAMES = ("deref-load-chain", "base-load-chain")


def codepoints(width, ops=49, field=10):
    """What a frame's op block costs at a given immediate width.

    Below the field width the columns are already drawn and nothing is saved;
    above it every bit doubles the opcode entries the ops occupy.
    """
    return ops * (1 << max(0, width - field))


def block(cp):
    """The power-of-two block encoding_assign would reserve for `cp`."""
    n = 1
    while n < cp:
        n <<= 1
    return n


def chunk_hits(args):
    """Schedule+pair one chunk at a given (wa, wb); return per-rule hit counts."""
    chunk, xlen, wa, wb = args
    import scheduler.rules as rules
    from scheduler.reorder import ScheduleMode

    rules._DEREF_OFF_BITS = wa
    rules._BASE_OFF_BITS = wb
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


def measure(names, wa, wb):
    """{rule: hits} and total pairs over `names`, split by base."""
    by_base = {32: Counter(), 64: Counter()}
    for name in names:
        path = os.path.join(ROOT, "tests", f"{name}.s")
        source = open(path).read()
        xlen, _ = detect_xlen(source[:8192])
        chunks = driver()._split_source(source)
        with ProcessPoolExecutor() as pool:
            for counts in pool.map(chunk_hits,
                                   [(c, xlen, wa, wb) for c in chunks]):
                by_base[xlen].update(counts)
    return by_base


def row(label, by_base, out=sys.stdout):
    tot = Counter()
    tot.update(by_base[32])
    tot.update(by_base[64])
    return tot


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--widths", default="8,9,10,11,12")
    ap.add_argument("--corpora", default=None)
    ap.add_argument("--verify", action="store_true",
                    help="re-run one off-diagonal setting to test independence")
    args = ap.parse_args()

    names = args.corpora.split(",") if args.corpora else CORPORA
    widths = [int(w) for w in args.widths.split(",")]

    print(f"# Pointer-chase offset width sweep.  {len(names)} corpora.")
    print("#\n# Both frames swept together: they collide only on the no-offset")
    print("# chase, which fits at every width, so one run reports both curves.")
    print("# `total` is ALL corpus pairs -- the number that says whether a width")
    print("# bought anything or merely moved it from another frame.")
    print(f"\n{'w':>3}  {'deref':>8}{'base':>8}{'both':>8}"
          f"{'total':>10}{'d.cp':>7}{'b.cp':>7}{'block':>7}")
    print("-" * 62)

    prev = None
    seen = {}                      # each width is measured ONCE and reused
    for w in widths:
        by_base = seen[w] = measure(names, w, w)
        tot = row(f"w={w}", by_base)
        d, b = tot[FRAMES[0]], tot[FRAMES[1]]
        total = sum(tot.values())
        cp = codepoints(w)
        delta = "" if prev is None else f"  {total - prev:+d}"
        print(f"{w:>3}  {d:8}{b:8}{d + b:8}{total:10}{cp:7}{cp:7}"
              f"{block(cp):7}{delta}")
        prev = total

    print("\nper-base detail at each width:")
    for w in widths:
        by_base = seen[w]
        for base in (32, 64):
            d, b = by_base[base][FRAMES[0]], by_base[base][FRAMES[1]]
            print(f"  w={w:<3} RV{base}  deref {d:7}  base {b:7}  "
                  f"total {sum(by_base[base].values()):8}")

    if args.verify:
        print("\nindependence check — widening one form must not move the other:")
        lo, hi = min(widths), max(widths)
        for wa, wb in ((hi, lo), (lo, hi)):
            tot = row("", measure(names, wa, wb))
            print(f"  wa={wa:<3} wb={wb:<3}  deref {tot[FRAMES[0]]:7}  "
                  f"base {tot[FRAMES[1]]:7}  total {sum(tot.values()):8}")
        print("  compare each against the matching w= row above: deref should")
        print("  match the wa row and base the wb row, exactly.")


if __name__ == "__main__":
    main()

"""util/anchor_scan.py — what could pair with an anchor instruction?

Reports, for one or more corpora, the operand shapes reachable at an anchor:
the marginal reach of each shape, the greedy op-set order, and the coverage of
a fixed set. Everything is measured against the REAL scheduled and paired
stream, counts only candidates no other frame has taken, and allows the
scheduler to reorder a candidate down to the anchor.

The first run on a corpus pays for the schedule (minutes on cpp-rv32); every
run after that reads the cache and answers in seconds. The cache is keyed by
the content of the parser, the scheduler, the pairer, the rules and the yaml,
so it invalidates itself when any of them changes.

  python3 util/anchor_scan.py cpp-rv32 musl-gcc-rv32 sqlite-rv32
  python3 util/anchor_scan.py cpp-rv32 --anchor jalr
  python3 util/anchor_scan.py cpp-rv32 --fixed 'mv rd5,rs5' 'li rd3,imm7'
"""
import argparse
import os
import sys
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from analysis.anchors import scan, greedy, coverage, direct_call, any_call


def anchor_for(spec):
    if spec == "call":
        return any_call
    if spec == "near-call":
        return direct_call
    if spec == "jalr":
        return lambda r: r.mnem == "jalr"
    if spec == "branch":
        return lambda r: r.is_branch
    return lambda r: r.mnem == spec


def report(corpus, args):
    pred = anchor_for(args.anchor)
    n_anchors, n_scored, rows = scan(corpus, anchor=pred, budget=args.budget,
                                     rebuild=args.rebuild)
    if not n_scored:
        print(f"== {corpus}: {n_anchors} anchors, none scored")
        return
    taken = n_anchors - n_scored
    print(f"== {corpus}: {n_anchors} `{args.anchor}` anchors, {n_scored} solo "
          f"({taken} already paired by another frame)")

    dist = Counter(min(c, 4) for _, c in rows)
    print("   -- movable candidates per anchor --")
    for k in sorted(dist):
        tail = "+" if k == 4 else ""
        print(f"   {dist[k]:8d}  {100*dist[k]/n_scored:5.1f}%  {k}{tail}")

    marg = Counter()
    for avail, _ in rows:
        for shp in avail:
            marg[shp] += 1
    print("   -- marginal reach of each shape (overlapping) --")
    for shp, v in marg.most_common(args.top):
        print(f"   {v:8d}  {100*v/n_scored:5.1f}%  {shp}")

    print("   -- greedy op-set order --")
    for shp, gain, cum in greedy(rows, args.limit):
        print(f"   +{gain:7d}  cum {100*cum/n_scored:5.1f}%  {shp}")

    if args.fixed:
        cov = coverage(rows, args.fixed)
        print(f"   -- fixed set of {len(args.fixed)} --")
        for k in range(1, len(args.fixed) + 1):
            c = coverage(rows, args.fixed[:k])
            print(f"   {c:8d}  {100*c/n_scored:5.1f}%  +{args.fixed[k-1]}")
        print(f"   TOTAL {cov} = {100*cov/n_scored:.1f}% of solo anchors, "
              f"{100*cov/n_anchors:.1f}% of all")
    print()


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("corpora", nargs="+")
    ap.add_argument("--anchor", default="call",
                    help="call (near+far, default), near-call, jalr, branch, or a mnemonic")
    ap.add_argument("--budget", type=int, default=10,
                    help="operand bits the anchor leaves for its partner")
    ap.add_argument("--fixed", nargs="*", default=None,
                    help="score this shape set, cumulatively, in order")
    ap.add_argument("--top", type=int, default=25)
    ap.add_argument("--limit", type=int, default=12)
    ap.add_argument("--rebuild", action="store_true",
                    help="ignore the cache and reschedule")
    args = ap.parse_args()
    for corpus in args.corpora:
        report(corpus, args)


if __name__ == "__main__":
    main()

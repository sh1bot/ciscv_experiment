"""
util/rule_overlap.py — how much do the frames overlap?

`util/rule_hits.py` credits each pair to the FIRST rule in `RULES` order that
accepts it, because that is what `find_b_partners` does.  That makes a frame's
hit count an artefact of rule order: a frame late in the list scores only the
pairs no earlier frame wanted.  This measures the bias directly.

Which rule wins is pure labelling — no caller uses the rule identity to make a
scheduling decision, so the schedule and the pair count turn on whether ANY
rule accepts, never on which (see `pairing.all_acceptors`).  Two things follow:

  * co-acceptance can be measured exactly with no re-scheduling, by recording
    every accepting rule at the moment each pair is taken; and
  * a frame whose every hit is co-accepted by ANOTHER frame can be deleted
    with no effect whatsoever — the pairable set is unchanged, so the schedule
    and the pairing come out identical.  `exclusive == 0` is a proof, not an
    estimate.

Per frame:

  hits       pairs credited to it under first-wins (what rule_hits reports)
  reach      pairs it could encode, whoever won them -- its score if it were
             first in RULES order.  reach - hits is what rule order hides.
  excl       pairs where it is the ONLY acceptor -- what it alone buys
  excl/cp    those per codepoint.  THIS is the allocation-efficiency number to
             spend codepoints on; hits/cp flatters whatever sits early in RULES.

`excl` is NOT the cost of deleting the frame when it is nonzero: those pairs
return to the scheduler, which may re-pair the instructions some other way.
It is a good estimate of the loss but not a bound -- greedy list scheduling is
not monotone, so removing an option can cascade either way.  Measuring the
true cost needs a leave-one-out re-run per frame.

Usage:  python3 util/rule_overlap.py [name ...]
"""
import argparse
import importlib.util
import os
import sys
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "util"))

from isa.xlen import detect_xlen
from rule_hits import CORPORA, frames_from_yaml

_DRIVER = None


def driver():
    """`__main__.py` loaded as an importable module.

    Its chunk splitter and worker are the real scheduling path; replicating
    them here would let the overlap measurement drift away from what the
    scheduler actually does.  It cannot be `import __main__` (that is this
    script), so it is loaded by path, once, and cached.
    """
    global _DRIVER
    if _DRIVER is None:
        spec = importlib.util.spec_from_file_location(
            "ciscv_driver", os.path.join(ROOT, "__main__.py"))
        mod = importlib.util.module_from_spec(spec)
        sys.modules["ciscv_driver"] = mod
        spec.loader.exec_module(mod)
        _DRIVER = mod
    return _DRIVER


def chunk_overlap(chunk, xlen):
    """Schedule+pair one chunk with the recorder on; return the tallies.

    Aggregated in the worker: the raw event stream is one entry per pair
    (~500k on the full corpus), while the distinct (winner, acceptor-set)
    combinations number in the hundreds.
    """
    import scheduler.pairing as pairing
    from scheduler.reorder import ScheduleMode

    counts = Counter()

    def sink(winner, acceptors):
        counts[(winner, tuple(sorted(acceptors)))] += 1

    pairing.ACCEPTOR_SINK = sink
    try:
        driver()._process_chunk(chunk, False, ScheduleMode.LIST, None, 0, xlen)
    finally:
        pairing.ACCEPTOR_SINK = None
    return counts


def measure(name):
    """(xlen, Counter[(winner_rule, acceptor_rules)]) for one corpus."""
    path = os.path.join(ROOT, "tests", f"{name}.s")
    source = open(path).read()
    xlen, _ = detect_xlen(source[:8192])
    chunks = driver()._split_source(source)
    total = Counter()
    with ProcessPoolExecutor() as pool:
        for counts in pool.map(chunk_overlap, chunks, [xlen] * len(chunks)):
            total.update(counts)
    return xlen, total


def fold(counts, rule2frame):
    """Re-key rule-level tallies to frames.

    Two rules can share one frame (deref-load-chain / base-load-chain share a
    49-codepoint block), and a frame does not overlap ITSELF: folding before
    the matrix keeps that from reading as redundancy.
    """
    out = Counter()
    for (winner, acceptors), n in counts.items():
        wf = rule2frame[winner]
        af = frozenset(rule2frame[r] for r in acceptors)
        out[(wf, af)] += n
    return out


def report(label, counts, cp, out=sys.stdout):
    hits, reach, excl = Counter(), Counter(), Counter()
    matrix = defaultdict(Counter)
    mult = Counter()
    for (wf, af), n in counts.items():
        hits[wf] += n
        mult[len(af)] += n
        for f in af:
            reach[f] += n
        if len(af) == 1:
            excl[wf] += n
        for f in af:
            if f != wf:
                matrix[wf][f] += n
    total = sum(hits.values()) or 1

    print(f"\n{'=' * 78}\n{label}\n{'=' * 78}", file=out)
    print(f"\n{'frame':34}{'cp':>4}{'hits':>8}{'reach':>8}{'excl':>8}"
          f"{'excl/cp':>9}{'hidden':>8}", file=out)
    print("-" * 78, file=out)
    for frame in sorted(cp, key=lambda f: -(excl[f] / cp[f])):
        hidden = reach[frame] - hits[frame]
        print(f"{frame:34}{cp[frame]:4}{hits[frame]:8}{reach[frame]:8}"
              f"{excl[frame]:8}{excl[frame] / cp[frame]:9.1f}{hidden:8}", file=out)
    print("-" * 78, file=out)
    print(f"{'TOTAL':34}{sum(cp.values()):4}{total:8}{'':8}"
          f"{sum(excl.values()):8}{sum(excl.values()) / sum(cp.values()):9.1f}",
          file=out)

    print(f"\nhow many frames could encode each pair:", file=out)
    for k in sorted(mult):
        print(f"  {k} frame{'s' if k > 1 else ' '}: {mult[k]:8}"
              f"  {100 * mult[k] / total:5.1f}%"
              f"{'   <- only this frame can' if k == 1 else ''}", file=out)
    redundant = total - mult[1]
    print(f"  {redundant} pairs ({100 * redundant / total:.1f}%) are encodable "
          f"by more than one frame.", file=out)

    print(f"\nwho shadows whom — of the pairs each frame WON, which other "
          f"frames\nwould also have taken them (first-wins hides this):", file=out)
    for frame in sorted(matrix, key=lambda f: -hits[f]):
        parts = [f"{g} {n}" for g, n in matrix[frame].most_common(4)]
        if parts:
            print(f"  {frame:32} {', '.join(parts)}", file=out)

    zero = [f for f in cp if excl[f] == 0 and reach[f] > 0]
    if zero:
        print(f"\nfully shadowed — every pair they take is encodable by another\n"
              f"frame, so deleting them changes nothing at all:", file=out)
        for f in zero:
            print(f"  {f:32} {cp[f]:4} codepoints, {hits[f]} hits, "
                  f"{reach[f]} reach", file=out)
    dead = [f for f in cp if reach[f] == 0]
    if dead:
        print(f"\nnever accepted any taken pair: {', '.join(sorted(dead))}", file=out)


def check(name, counts, raw_dir):
    """Assert the instrumented run reproduces util/rule_hits.py's counts.

    The recorder hangs off the two places greedy_pair takes a pair, and a third
    could be added without anyone noticing the overlap numbers had gone quiet
    -- that is exactly how the `_backward_pair` second pass was missed first
    time round.  Comparing per RULE (not per frame) also catches a fold that
    silently drops a rule.
    """
    from rule_hits import parse_tail
    path = os.path.join(raw_dir, f"{name}.txt")
    if not os.path.exists(path):
        return
    _i, _p, pairs, hits = parse_tail(open(path).read())
    mine = Counter()
    for (winner, _acc), n in counts.items():
        mine[winner] += n
    bad = [(r, hits.get(r, 0), mine.get(r, 0))
           for r in set(hits) | set(mine) if hits.get(r, 0) != mine.get(r, 0)]
    if bad or sum(mine.values()) != pairs:
        raise SystemExit(
            f"{name}: instrumented run disagrees with {path}\n"
            f"  total {sum(mine.values())} vs {pairs}\n"
            + "".join(f"  {r}: rule_hits {a}, overlap {b}\n" for r, a, b in bad))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("names", nargs="*")
    ap.add_argument("--check", metavar="DIR",
                    default=os.path.join(ROOT, "results", "corpus", "rule-hits"),
                    help="cross-check hit counts against util/rule_hits.py blocks")
    args = ap.parse_args()

    rule2frame, cp = frames_from_yaml()
    by_base = {32: Counter(), 64: Counter()}
    for name in args.names or CORPORA:
        xlen, counts = measure(name)
        if args.check:
            check(name, counts, args.check)
        print(f"{name:16} RV{xlen}  {sum(counts.values()):7} pairs",
              file=sys.stderr)
        by_base[xlen].update(fold(counts, rule2frame))

    print("# Frame overlap over the corpus.  Generated by util/rule_overlap.py.")
    print("#\n# hits    pairs credited under first-wins (== util/rule_hits.py)")
    print("# reach   pairs the frame could encode, whoever won them")
    print("# excl    pairs where it is the ONLY frame that can")
    print("# hidden  reach - hits: what RULES order hides from the hit count")
    for xlen in (32, 64):
        if by_base[xlen]:
            report(f"RV{xlen}", by_base[xlen], cp)
    if by_base[32] and by_base[64]:
        both = Counter()
        both.update(by_base[32])
        both.update(by_base[64])
        report("RV32 + RV64", both, cp)


if __name__ == "__main__":
    main()

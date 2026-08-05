"""
util/chain_imm_grid.py — the joint (A-offset, B-offset) demand of pointer chases.

Every pointer-chase frame constrains its offsets, so the population it takes is
a sub-region of the A x B plane and asking how wide its fields should be from
that population can only re-derive the region.  This answers the question no
frame can see about itself: over EVERY chain the pairer could form, what is the
joint demand?

It is what showed that the frames of the day -- `deref-load-chain` and a wide
`base-load-chain`, each requiring ONE offset to be zero -- could only ever
reach the axes, leaving a quarter of all chases off them with both offsets
real.  That is the measurement `load5-load5-chain` exists because of.  The
census is deliberately frame-agnostic, so it stays valid as the frames change.

METHOD.  The two chain rules are replaced by ONE permissive rule that keeps all
three structural gates -- must_chain_base (B's base IS A's loaded value),
no_escape (the temporary dies in the packet) and a_base_not_from_auipc -- and
drops only the offset conditions.  Reusing the real decorators rather than
re-deriving the structure is deliberate: a hand-rolled predicate is how a
census stops describing the pairer.  The permissive rule sits at the index the
first chain rule had, so attribution against every other frame is unchanged.

Required bits are counted the way `uimm_fits` counts them: the offset is
width-scaled (`imm >> access_shift`), must be a multiple of the access width,
and must be non-negative -- an unsigned field cannot hold a negative offset at
any width.  Offsets that fail those two structural tests are reported in their
own rows, because no choice of width admits them.

Usage:  python3 util/chain_imm_grid.py [--corpora a,b] [--max-bits 12]
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

PERMISSIVE = "chain-any"
UNALIGNED, NEGATIVE = -1, -2


def req_bits(insn):
    """Bits an unsigned width-scaled field needs, or UNALIGNED / NEGATIVE."""
    if insn.imm is None:
        return 0
    shift = insn.access_shift or 0
    if insn.imm < 0:
        return NEGATIVE
    if not insn.imm_multiple(shift):
        return UNALIGNED
    v = insn.imm >> shift
    return v.bit_length()          # 0 for a zero offset


def install_permissive():
    """Swap the two chain rules for one that gates structure but not offsets."""
    import scheduler.rules as rules
    from scheduler.rules import PairingRule, NotPair

    # A worker handles many chunks and this runs per chunk; the swap must be
    # idempotent or the second call finds the chain rules already gone.
    if any(r.name == PERMISSIVE for r in rules.RULES):
        return

    @rules.must_chain_base
    @rules.no_escape
    @rules.a_base_not_from_auipc
    def _any_chain(a, b):
        if a.rbase is None or a.rd is None:
            raise NotPair("A missing base/dest register")
        return None

    # Every load->load chain rule, whatever the frames are currently called:
    # naming them individually is how this went stale last time, leaving a live
    # chain rule in place beside the permissive one.
    chain = {r.name for r in rules.RULES
             if r.check.__name__ in ("_load0_load10_chain", "_load5_load5_chain")}
    if not chain:
        raise SystemExit("no load->load chain rules found — has rules.py moved on?")
    idx = min(i for i, r in enumerate(rules.RULES) if r.name in chain)
    kept = [r for r in rules.RULES if r.name not in chain]
    kept.insert(idx, PairingRule(name=PERMISSIVE,
                                 a_mnemonic_set=rules._CHAIN_LOAD_MN,
                                 b_mnemonic_set=rules._CHAIN_LOAD_MN,
                                 check=_any_chain))
    rules.RULES[:] = kept


def chunk_grid(args):
    chunk, xlen = args
    from scheduler.reorder import ScheduleMode
    install_permissive()
    counts = Counter()
    for tag, packets in driver()._process_chunk(chunk, False, ScheduleMode.LIST,
                                                None, 0, xlen):
        if tag == "pad_nops":
            continue
        for item in packets:
            if item[0] == "pair" and item[3] == PERMISSIVE:
                a, b = item[1], item[2]
                counts[(req_bits(a), req_bits(b), a.rbase == 2)] += 1
    return counts


def measure(names):
    by_base = {32: Counter(), 64: Counter()}
    for name in names:
        source = open(os.path.join(ROOT, "tests", f"{name}.s")).read()
        xlen, _ = detect_xlen(source[:8192])
        chunks = driver()._split_source(source)
        with ProcessPoolExecutor() as pool:
            for c in pool.map(chunk_grid, [(c, xlen) for c in chunks]):
                by_base[xlen].update(c)
        print(f"  {name}", file=sys.stderr)
    return by_base


def label(k):
    return {UNALIGNED: "unal", NEGATIVE: "neg"}.get(k, str(k))


def grid(counts, maxb, title, out=sys.stdout):
    """A-bits down, B-bits across."""
    total = sum(counts.values()) or 1
    keys = [NEGATIVE, UNALIGNED] + list(range(0, maxb + 1))
    present_a = [k for k in keys if any(a == k for a, _b, _s in counts)]
    present_b = [k for k in keys if any(b == k for _a, b, _s in counts)]

    print(f"\n{title}  ({total} chains)", file=out)
    print("        B offset bits ->", file=out)
    print("  A     " + "".join(f"{label(k):>7}" for k in present_b)
          + f"{'row':>9}", file=out)
    print("  " + "-" * (6 + 7 * len(present_b) + 9), file=out)
    for ka in present_a:
        cells = []
        rowtot = 0
        for kb in present_b:
            n = sum(v for (a, b, _s), v in counts.items()
                    if a == ka and b == kb)
            rowtot += n
            cells.append(f"{n:>7}" if n else f"{'.':>7}")
        print(f"  {label(ka):<6}" + "".join(cells) + f"{rowtot:>9}", file=out)
    print("  " + "-" * (6 + 7 * len(present_b) + 9), file=out)
    coltot = []
    for kb in present_b:
        coltot.append(sum(v for (_a, b, _s), v in counts.items() if b == kb))
    print("  col   " + "".join(f"{n:>7}" for n in coltot)
          + f"{total:>9}", file=out)

    on_a = sum(v for (a, b, _s), v in counts.items() if b == 0 and a >= 0)
    on_b = sum(v for (a, b, _s), v in counts.items() if a == 0 and b >= 0)
    both0 = sum(v for (a, b, _s), v in counts.items() if a == 0 and b == 0)
    off = sum(v for (a, b, _s), v in counts.items() if a > 0 and b > 0)
    bad = sum(v for (a, b, _s), v in counts.items() if a < 0 or b < 0)
    print(f"\n  on the A axis (B offset zero) : {on_a:7}  {100*on_a/total:5.1f}%"
          f"   <- offset on the FIRST load only", file=out)
    print(f"  on the B axis (A offset zero) : {on_b:7}  {100*on_b/total:5.1f}%"
          f"   <- load0-load10-chain's population", file=out)
    print(f"    of which both offsets zero  : {both0:7}  {100*both0/total:5.1f}%"
          f"   <- counted in both axes above", file=out)
    print(f"  OFF the axes (both nonzero)   : {off:7}  {100*off/total:5.1f}%"
          f"   <- load5-load5-chain's population", file=out)
    print(f"  structurally unencodable      : {bad:7}  {100*bad/total:5.1f}%"
          f"   <- negative or unaligned offset", file=out)


def sp_split(counts, title, out=sys.stdout):
    """Is sp-as-base a distinct population?"""
    print(f"\n{title} — rs1a = sp?", file=out)
    for is_sp in (True, False):
        sub = {k: v for k, v in counts.items() if k[2] is is_sp}
        tot = sum(sub.values())
        if not tot:
            continue
        off = sum(v for (a, b, _s), v in sub.items() if a > 0 and b > 0)
        on_a = sum(v for (a, b, _s), v in sub.items() if b == 0 and a > 0)
        on_b = sum(v for (a, b, _s), v in sub.items() if a == 0 and b > 0)
        wide_a = sum(v for (a, _b, _s), v in sub.items() if a > 10)
        wide_b = sum(v for (_a, b, _s), v in sub.items() if b > 10)
        print(f"  rs1a {'== sp' if is_sp else '!= sp'}: {tot:7} chains"
              f"   A-only {on_a:6}  B-only {on_b:6}  both {off:6}"
              f"   >10 bits: A {wide_a}, B {wide_b}", file=out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--corpora", default=None)
    ap.add_argument("--max-bits", type=int, default=12)
    args = ap.parse_args()
    names = args.corpora.split(",") if args.corpora else CORPORA

    by_base = measure(names)
    print("# Joint (A-offset, B-offset) demand over every chain the pairer can")
    print("# form, with the offset conditions of the two chain frames removed.")
    print("# Cells are pair counts. `neg`/`unal` are offsets no unsigned")
    print("# width-scaled field can hold, whatever its width.")
    both = Counter()
    for base in (32, 64):
        if by_base[base]:
            grid(by_base[base], args.max_bits, f"=== RV{base} ===")
            sp_split(by_base[base], f"RV{base}")
            both.update(by_base[base])
    if both:
        grid(both, args.max_bits, "=== RV32 + RV64 ===")
        sp_split(both, "RV32 + RV64")


if __name__ == "__main__":
    main()

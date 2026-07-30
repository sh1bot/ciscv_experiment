"""analysis/zicond_select.py — how the four-instruction Zicond select splits into
packets.

The idiom the compilers emit for a conditional move is

    <setup>    rc, ...          # condition into rc
    czero.eqz  t0, v0, rc
    czero.nez  t1, v1, rc
    or         rd, t0, t1

`results/corpus/FINDINGS.md` §4 candidate 4 (`czero-select-or`) captures the back
half only — `(czero.nez, or)` — leaving the setup and the first czero solo.  The
question this measures is whether the *front* half pairs too, as
`(setup, czero.eqz)`, so the whole idiom goes out as two packets:

    packet A: setup rc, ...     ; czero.eqz t0, v0, rc
    packet B: czero.nez t1, v1, rc ; or rd, t0, t1

Two things decide that, and both are measured here:

  adjacency   the four have to be contiguous in that order (nothing between),
              or the reorderer has to be able to make them so.  Compilers
              routinely put the `li` that materialises a select arm between
              them.
  field count `rc` and `t0` both cross the packet boundary, so neither can be
              an unencoded chain temp.  Packet A must draw rc, the setup's own
              sources, and the czero's v0/t0 — five 5-bit fields, one more than
              the grid has, unless the setup is unary or the czero is RSD form
              (`t0 == v0`).

So each site is classified by shape *and* by how many register fields its two
packets would have to draw.  The front half also has two rival partners for the
same slot — the `li` that feeds `v0`, and a preceding unrelated instruction —
so `--realize` re-measures with the real greedy pairer to see which partner the
scheduler actually takes and what the net pair delta is.

Usage:  python3 -m analysis.zicond_select [--realize] [--sample N] [corpus.s ...]
"""

from __future__ import annotations
import os
import sys
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from analysis.parser import parse_file
from analysis.liveness import compute_global_liveness, compute_local_liveness

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TESTS = os.path.join(ROOT, "tests")

CZERO = frozenset({"czero.eqz", "czero.nez"})
# The `or` is the canonical combiner; add/xor also work on disjoint arms, so
# they are matched and reported separately rather than assumed absent.
COMBINERS = frozenset({"or", "add", "addw", "xor"})

# Packet budget: the grid has exactly four 5-bit register columns (funct5, rs2,
# rs1, rd).  Those 20 bits plus the 2-bit marker leave 10 — opcode5:funct3:g:h,
# the 1024-codepoint namespace — for the prefix code and op selection.
#
# A fifth register field is not impossible, it is priced out: it has to eat
# funct3+g+h, so each (opA, opB) combination claims a 5-bit prefix, i.e. 32 of
# the 1024 codepoints.  Two czero variants alone would be 64, against 130 spare
# — where candidate 4's whole frame costs 2.
MAX_FIELDS = 4


# ---------------------------------------------------------------------------
# idiom detection
# ---------------------------------------------------------------------------

def _def_site(ins: list, k: int, reg: int):
    """Index of the last write to `reg` strictly before k, or None."""
    for j in range(k - 1, -1, -1):
        if ins[j].rd == reg:
            return j
    return None


def find_selects(ins: list) -> list:
    """Return the select idioms in a block as (i0, i1, k, rc) index tuples.

    i0/i1 are the two czero instructions in program order, k the combiner.  A
    select is a combiner whose two sources are written by a complementary
    czero.eqz/czero.nez pair testing the same condition register.
    """
    out = []
    for k, comb in enumerate(ins):
        if comb.mnemonic not in COMBINERS:
            continue
        if comb.rs1 is None or comb.rs2 is None or comb.rs1 == comb.rs2:
            continue
        j1 = _def_site(ins, k, comb.rs1)
        j2 = _def_site(ins, k, comb.rs2)
        if j1 is None or j2 is None:
            continue
        a, b = ins[j1], ins[j2]
        if {a.mnemonic, b.mnemonic} != CZERO:
            continue
        # czero.{eqz,nez} rd, rs1, rs2 — rs2 is the condition.
        if a.rs2 is None or a.rs2 != b.rs2:
            continue
        out.append((min(j1, j2), max(j1, j2), k, a.rs2))
    return out


# ---------------------------------------------------------------------------
# field accounting
# ---------------------------------------------------------------------------

def _src_fields(insn) -> int:
    """Register fields the setup's own sources need.

    x0 sources are free: the pseudo spelling names them in the opcode
    (`snez rc,r` is `sltu rc,x0,r`, `seqz` is `sltiu rc,r,1`), so a frame can
    reach them with a codepoint instead of a field.  The parser has already
    canonicalised those pseudos, which is why this has to be counted rather
    than read off the mnemonic.
    """
    return len({r for r in (insn.rs1, insn.rs2) if r})


def _needs_imm(insn) -> bool:
    """True if the setup carries an immediate the packet would have to hold.

    imm == 0 is free — it re-encodes as the register form (`addi rd,rs,0` is
    `add rd,rs,x0`), the aliasing convention the existing frames already use.
    An unresolved immediate (`%lo(sym)`) never fits.
    """
    if insn.imm_expr is not None:
        return True
    return insn.imm is not None and insn.imm != 0


def front_fields(setup, cz) -> int:
    """Register fields packet A (`setup rc,... ; czero t0, v0, rc`) must draw.

    rc is written by A and read by B, but it is *not* dead at the packet
    boundary — the second czero reads it — so it is a real field, not a chain
    temp.  t0 likewise survives into packet B.
    """
    n = 1 + _src_fields(setup)              # rc + the setup's sources
    n += 1 if cz.rd == cz.rs1 else 2        # RSD czero shares v0/t0
    return n


def arm_fields(arm, cz) -> int:
    """Register fields packet A needs when the first czero's A-slot partner is the
    instruction that materialises its *arm value* rather than the condition:

        li v0, imm  ;  czero.eqz t0, v0, rc

    v0 dies at the czero (or is overwritten by it, the RSD case), so it is the
    unencoded chain temp — this is chain-alu-pair's shape with a czero in the
    B slot.  What must be drawn is rc, t0, and the arm op's own sources.
    """
    return 2 + _src_fields(arm)


def back_fields(cz, comb) -> int:
    """Register fields packet B (`czero t1, v1, rc ; or rd, t0, t1`) must draw.

    t1 costs nothing either way: it is produced and consumed inside the packet
    and dies there (the unencoded chain temp), or the czero is RSD form and it
    coincides with v1, whose field exists anyway.
    """
    n = 3                                   # v1, rc, and t0
    if comb.rd not in (comb.rs1, comb.rs2):
        n += 1                              # a separate destination
    return n


def imm_room(insn, fields: int) -> bool:
    """Does the A slot's immediate fit the columns its registers leave free?

    Each 5-bit column the packet does not spend on a register can hold immediate
    bits at no codepoint cost; a bit borrowed beyond that comes out of the 10-bit
    op namespace and doubles the frame's cost (`chain_alu`'s 6-bit `addi` is the
    worked example — 5 column bits plus 1 borrowed, priced at 2 codepoints).
    So the free width is 5 bits per spare column, width-scaled for memory
    offsets per ACCOUNTING §6.
    """
    if not _needs_imm(insn):
        return True
    if insn.imm_expr is not None or fields >= MAX_FIELDS:
        return False
    bits = 5 * (MAX_FIELDS - fields)
    shift = insn.access_shift or 0
    return (insn.uimm_fits(bits, shift) if insn.access_shift is not None
            else insn.imm_fits(bits))


def _dead_after(reg: int, insn) -> bool:
    return reg not in insn.live_out


# ---------------------------------------------------------------------------
# per-corpus measurement
# ---------------------------------------------------------------------------

class Stats:
    def __init__(self, name):
        self.name = name
        self.insns = 0
        self.czero = Counter()
        self.czero_rsd = 0
        self.selects = 0
        self.by_combiner = Counter()
        self.shape = Counter()
        self.setup_op = Counter()
        self.setup_op_ok = Counter()      # only the sites the frame could take
        self.setup_fields = Counter()
        self.front_ok = 0            # front half adjacent AND fits the budget
        self.back_ok = 0             # back half adjacent AND fits the budget
        self.both_ok = 0
        self.arm_ok = 0              # front half via the arm-value chain instead
        self.front_any = 0           # front half by either partner
        self.both_any = 0
        self.rc_origin = Counter()
        self.rc_fanout = Counter()
        self.rc_dead = Counter()
        self.setup_fields_fixed_rc = Counter()
        self.czero_shape = Counter()
        self.comb_shape = Counter()
        # ACCOUNTING §6: a distribution one function owns is not evidence.
        self.by_function = Counter()
        self.front_rival = Counter()
        self.arm_op = Counter()
        self.arm_op_ok = Counter()
        self.arm_fields = Counter()
        self.low_regs = Counter()
        self.samples = []


def measure(path: str, stats: Stats, sample: int = 0) -> None:
    _blocks, fns = parse_file(open(path).read())
    for fn in fns:
        fn_name = fn.name or "(unknown)"
        gl = compute_global_liveness(fn.blocks)
        for bl in fn.blocks:
            ins = bl.instructions
            if not ins:
                continue
            compute_local_liveness(bl, gl)
            stats.insns += len(ins)
            for i in ins:
                if i.mnemonic in CZERO:
                    stats.czero[i.mnemonic] += 1
                    if i.rd == i.rs1:
                        stats.czero_rsd += 1
            for (i0, i1, k, rc) in find_selects(ins):
                stats.by_function[fn_name] += 1
                _score_site(ins, i0, i1, k, rc, stats, sample)


def _score_site(ins, i0, i1, k, rc, stats, sample) -> None:
    cz0, cz1, comb = ins[i0], ins[i1], ins[k]
    stats.selects += 1
    stats.by_combiner[comb.mnemonic] += 1

    # Both czeros in RSD form is what would make a [czero, czero] frame cheap —
    # t0/v0 and t1/v1 collapse, so the pair draws three fields, not five.
    n_rsd = (cz0.rd == cz0.rs1) + (cz1.rd == cz1.rs1)
    stats.czero_shape[("both RSD", "one RSD", "neither RSD")[2 - n_rsd]] += 1
    stats.comb_shape["RSD (rd is one of the arms)" if comb.rd in (comb.rs1, comb.rs2)
                     else "separate destination"] += 1

    czeros_adj = (i1 == i0 + 1)
    back_adj = (k == i1 + 1)
    setup = _def_site(ins, i0, rc)
    front_adj = setup is not None and setup == i0 - 1

    # How the condition register arrived, and whether it serves this select alone.
    if setup is None:
        stats.rc_origin["block entry"] += 1
    elif front_adj:
        stats.rc_origin["adjacent"] += 1
    else:
        stats.rc_origin[f"{i0 - setup} back"] += 1
    stats.rc_fanout[sum(1 for x in ins if x.mnemonic in CZERO and x.rs2 == rc)] += 1
    # If the condition dies with the second czero, it could live in a fixed
    # architectural register instead of an encoded field — the cross-packet
    # equivalent of the chain temp (TODO decision 4).  That is the only thing
    # that makes a three-operand setup affordable, so it is counted separately.
    stats.rc_dead["dies with the select" if _dead_after(rc, ins[i1])
                  else "live past the select"] += 1

    # What the front czero's own predecessor is, when it is not the setup: the
    # rival for that slot is the instruction that materialises the select arm.
    if not front_adj and i0 > 0:
        prev = ins[i0 - 1]
        if prev.rd is not None and prev.rd == cz0.rs1:
            stats.front_rival["arm value (li/other)"] += 1
        else:
            stats.front_rival["unrelated"] += 1

    # Shape, as emitted.
    shape = ("4-contiguous" if (front_adj and czeros_adj and back_adj)
             else "back only" if back_adj
             else "czeros adjacent" if czeros_adj
             else "scattered")
    stats.shape[shape] += 1

    # Encodability of each half.
    back_fits = back_fields(cz1, comb) <= MAX_FIELDS and (
        cz1.rd == comb.rd or _dead_after(cz1.rd, comb))
    if back_adj and back_fits:
        stats.back_ok += 1

    front_fits = False
    if setup is not None:
        stats.setup_op[setup_label(ins[setup])] += 1
        f = front_fields(ins[setup], cz0)
        stats.setup_fields[f if not _needs_imm(ins[setup]) else f"{f}+imm"] += 1
        if _dead_after(rc, ins[i1]):
            stats.setup_fields_fixed_rc[
                f - 1 if not _needs_imm(ins[setup]) else f"{f - 1}+imm"] += 1
        front_fits = f <= MAX_FIELDS and not _needs_imm(ins[setup])
        if front_adj and front_fits:
            stats.front_ok += 1
            stats.setup_op_ok[setup_label(ins[setup])] += 1
            if back_adj and back_fits and czeros_adj:
                stats.both_ok += 1
                if len(stats.samples) < sample:
                    stats.samples.append(
                        [ins[j].raw.strip() for j in (setup, i0, i1, k)])

    # The rival for the same A slot: the arm-value chain.  It does not need the
    # condition to be adjacent, only the value the czero masks.
    arm = _def_site(ins, i0, cz0.rs1) if cz0.rs1 else None
    arm_ok = False
    if arm is not None:
        stats.arm_op[setup_label(ins[arm])] += 1
        af = arm_fields(ins[arm], cz0)
        stats.arm_fields[af if not _needs_imm(ins[arm]) else f"{af}+imm"] += 1
        arm_dies = cz0.rd == cz0.rs1 or _dead_after(cz0.rs1, cz0)
        # Unlike the condition chain, this shape often has a spare column for an
        # immediate — `li` draws only two registers — so the immediate is checked
        # against the room its register count leaves rather than banned outright.
        arm_ok = (arm == i0 - 1 and arm_dies and af <= MAX_FIELDS
                  and imm_room(ins[arm], af))
        if arm_ok:
            stats.arm_ok += 1
            stats.arm_op_ok[setup_label(ins[arm])] += 1
            # Could chain-alu-pair host it as-is?  Its @chain_uses_low_regs
            # confines every encoded register to x0..x15.
            encoded = [r for r in (cz0.rd, cz0.rs2) if r is not None]
            stats.low_regs["x0-x15" if all(r < 16 for r in encoded)
                           else "needs the full 5-bit field"] += 1

    if (front_adj and front_fits) or arm_ok:
        stats.front_any += 1
        if back_adj and back_fits and czeros_adj:
            stats.both_any += 1


def setup_label(insn) -> str:
    """Mnemonic plus the pseudo shape the operands imply."""
    m = insn.mnemonic
    if m == "addi" and insn.rs1 == 0:
        return "li"
    if m == "addi" and insn.imm == 0:
        return "mv"
    if m == "sltu" and insn.rs1 == 0:
        return "snez"
    if m == "sltiu" and insn.imm == 1:
        return "seqz"
    if m == "slt" and insn.rs2 == 0:
        return "sltz"
    if m == "slt" and insn.rs1 == 0:
        return "sgtz"
    return m


# ---------------------------------------------------------------------------
# reporting
# ---------------------------------------------------------------------------

def pct(n, d):
    return f"{100.0 * n / d:5.1f}%" if d else "    — "


def report(all_stats: list) -> None:
    print(f"\n{'corpus':14}{'insns':>8}{'czero':>7}{'RSD':>7}{'selects':>8}"
          f"{'in sel':>7}{'4-cont':>8}{'back':>7}{'front':>7}{'both':>7}"
          f"{'arm':>7}{'front*':>8}{'both*':>7}")
    print("-" * 97)
    for s in all_stats:
        cz = sum(s.czero.values())
        print(f"{s.name:14}{s.insns:>8}{cz:>7}{pct(s.czero_rsd, cz):>7}"
              f"{s.selects:>8}{pct(2 * s.selects, cz):>7}"
              f"{pct(s.shape['4-contiguous'], s.selects):>8}"
              f"{pct(s.back_ok, s.selects):>7}"
              f"{pct(s.front_ok, s.selects):>7}{pct(s.both_ok, s.selects):>7}"
              f"{pct(s.arm_ok, s.selects):>7}{pct(s.front_any, s.selects):>8}"
              f"{pct(s.both_any, s.selects):>7}")
    print("-" * 97)
    print("  in sel  = czeros that belong to a select (2 per site) / all czeros")
    print("  4-cont  = setup,czero,czero,combiner contiguous in that order")
    print("  back    = (czero, or) adjacent AND fits 4 register fields  [candidate 4]")
    print("  front   = (setup, czero) adjacent AND fits 4 fields")
    print("  both    = front and back both fire — the select is two packets")
    print("  arm     = (arm value, czero) adjacent instead: the rival A-slot partner")
    print("  front*/both* = front half by either partner")

    pooled = Stats("POOLED")
    for s in all_stats:
        pooled.insns += s.insns
        pooled.czero += s.czero
        pooled.czero_rsd += s.czero_rsd
        pooled.selects += s.selects
        for attr in ("by_combiner", "shape", "setup_op", "setup_fields",
                     "rc_origin", "rc_fanout", "rc_dead", "front_rival",
                     "setup_fields_fixed_rc", "arm_op", "arm_op_ok",
                     "setup_op_ok", "arm_fields",
                     "low_regs", "czero_shape", "comb_shape", "by_function"):
            getattr(pooled, attr).update(getattr(s, attr))
        pooled.front_ok += s.front_ok
        pooled.back_ok += s.back_ok
        pooled.both_ok += s.both_ok
        pooled.arm_ok += s.arm_ok
        pooled.front_any += s.front_any
        pooled.both_any += s.both_any

    def hist(title, counter, note=""):
        n = sum(counter.values())
        print(f"\n{title}  (n={n}){note}")
        for key, c in counter.most_common(12):
            print(f"    {str(key):24}{c:>8}{pct(c, n):>8}")

    hist("combiner", pooled.by_combiner)
    hist("czero form within a select", pooled.czero_shape,
         "  — both RSD makes a [czero, czero] frame 3 fields, not 5")
    hist("combiner form", pooled.comb_shape)
    hist("shape as emitted", pooled.shape)
    hist("condition register origin", pooled.rc_origin,
         "  — distance from the first czero")
    hist("czeros reading the same condition register", pooled.rc_fanout,
         "  — 2 = private to this select")
    hist("setup op", pooled.setup_op)
    hist("setup op, sites the frame could actually take", pooled.setup_op_ok,
         "  — this is the A-slot op list to price")
    hist("front-half register fields needed", pooled.setup_fields,
         f"  — budget is {MAX_FIELDS}")
    hist("condition register lifetime", pooled.rc_dead)
    hist("front-half fields if the condition sat in a fixed register",
         pooled.setup_fields_fixed_rc, "  — only the sites where it dies")
    hist("what precedes the first czero when the setup does not",
         pooled.front_rival)
    hist("top functions by select count", pooled.by_function,
         "  — ACCOUNTING §6 concentration check")
    hist("arm-value op (the rival A-slot partner)", pooled.arm_op)
    hist("arm-value op, sites the frame could actually take", pooled.arm_op_ok,
         "  — this is the A-slot op list to price")
    hist("arm-chain register fields needed", pooled.arm_fields,
         f"  — budget is {MAX_FIELDS}")
    hist("arm-chain encoded registers vs chain-alu-pair's x0-x15 window",
         pooled.low_regs)

    print(f"\npooled: {pooled.selects} selects, "
          f"back half {pct(pooled.back_ok, pooled.selects).strip()}, "
          f"front half {pct(pooled.front_ok, pooled.selects).strip()}, "
          f"both {pct(pooled.both_ok, pooled.selects).strip()}; "
          f"front by either partner {pct(pooled.front_any, pooled.selects).strip()}, "
          f"both {pct(pooled.both_any, pooled.selects).strip()}")
    print(f"pairs at the ceiling: candidate 4 alone {pooled.back_ok}, "
          f"+ condition front half {pooled.back_ok + pooled.front_ok}, "
          f"+ either front half {pooled.back_ok + pooled.front_any}")

    for s in all_stats:
        for smp in s.samples:
            print(f"\n  {s.name}")
            for line in smp:
                print(f"    {line}")


def main(argv: list) -> int:
    sample = 0
    realize = False
    paths = []
    it = iter(argv)
    for arg in it:
        if arg == "--sample":
            sample = int(next(it))
        elif arg == "--realize":
            realize = True
        else:
            paths.append(arg)
    if not paths:
        paths = [os.path.join(TESTS, f) for f in sorted(os.listdir(TESTS))
                 if f.endswith(".s") and not f.endswith("-noalias.s")]

    all_stats = []
    for path in paths:
        name = os.path.basename(path)[:-2]
        s = Stats(name)
        measure(path, s, sample)
        all_stats.append(s)
        print(f"  {name}: {s.selects} selects", file=sys.stderr)
    report(all_stats)

    if realize:
        from analysis.zicond_realize import realize_all
        realize_all(paths)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

"""
analysis/encoding_budget.py — per-frame opcode codepoint budget from the corpus.

For every pairing rule, scan corpus adjacencies the rule accepts and measure the
demand its opcode field must satisfy under the g/h variable-length scheme:

  * the distinct (opA, opB) operation combinations that actually occur, ranked
    by frequency  ->  how many opcode "leaves" cover 90/95/99% of real pairs;
  * the A-side / B-side immediate-width distribution  ->  how many g/h bits the
    immediate VLC must supply, and for what fraction of pairs.

The opcode namespace shared by all frames is opcode5(5) + funct3(3) = 256 base
leaves, each splittable by g,h into up to 4 sub-leaves for wide immediates
(1024 max).  Summing "leaves for 95% coverage" over all frames answers whether
the whole encoding fits.

Usage:  python3 -m analysis.encoding_budget file1.s [file2.s ...]
"""
import sys, os, math
from collections import Counter, defaultdict

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from analysis.parser import parse_file
from analysis.liveness import compute_global_liveness, compute_local_liveness
from scheduler.pairing import stamp_slot_eligibility
from scheduler.rules import RULES, NotPair

from analysis.imm_traits import SIGNED as IMM_SIGNED   # single source of truth
SHIFT_MN     = {"slli", "srli", "srai", "slliw", "srliw", "sraiw"}


def subform(insn):
    """Split addi into its pseudo-op subforms so immediate semantics are clean."""
    m = insn.mnemonic
    if m == "addi":
        if insn.rs1 == 0:      return "li"
        if insn.imm == 0:      return "mv"
        # rd == rs1 MUST be tested before the sp check: `addi sp, sp, -32` is a
        # frame adjust (a read-modify-write of sp, belonging to prologue-pair /
        # epilogue-pair), not an addi4spn.  Testing rs1 == 2 first swept every
        # frame adjust into addi4spn -- 24% of that population, and the source
        # of its apparent negative immediates and its 800-byte "offset cluster".
        if insn.rd == insn.rs1: return "addi_rsd"
        if insn.rs1 == 2:      return "addi4spn"
        return "addi_other"
    return m


def signed_bits(v):
    n = 1
    while not (-(1 << (n - 1)) <= v <= (1 << (n - 1)) - 1):
        n += 1
    return n


def unsigned_bits(v):
    n = 0
    while v >= (1 << n):
        n += 1
    return max(n, 1)


def imm_width(insn):
    if insn.imm is None:
        return None
    if insn.imm == 0:
        return 0
    if subform(insn) in IMM_SIGNED:
        return signed_bits(insn.imm)
    if insn.mnemonic in SHIFT_MN:
        return unsigned_bits(insn.imm)
    # memory offset: width-scaled significant bits
    shift = insn.access_shift or 0
    if insn.imm % (1 << shift) == 0:
        return unsigned_bits(abs(insn.imm) >> shift)
    return unsigned_bits(abs(insn.imm))


def eligible(rule, a, b):
    if rule.a_mnemonic_set and a.mnemonic not in rule.a_mnemonic_set: return False
    if rule.b_mnemonic_set and b.mnemonic not in rule.b_mnemonic_set: return False
    if not all(getattr(a, p) for p in rule.a_prerequisites): return False
    if not all(getattr(b, p) for p in rule.b_prerequisites): return False
    return True


def rule_ok(rule, a, b):
    try:
        rule.check(a, b); return True
    except NotPair:
        return False
    except Exception:
        return False


def _regs(insn):                           # non-None, non-x0 register operands
    return [r for r in (insn.rd, insn.rs1, insn.rs2) if r not in (None, 0)]


def collect(paths):
    cooc   = defaultdict(Counter)          # rule -> Counter[(opA,opB)]
    widthA = defaultdict(Counter)          # rule -> Counter[bits]
    widthB = defaultdict(Counter)
    nmatch = Counter()                     # rule -> matches
    nlow   = Counter()                     # rule -> matches with all regs <= x15
    for path in paths:
        with open(path) as f:
            src = f.read()
        _b, fns = parse_file(src)
        for fn in fns:
            for bl in fn.blocks:
                stamp_slot_eligibility(bl.instructions)
        for fn in fns:
            gl = compute_global_liveness(fn.blocks)
            for bl in fn.blocks:
                if not bl.instructions: continue
                compute_local_liveness(bl, gl)
                ins = bl.instructions
                for i in range(len(ins) - 1):
                    a, b = ins[i], ins[i + 1]
                    if not (a.a_slot_ok and b.b_slot_ok): continue
                    for rule in RULES:
                        if not eligible(rule, a, b): continue
                        if not rule_ok(rule, a, b): continue
                        cooc[rule.name][(subform(a), subform(b))] += 1
                        wa, wb = imm_width(a), imm_width(b)
                        if wa is not None: widthA[rule.name][wa] += 1
                        if wb is not None: widthB[rule.name][wb] += 1
                        nmatch[rule.name] += 1
                        if all(r < 16 for r in _regs(a) + _regs(b)):
                            nlow[rule.name] += 1
                        break          # first accepting rule, greedy-style
    return cooc, widthA, widthB, nmatch, nlow


def leaves_for(counter, frac):
    """Ranked distinct (opA,opB) leaves covering `frac` of total mass."""
    total = sum(counter.values())
    if total == 0: return 0, 0
    cum, n = 0, 0
    for _, c in counter.most_common():
        cum += c; n += 1
        if cum >= frac * total:
            break
    return n, total


def pctile_bits(counter, frac):
    total = sum(counter.values())
    if total == 0: return 0
    cum = 0
    for bits in sorted(counter):
        cum += counter[bits]
        if cum >= frac * total:
            return bits
    return max(counter) if counter else 0


def main():
    paths = sys.argv[1:] or ["tests/godot.s"]
    paths = [p if os.path.isabs(p) else os.path.join(os.path.dirname(__file__), "..", p) for p in paths]
    cooc, widthA, widthB, nmatch, nlow = collect(paths)

    print(f"# Encoding budget over: {', '.join(os.path.basename(p) for p in paths)}\n")
    header = f"{'rule':24} {'match':>6} {'pairs':>5} {'L90':>4} {'L95':>4} {'L99':>4} " \
             f"{'bits95':>6} {'immA95':>6} {'immB95':>6}"
    print(header)
    print("-" * len(header))

    order = [r.name for r in RULES]
    tot_L90 = tot_L95 = tot_L99 = 0
    wide_imm = []
    for name in order:
        c = cooc.get(name)
        if not c: continue
        L90, total = leaves_for(c, 0.90)
        L95, _     = leaves_for(c, 0.95)
        L99, _     = leaves_for(c, 0.99)
        bits95     = math.ceil(math.log2(L95)) if L95 else 0
        iA = pctile_bits(widthA.get(name, Counter()), 0.95)
        iB = pctile_bits(widthB.get(name, Counter()), 0.95)
        tot_L90 += L90; tot_L95 += L95; tot_L99 += L99
        # a 5-bit base field + g + h reaches 7 bits; beyond that needs a
        # dedicated wide (e.g. sp-relative) variant or loses coverage.
        if iA > 7 or iB > 7:
            wide_imm.append((name, iA, iB))
        print(f"{name:24} {total:6} {len(c):5} {L90:4} {L95:4} {L99:4} "
              f"{bits95:6} {iA:6} {iB:6}")

    print("-" * len(header))
    print(f"{'TOTAL (sum over frames)':24} {'':>6} {'':>5} {tot_L90:4} {tot_L95:4} {tot_L99:4}")
    print(f"\nOpcode namespace: opcode5(5)+funct3(3) = 256 base leaves "
          f"(x4 via g,h = 1024 max).")
    for frac, tot in (("90%", tot_L90), ("95%", tot_L95), ("99%", tot_L99)):
        verdict = "FITS base-256" if tot <= 256 else ("FITS 1024 w/ g,h" if tot <= 1024 else "OVER")
        print(f"  sum leaves @ {frac} coverage = {tot:4}  -> {verdict}")
    print(f"\nFrames whose immediate demand (p95) exceeds a 5-bit+g+h (7-bit) field")
    print(f"  -> need a dedicated wide/sp-relative immediate variant:")
    for name, iA, iB in wide_imm:
        print(f"    {name:24} immA95={iA} immB95={iB}")

    print(f"\nRegister-field pressure (share of matches with all regs <= x15):")
    print(f"  {'<95% -> a 4-bit register cut would cost real coverage'}")
    for name in order:
        if not nmatch[name]: continue
        frac = 100 * nlow[name] / nmatch[name]
        flag = "" if frac > 99.5 else ("  <- 4-bit cut costly" if frac < 95 else "  <- minor")
        print(f"    {name:24} {frac:5.1f}%{flag}")


if __name__ == "__main__":
    main()

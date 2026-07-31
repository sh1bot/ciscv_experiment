"""What immediate width does each frame's slot actually NEED?

Runs the real pipeline and, for every scheduled pair, records the width the
encoded field would have to be: memory offsets divided by their access width
(the field is width-scaled), addi4spn by 4, everything else as-is. Branch and
jump displacements are unresolved labels in the corpus and are skipped, not
guessed.
"""
import collections, os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from analysis.parser import parse_file
from analysis.liveness import compute_global_liveness, compute_local_liveness
from analysis.depgraph import build_dep_graph
from scheduler.pairing import stamp_slot_eligibility, greedy_pair
from scheduler.reorder import schedule, ScheduleMode

def width(v, signed=True):
    if v is None: return None
    if signed:
        for b in range(1, 34):
            if -(1 << (b-1)) <= v <= (1 << (b-1)) - 1: return b
    else:
        if v < 0: return None
        for b in range(1, 34):
            if v < (1 << b): return b
    return 34

def field_width(insn):
    """Bits the drawn field needs for this instruction's immediate."""
    if insn.imm is None: return None
    if insn.has_mem_operand:
        w = insn.access_width
        if not w or insn.imm % w: return None
        return width(insn.imm // w, signed=False if insn.imm >= 0 else True)
    if insn.is_addi4spn:
        return width(insn.imm // 4, signed=False)
    if insn.is_branch or insn.is_jump or insn.mnemonic in ("j", "jal"):
        return None                      # unresolved label
    return width(insn.imm)

need = collections.defaultdict(collections.Counter)
for path in sys.argv[1:]:
    _b, fns = parse_file(open(path).read())
    for fn in fns:
        for b in fn.blocks: stamp_slot_eligibility(b.instructions)
        g = compute_global_liveness(fn.blocks)
        for b in fn.blocks:
            if not b.instructions: continue
            compute_local_liveness(b, g)
            o = schedule(b, build_dep_graph(b), ScheduleMode.LIST)
            b.instructions = o; compute_local_liveness(b, g)
            for p in greedy_pair(o):
                if p[0] != 'pair': continue
                _, ia, ib, rule = p
                for slot, insn in (("a", ia), ("b", ib)):
                    w = field_width(insn)
                    if w is not None: need[(rule, slot)][min(w, 13)] += 1

print(f"{'frame':30}{'slot':>5}{'n':>8}   cumulative fit at 5 / 6 / 7 / 8 / 10 bits")
print("-"*88)
for k in sorted(need):
    h = need[k]; tot = sum(h.values())
    if tot < 50: continue
    cum, out = 0, []
    for b in range(1, 14):
        cum += h[b]
        if b in (5,6,7,8,10): out.append(f"{100*cum/tot:5.1f}%")
    print(f"{k[0][:29]:30}{k[1]:>5}{tot:>8}   " + "  ".join(out))

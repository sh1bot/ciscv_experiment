"""analysis/imm_relations.py — derive each frame's inter-immediate relation from
its template expressions, and check it against the corpus.

A template line carries at most one immediate, written as a linear expression
over a shared variable and the access width k, e.g.  k*imm,  k*imm+k,  -16*imm,
16*imm-k,  -k*imma,  4*immb.  When the A and B lines of a pair name the SAME
variable, the frame has a cross-instruction relation: the two immediates are two
views of one underlying value (a shared frame size, a ±stride, adjacent memory
offsets). This is the arithmetic rules.py hard-codes (e.g. mem-pair's
abs(a.imm-b.imm)==width, prologue's b.imm+width+a.imm==0).

We parse those expressions into a linear form  value = m*var + b  (m and b each
carry an integer part plus a multiple of k), eliminate the shared variable, and
check every corpus pair a rule matches against the resulting constraint.

Usage:  python3 -m analysis.imm_relations [--show] tests/godot.s tests/testcase0.s
"""
import os, re, sys
from collections import Counter

import yaml

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

from analysis.parser import parse_file
from analysis.liveness import compute_global_liveness, compute_local_liveness
from scheduler.pairing import stamp_slot_eligibility
from scheduler.rules import RULES
from analysis.encoding_verify import eligible, rule_ok
from analysis.imm_expr import parse_expr, ev, expr_str


# --- per-frame relations ---------------------------------------------------
def frame_relations(frame):
    """(var, mA, bA, mB, bB) for each template whose A/B lines share a variable."""
    rels = []
    for pair in frame.get("templates", []):
        ea, eb = parse_expr(pair[0]), parse_expr(pair[1])
        if ea and eb and ea[0] == eb[0]:
            rels.append((ea[0], ea[1], ea[2], eb[1], eb[2]))
    # de-dup identical relations across templates
    seen, out = set(), []
    for r in rels:
        if r not in seen:
            seen.add(r); out.append(r)
    return out


def satisfies(rel, va, vb, k):
    """Does (a.imm=va, b.imm=vb, width=k) fit the relation? Eliminate the shared
    variable: mB*(va-bA) == mA*(vb-bB)."""
    _var, mA, bA, mB, bB = rel
    return ev(mB, k) * (va - ev(bA, k)) == ev(mA, k) * (vb - ev(bB, k))


def load_frames():
    spec = yaml.safe_load(open(os.path.join(ROOT, "encoding.yaml")))
    rule2frame, rels = {}, {}
    for node in spec["doc"]:
        if "frame" not in node:
            continue
        f = node["frame"]
        rels[f["name"]] = frame_relations(f)
        for rn in f.get("rules_py_names") or [x.strip() for x in f["name"].split(",")]:
            rule2frame[rn] = f["name"]
    return rule2frame, rels


def main():
    args = sys.argv[1:]
    show = "--show" in args
    paths = [a for a in args if a != "--show"] or ["tests/godot.s", "tests/testcase0.s"]
    paths = [p if os.path.isabs(p) else os.path.join(ROOT, p) for p in paths]

    rule2frame, rels = load_frames()

    print("Derived inter-immediate relations (frames whose A and B share a var):")
    for frame, rl in rels.items():
        if not rl:
            continue
        for var, mA, bA, mB, bB in rl:
            print(f"  {frame:34}  a.imm={expr_str(mA, bA, var):10}  "
                  f"b.imm={expr_str(mB, bB, var)}")
    if show:
        return

    claimed = Counter(); checkable = Counter(); ok = Counter()
    viol = {}
    for path in paths:
        _b, fns = parse_file(open(path).read())
        for fn in fns:
            for bl in fn.blocks:
                stamp_slot_eligibility(bl.instructions)
        for fn in fns:
            gl = compute_global_liveness(fn.blocks)
            for bl in fn.blocks:
                if not bl.instructions:
                    continue
                compute_local_liveness(bl, gl)
                ins = bl.instructions
                for i in range(len(ins) - 1):
                    a, b = ins[i], ins[i + 1]
                    if not (a.a_slot_ok and b.b_slot_ok):
                        continue
                    for rule in RULES:
                        if not eligible(rule, a, b) or not rule_ok(rule, a, b):
                            continue
                        frame = rule2frame.get(rule.name)
                        rl = rels.get(frame) if frame else None
                        if not rl:
                            break                      # no relation to check
                        claimed[frame] += 1
                        if a.imm is None or b.imm is None:
                            break                      # one side has no immediate
                        k = next((x.access_width for x in (a, b)
                                  if x.has_mem_operand and x.access_width), 1)
                        checkable[frame] += 1
                        # order-insensitive: the pair may have matched with the
                        # physical instructions in either A/B order (scheduler-
                        # owned canonical ordering), so try both.
                        if any(satisfies(r, a.imm, b.imm, k)
                               or satisfies(r, b.imm, a.imm, k) for r in rl):
                            ok[frame] += 1
                        elif frame not in viol:
                            viol[frame] = (a.imm, b.imm, k)
                        break

    print("\nCorpus check (pairs a rule matched, for relation-bearing frames):")
    hdr = f"{'frame':34} {'matched':>7} {'checked':>7} {'holds':>6}"
    print(hdr); print("-" * len(hdr))
    for frame in rels:
        if not claimed.get(frame):
            continue
        cl, ck, o = claimed[frame], checkable.get(frame, 0), ok.get(frame, 0)
        rate = f"{100*o/ck:5.1f}%" if ck else "   -  "
        note = ""
        if ck and o < ck:
            va, vb, k = viol[frame]
            note = f"  e.g. a.imm={va} b.imm={vb} k={k}"
        print(f"{frame:34} {cl:7} {ck:7} {rate:>6}{note}")
    print("\nWidth k is the pair's memory access width; immediates with no shared\n"
          "variable (independent imma/immb) carry no relation and are not listed.")


if __name__ == "__main__":
    main()

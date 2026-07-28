"""analysis/frame_score.py — score each frame's opcode cost against its corpus
hit rate.

The opcode namespace is a fixed bit budget; an entropy-optimal prefix code
spends -log2(p) bits on something used with probability p. So a frame's share of
the codepoints should match its share of the hits. Per frame we report:

  hits    corpus pairs the frame's rules match
  cp      codepoints it costs (opcode_codepoints)
  s       log2( hit_share / cost_share ):
            ~0 proportionate, >>0 under-allocated (cheap for its use),
            <<0 OVER-PROVISIONED (big block, rarely used)
  H(ops)  entropy of the (op_a, op_b) combos actually exercised, in bits
  opsel   bits the block spends selecting an op-combo (ceil log2 cp)
  slack   opsel - H(ops): op-select bits not justified by real variety

Σ p_i·s_i = D_KL(usage ‖ allocation) — the wasted bits per packet from the
allocation not matching usage. Frames are ranked by s so the over-provisioned
ones surface at the top.

Usage:  python3 -m analysis.frame_score [tests/godot.s tests/testcase0.s]
"""
import math, os, sys
from collections import Counter, defaultdict

import yaml

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "util"))

from analysis.parser import parse_file
from analysis.liveness import compute_global_liveness, compute_local_liveness
from scheduler.pairing import stamp_slot_eligibility
from scheduler.rules import RULES
from analysis.encoding_verify import eligible, rule_ok
from analysis.encoding_budget import subform
from encoding_render import opcode_codepoints


def entropy(counter):
    n = sum(counter.values())
    if not n:
        return 0.0
    return -sum((c / n) * math.log2(c / n) for c in counter.values())


def gather(paths, rule2frame):
    """Per frame: corpus hit count and the distribution of (op_a, op_b) combos."""
    hits = Counter()
    combos = defaultdict(Counter)
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
                        if frame:
                            hits[frame] += 1
                            combos[frame][(subform(a), subform(b))] += 1
                        break
    return hits, combos


def main():
    paths = sys.argv[1:] or ["tests/godot.s", "tests/testcase0.s"]
    paths = [p if os.path.isabs(p) else os.path.join(ROOT, p) for p in paths]

    spec = yaml.safe_load(open(os.path.join(ROOT, "encoding.yaml")))
    grid = spec["grid"]
    rule2frame, cp, budget = {}, {}, {}
    for node in spec["doc"]:
        if "frame" not in node:
            continue
        f = node["frame"]
        if not f.get("ops"):
            continue
        cp[f["name"]] = opcode_codepoints(f, grid)
        budget[f["name"]] = f.get("budget")
        for rn in f.get("rules_py_names") or [x.strip() for x in f["name"].split(",")]:
            rule2frame[rn] = f["name"]

    hits, combos = gather(paths, rule2frame)
    total_hits = sum(hits.values()) or 1
    total_cp = sum(cp.values()) or 1

    rows, kl = [], 0.0
    for frame in cp:
        h, c = hits.get(frame, 0), cp[frame]
        hs, cs = h / total_hits, c / total_cp
        s = math.log2(hs / cs) if hs > 0 else None
        if s is not None:
            kl += hs * s
        H = entropy(combos[frame])
        opsel = math.ceil(math.log2(c)) if c > 1 else 0
        rows.append((frame, h, c, budget[frame], hs, cs, s, H, opsel))

    # rank: over-provisioned (low/None s) first
    rows.sort(key=lambda r: (r[6] is not None, r[6] if r[6] is not None else 0))

    hdr = (f"{'frame':34} {'hits':>6} {'cp':>4} {'hit%':>6} {'cost%':>6} "
           f"{'s':>6}  {'H(ops)':>6} {'opsel':>5} {'slack':>5}")
    print(hdr); print("-" * len(hdr))
    for frame, h, c, bud, hs, cs, s, H, opsel in rows:
        sfx = f"{s:6.2f}" if s is not None else "  —   "
        slack = opsel - H
        flag = "  ⚠ over-provisioned" if (s is not None and s <= -2) else \
               ("  ⚠ unused" if h == 0 else "")
        print(f"{frame:34} {h:6} {c:4} {100*hs:5.1f}% {100*cs:5.1f}% "
              f"{sfx}  {H:6.2f} {opsel:5} {slack:5.1f}{flag}")
    print("-" * len(hdr))
    print(f"\nΣ p·s = D_KL(usage ‖ allocation) = {kl:.2f} bits/packet wasted by the\n"
          f"allocation not matching usage (0 = codepoint share equals hit share).")
    print("s = log2(hit_share / cost_share): <<0 over-provisioned, >>0 under-\n"
          "allocated. slack = opsel - H(ops): op-select bits above the real op\n"
          "variety. Unused frames (0 hits) may be new or rules.py-only.")


if __name__ == "__main__":
    main()

"""util/frame_containment.py — which frames encode a subset of another's work?

`util/rule_overlap.py` answers this dynamically: of the pairs a frame actually
won on the corpus, how many another frame would also have taken. That is the
stronger evidence where it applies, but it is CORPUS-LIMITED -- a frame can be
fully shadowed on today's binaries and not in general.

This asks the declaration-level question instead: does frame G encode every
(opA, opB) combination frame F encodes, with immediate fields at least as
wide? That is a property of `encoding.yaml`, true whatever the corpus, and it
is what "we could elide F and lean on G" would need.

CONTAINMENT IS NECESSARY, NOT SUFFICIENT. Two frames can name the same op pair
and still not substitute for one another, because a frame is also a set of
structural constraints the op list does not state: whether the shared value is
a dead temporary (a chain) or a live result (a pair), whether a base register
is pinned to sp, which register class each field allows, whether the rd column
carries a sentinel. The report prints those alongside, and flags a candidate
as CHECK RULES rather than REDUNDANT whenever they differ.

    python3 util/frame_containment.py            # all frames
    python3 util/frame_containment.py --min 1    # include single-combo overlaps
"""
import argparse
import os
import sys
from collections import defaultdict

import yaml

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

from encoding_render import (cluster_pairs, op_name, op_bits, imm_field_bits,
                             opcode_codepoints, row_parts)


def frames(spec):
    return [n["frame"] for n in spec["doc"] if "frame" in n and n["frame"].get("ops")]


def combos(frame, grid):
    """{(opA, opB): (bitsA, bitsB)} -- every pair the frame encodes, with the
    EFFECTIVE immediate reach of each side.

    Effective, not declared: a bare op carries whatever field its row draws, so
    reading `imm: {bits}` alone reports None and silently passes every width
    test.  That is how `load0-load10-chain` and `load5-load5-chain` -- the same
    seven combinations over a 10-bit budget split two different ways -- first
    came out as mutually redundant.
    """
    fa, fb = imm_field_bits(frame, grid, "a"), imm_field_bits(frame, grid, "b")
    out = {}
    for c in frame.get("ops") or []:
        for ea, eb in cluster_pairs(c):
            out[(op_name(ea), op_name(eb))] = (op_bits(ea) or fa,
                                               op_bits(eb) or fb)
    return out


def shape(frame, grid):
    """The structural facts an op list does not state, as a comparable tuple."""
    tmpl = " ".join(l for pair in frame.get("templates", []) for l in pair)
    fields = set()
    for row in frame.get("rows") or []:
        for _f, stem, _b, _raw in row_parts(row, grid):
            fields.add(stem)
    return {
        "chain": "tmp" in tmpl,          # a value passes through the temporary
        "sp": "(sp)" in tmpl or "sp," in tmpl,
        "fields": frozenset(fields),
        "guest": any(str(r.get("rd")) == "unused" for r in frame.get("rows") or []),
    }


def shape_diff(sf, sg):
    """Why F and G are not interchangeable, structurally. Empty means they are
    the same animal as far as this tool can see."""
    d = []
    if sf["chain"] != sg["chain"]:
        d.append("chain" if sf["chain"] else "pair")
    if sf["sp"] != sg["sp"]:
        d.append("sp-pinned" if sf["sp"] else "any-base")
    if sf["guest"] != sg["guest"]:
        d.append("sentinel-selected" if sf["guest"] else "own-opcode")
    missing = sf["fields"] - sg["fields"]
    if missing:
        d.append("fields " + ",".join(sorted(missing)) + " not drawn there")
    return d


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--min", type=int, default=1,
                    help="minimum shared combinations to report (default 1)")
    args = ap.parse_args()

    spec = yaml.safe_load(open(os.path.join(ROOT, "encoding.yaml")))
    grid = spec["grid"]
    fs = frames(spec)
    C = {f["name"]: combos(f, grid) for f in fs}
    S = {f["name"]: shape(f, grid) for f in fs}
    CP = {f["name"]: opcode_codepoints(f, grid) for f in fs}

    print(f"{len(fs)} frames; a combination is one (opA, opB) the frame encodes.\n")

    full, partial = [], []
    for f in fs:
        for g in fs:
            if f is g:
                continue
            fn, gn = f["name"], g["name"]
            shared = set(C[fn]) & set(C[gn])
            if len(shared) < args.min:
                continue
            # width test: G must be at least as wide on every shared op
            narrower = []
            for k in shared:
                for slot, (bf, bg) in enumerate(zip(C[fn][k], C[gn][k])):
                    # `is not None`, not truthiness: a zero-width field is a
                    # real answer (the offset is structurally zero) and 0 < 5
                    # is exactly the shortfall this test exists to catch.
                    if bf is not None and bg is not None and bg < bf:
                        narrower.append(f"{k[slot]}@{bg}<{bf}")
            covered = set(C[fn]) <= set(C[gn]) and not narrower
            rec = (fn, gn, len(shared), len(C[fn]), len(C[gn]),
                   narrower, shape_diff(S[fn], S[gn]))
            (full if covered else partial).append(rec)

    print("=" * 78)
    print("SAME COMBINATIONS, DIFFERENT IMMEDIATE SPLIT")
    print("=" * 78)
    print("  Neither contains the other: they encode the same op pairs and")
    print("  divide the same field budget differently, so each is wider than")
    print("  the other on one slot.  Eliding either forfeits its half.\n")
    seen = set()
    any_split = False
    for f in fs:
        for g in fs:
            fn, gn = f["name"], g["name"]
            if fn >= gn or set(C[fn]) != set(C[gn]) or C[fn] == C[gn]:
                continue
            wf = {k: v for k, v in C[fn].items()}
            wg = {k: v for k, v in C[gn].items()}
            a_f = max(v[0] or 0 for v in wf.values())
            b_f = max(v[1] or 0 for v in wf.values())
            a_g = max(v[0] or 0 for v in wg.values())
            b_g = max(v[1] or 0 for v in wg.values())
            print(f"  {fn:30} A={a_f:2}b B={b_f:2}b   ({CP[fn]}cp)")
            print(f"  {gn:30} A={a_g:2}b B={b_g:2}b   ({CP[gn]}cp)")
            print(f"  {'':30} {len(C[fn])} shared combinations\n")
            any_split = True
    if not any_split:
        print("  (none)\n")

    print("=" * 78)
    print("FULLY CONTAINED — every combination F encodes, G encodes at least as wide")
    print("=" * 78)
    if not full:
        print("  (none)")
    for fn, gn, n, nf, ng, _w, diff in sorted(full, key=lambda r: -r[2]):
        verdict = "REDUNDANT" if not diff else "CHECK RULES"
        print(f"  {fn} ({CP[fn]}cp, {nf} combos)  <=  {gn} ({CP[gn]}cp, {ng})"
              f"   [{verdict}]")
        if diff:
            print(f"      differs: {'; '.join(diff)}")

    print()
    print("=" * 78)
    print("PARTIAL OVERLAP — shared combinations, but F has some G does not")
    print("=" * 78)
    rows = sorted(partial, key=lambda r: -r[2])
    print(f"  {'F':30}{'G':30}{'shared':>7}{'of F':>6}")
    for fn, gn, n, nf, ng, narrow, diff in rows[:24]:
        flag = "  narrower: " + ",".join(narrow[:3]) if narrow else ""
        print(f"  {fn:30}{gn:30}{n:7}{nf:6}{flag}")
    if len(rows) > 24:
        print(f"  ... {len(rows) - 24} more")

    # a combination no other frame encodes is what a frame alone is buying
    owner = defaultdict(list)
    for f in fs:
        for k in C[f["name"]]:
            owner[k].append(f["name"])
    print()
    print("=" * 78)
    print("SOLE ENCODER — combinations only one frame can express")
    print("=" * 78)
    print(f"  {'frame':34}{'combos':>8}{'sole':>7}{'cp':>5}")
    for f in sorted(fs, key=lambda f: -len(C[f["name"]])):
        n = f["name"]
        sole = sum(1 for k in C[n] if len(owner[k]) == 1)
        print(f"  {n:34}{len(C[n]):8}{sole:7}{CP[n]:5}")


if __name__ == "__main__":
    main()

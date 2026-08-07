#!/usr/bin/env python3
"""
util/encoding_diff.py — stats-diff between two revisions of encoding.yaml.

Text diffs of the yaml drown structure in prose; this compares what the
revisions MEAN: frames added and removed, budget and codepoint-demand moves,
and per-op immediate width changes.

Usage:
    python3 util/encoding_diff.py REV               # REV vs working tree
    python3 util/encoding_diff.py REV1 REV2         # REV1 vs REV2
Revisions are anything `git show REV:encoding.yaml` accepts.
"""
import os
import subprocess
import sys

import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from encoding_render import op_contracts, opcode_codepoints


def _load(rev):
    if rev is None:
        with open(os.path.join(ROOT, "encoding.yaml")) as fh:
            return yaml.safe_load(fh)
    out = subprocess.run(["git", "-C", ROOT, "show", f"{rev}:encoding.yaml"],
                         capture_output=True, text=True)
    if out.returncode:
        raise SystemExit(f"git show {rev}:encoding.yaml: {out.stderr.strip()}")
    return yaml.safe_load(out.stdout)


def _frames(spec):
    return {n["frame"]["name"]: n["frame"]
            for n in spec.get("doc") or []
            if isinstance(n, dict) and n.get("frame")}


def _stat(frame, grid):
    try:
        cp = opcode_codepoints(frame, grid)
    except Exception:
        cp = None
    widths = {f"{s}:{mn}": c.get("bits")
              for s in ("a", "b")
              for mn, c in op_contracts(frame, s).items()}
    return {"budget": frame.get("budget"), "demand": cp, "widths": widths,
            "rows": len(frame.get("rows") or [])}


def main():
    args = sys.argv[1:]
    if not args or len(args) > 2:
        print(__doc__)
        return 2
    old_rev = args[0]
    new_rev = args[1] if len(args) == 2 else None
    old_spec, new_spec = _load(old_rev), _load(new_rev)
    old, new = _frames(old_spec), _frames(new_spec)
    og, ng = old_spec["grid"], new_spec["grid"]
    new_name = new_rev or "working tree"

    for name in sorted(old.keys() - new.keys()):
        s = _stat(old[name], og)
        print(f"- {name}  (budget {s['budget']}, demand {s['demand']})")
    for name in sorted(new.keys() - old.keys()):
        s = _stat(new[name], ng)
        print(f"+ {name}  (budget {s['budget']}, demand {s['demand']})")

    for name in sorted(old.keys() & new.keys()):
        so, sn = _stat(old[name], og), _stat(new[name], ng)
        notes = []
        for key in ("budget", "demand", "rows"):
            if so[key] != sn[key]:
                notes.append(f"{key} {so[key]} -> {sn[key]}")
        for mn in sorted(so["widths"].keys() | sn["widths"].keys()):
            wo, wn = so["widths"].get(mn), sn["widths"].get(mn)
            if wo != wn:
                notes.append(f"{mn}@{wo or 'field'} -> @{wn or 'field'}")
        if notes:
            print(f"~ {name}  " + ", ".join(notes))

    def total(frames, grid):
        return sum(f.get("budget") or 0 for f in frames.values())
    print(f"\nreserved: {total(old, og)} ({old_rev}) -> "
          f"{total(new, ng)} ({new_name})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

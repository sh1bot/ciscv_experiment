"""
util/rule_hits.py — per-frame hit counts over the whole corpus, split by base.

Runs the scheduler on each corpus and reads the `rule hits:` breakdown out of
its file-totals block, so the counts are what the PAIRER actually took on the
SCHEDULED stream — not an adjacency ceiling (see `analysis/frame_score.py` for
that, and CLAUDE.md on why adjacency is the wrong question).

A frame may be reached by more than one rules.py rule, so hits are folded to
frames before the codepoint division: a frame's cost is its opcode block,
whatever number of rules reach it.  No frame is shared today -- the last pair
that was, `deref-load-chain`/`base-load-chain`, turned out to share an
op-select with nothing selecting between them and was split.

Codepoints come from `encoding.yaml` via `encoding_render.opcode_codepoints` —
the same function `encoding_assign` prices the namespace with — so hits/cp is
hits per opcode leaf the frame spends out of the 1024-codepoint budget.

Usage:  python3 util/rule_hits.py [--raw DIR] [name ...]
        python3 util/rule_hits.py --from-raw DIR      (re-tabulate, no re-run)
"""
import argparse
import os
import re
import subprocess
import sys

import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "util"))

from encoding_render import opcode_codepoints
from isa.xlen import detect_xlen

# The scored corpus: every build with a `-noalias` twin to score against,
# minus the no-C builds, which exist only for util/cross_parity.py and have
# no real RVC to compare with (results/corpus/README.md).
CORPORA = [
    "testcase0", "musl-rv32", "musl-os-rv32", "musl-gcc-rv32", "sqlite-rv32",
    "cpp-rv32", "godot", "musl-rv64", "musl-os-rv64", "musl-gcc-rv64",
    "sqlite-rv64", "sqlitem-rv64", "sqlite-gcc-rv64", "cpp-rv64",
]


def frames_from_yaml():
    """(rule name -> frame name, frame name -> codepoints), yaml order."""
    spec = yaml.safe_load(open(os.path.join(ROOT, "encoding.yaml")))
    grid = spec["grid"]
    rule2frame, cp = {}, {}
    for node in spec["doc"]:
        f = node.get("frame")
        if not f or not f.get("ops"):
            continue
        cp[f["name"]] = opcode_codepoints(f, grid)
        for rn in f.get("rules_py_names") or [x.strip() for x in f["name"].split(",")]:
            rule2frame[rn] = f["name"]
    return rule2frame, cp


def run(name, raw_dir=None):
    """Scheduler stats for one corpus: (xlen, insns, packets, pairs, {rule: hits})."""
    path = os.path.join(ROOT, "tests", f"{name}.s")
    xlen, _ = detect_xlen(open(path).read(8192))
    out = subprocess.run([sys.executable, os.path.join(ROOT, "__main__.py"), path],
                         capture_output=True, text=True).stdout
    # The file-totals block is last; everything before it is per-function.
    tail = out[out.rindex("# --- file totals ---"):]
    if raw_dir:
        os.makedirs(raw_dir, exist_ok=True)
        with open(os.path.join(raw_dir, f"{name}.txt"), "w") as fh:
            fh.write(f"# {name} (RV{xlen})\n{tail}")
    return (xlen,) + parse_tail(tail)


def parse_tail(tail):
    """(insns, packets, pairs, {rule: hits}) from a file-totals block."""
    m = re.search(r"instructions: (\d+)\s+packets: (\d+)\s+pairs: (\d+)", tail)
    hits = {r: int(n) for r, n in re.findall(r"^#\s+([a-z0-9-]+): (\d+)$",
                                             tail, re.M)}
    return int(m.group(1)), int(m.group(2)), int(m.group(3)), hits


def table(label, per_corpus, cp, out=sys.stdout):
    """Frame rows for one base: hits, codepoints, hits/cp, share."""
    totals = {}
    for hits in per_corpus.values():
        for frame, n in hits.items():
            totals[frame] = totals.get(frame, 0) + n
    grand = sum(totals.values()) or 1
    print(f"\n{label}  ({', '.join(per_corpus)})", file=out)
    print(f"{'frame':34}{'hits':>9}{'cp':>6}{'hits/cp':>10}{'share':>8}", file=out)
    print("-" * 67, file=out)
    for frame in sorted(cp, key=lambda f: -(totals.get(f, 0) / cp[f])):
        n = totals.get(frame, 0)
        print(f"{frame:34}{n:9}{cp[frame]:6}{n / cp[frame]:10.1f}"
              f"{100 * n / grand:7.1f}%", file=out)
    print("-" * 67, file=out)
    print(f"{'TOTAL':34}{grand:9}{sum(cp.values()):6}"
          f"{grand / sum(cp.values()):10.1f}", file=out)
    return totals


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("names", nargs="*", default=None)
    ap.add_argument("--raw", metavar="DIR", help="write per-corpus stats blocks here")
    ap.add_argument("--from-raw", metavar="DIR", help="re-tabulate saved blocks")
    args = ap.parse_args()

    rule2frame, cp = frames_from_yaml()
    names = args.names or CORPORA
    by_base = {32: {}, 64: {}}
    sizes = {}

    for name in names:
        if args.from_raw:
            text = open(os.path.join(args.from_raw, f"{name}.txt")).read()
            xlen = int(re.search(r"\(RV(\d+)\)", text).group(1))
            insns, packets, pairs, hits = parse_tail(text)
        else:
            xlen, insns, packets, pairs, hits = run(name, args.raw)
            print(f"{name:16} RV{xlen}  {insns:7} insns  {pairs:6} pairs",
                  file=sys.stderr)
        sizes[name] = (insns, packets, pairs)
        folded = {}
        for rule, n in hits.items():
            frame = rule2frame.get(rule)
            if frame is None:              # a rules.py rule with no yaml frame
                raise SystemExit(f"{name}: rule {rule!r} has no frame in encoding.yaml")
            folded[frame] = folded.get(frame, 0) + n
        by_base[xlen][name] = folded

    print("# Frame hits over the corpus, by base.  Generated by util/rule_hits.py.")
    print("#\n# hits    pairs the frame's rules took on the scheduled stream")
    print("# cp      opcode codepoints the frame spends (encoding.yaml)")
    print("# hits/cp pairs bought per codepoint — the allocation-efficiency number")
    for xlen in (32, 64):
        if by_base[xlen]:
            table(f"RV{xlen}", by_base[xlen], cp)
    if by_base[32] and by_base[64]:
        both = dict(by_base[32], **by_base[64])
        table("RV32 + RV64", both, cp)
    print("\ncorpus sizes (insns / packets / pairs):")
    for name, (i, p, pr) in sizes.items():
        print(f"  {name:18}{i:8}{p:9}{pr:8}")


if __name__ == "__main__":
    main()

"""util/call_frame_value.py — what the indexed-call frame is worth, end to end.

A linear sweep over every control transfer that names a function, classified
by what it costs today and what it would cost as an indexed table jump. The
two frames it prices are:

  cm.jalt  a linking table jump   -- replaces `jal f` and `auipc ra; jalr ra`
  cm.jt    a non-linking one      -- replaces `j f`   and `auipc t1; jr t1`

Three separate effects, which the report keeps apart because they have very
different costs to buy:

  PAIRING   a table jump is ten bits, so an A instruction rides beside it.
            Worth one word per call that has a partner to absorb.
  WART      a far call is two instructions today; the table jump is one,
            whether or not it pairs. This is the Zcmt effect proper, and it
            needs no pairing opportunity at all -- an unpaired cm.jalt still
            fits a packet beside a derived nop.
  TABLE     the jvt table itself is a cost: entries * XLEN/8 bytes.

Pairing rates come from analysis/anchors.py, so they are measured against the
real scheduled and paired stream, exclude candidates another frame took, and
allow reordering. Table coverage is frequency-weighted over the union of call
and tail-call targets.

  python3 util/call_frame_value.py cpp-rv32 musl-gcc-rv32 sqlite-rv32
"""
import argparse
import os
import re
import sys
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from analysis.anchors import scan, coverage, any_call

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# The op set fitted in FINDINGS ("The indexed-call A slot, chosen from what is
# REACHABLE"): eight shapes, each ten bits or fewer.
A_OPS = ["mv rd5,rs5", "addi rd3,sp,imm7", "li rd3,imm7", "load rd3,k*imm7(sp)",
         "store rs5,k*imm5(sp)", "load rd5,0(rs5)", "store rs5,0(rs5)",
         "addi rsd5,imm5"]


def census(corpus):
    """Classify every transfer that names a function, and collect targets."""
    lines = open(os.path.join(ROOT, "tests", f"{corpus}.s")).read().splitlines()
    ins, labels, local = [], {}, set()
    for line in lines:
        if line.startswith("#") or not line.strip():
            continue
        if not line.startswith("\t"):
            s = line.strip()
            if s.endswith(":"):
                labels.setdefault(s[:-1], len(ins))
                if s.startswith(".L"):
                    local.add(s[:-1])
            continue
        p = line[1:].split(None, 1)
        if not p or not p[0][0].islower():
            continue
        ins.append((p[0], p[1].split("#")[0].strip() if len(p) > 1 else ""))

    called = {ops for m, ops in ins if m in ("jal", "c.jal")
              and ops and "," not in ops}
    n = Counter()
    freq = Counter()                 # target -> times transferred to
    prev = None
    for m, ops in ins:
        if m in ("jal", "c.jal") and ops and "," not in ops:
            n["near call"] += 1
            freq[ops] += 1
        elif m in ("j", "c.j") and ops:
            t = ops.strip()
            if t in local:
                n["local jump (not a transfer to a function)"] += 1
            else:
                n["near tail call"] += 1
                freq[t] += 1
        elif m in ("jalr", "jr") and prev and prev[0] == "auipc":
            # the linker could not relax it: auipc supplies the high bits, and
            # objdump names the resolved target in angle brackets -- usually a
            # PLT slot, and a real table entry like any other.
            n["far call" if m == "jalr" else "far tail call"] += 1
            sym = re.search(r"<([^>]+)>", ops)
            freq[sym.group(1) if sym else "<far:%d>" % len(freq)] += 1
        prev = (m, ops)
    return n, freq


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("corpora", nargs="+")
    ap.add_argument("--index-bits", type=int, default=10)
    ap.add_argument("--xlen", type=int, default=None,
                    help="table entry size; inferred from the corpus name")
    args = ap.parse_args()

    for corpus in args.corpora:
        n, freq = census(corpus)
        xlen = args.xlen or (64 if corpus.endswith("64") else 32)
        entries = 1 << args.index_bits

        transfers = sum(v for k, v in n.items() if k != "local jump "
                        "(not a transfer to a function)")
        in_table = sum(v for _, v in freq.most_common(entries))
        q = in_table / transfers if transfers else 0

        n_anchor, n_scored, rows = scan(corpus, anchor=any_call)
        p = coverage(rows, A_OPS) / n_scored if n_scored else 0

        near = n["near call"] + n["near tail call"]
        far = n["far call"] + n["far tail call"]

        pairing = q * p * (near + far)
        wart = q * far
        # the table need only be as large as the target set
        table = min(entries, len(freq)) * xlen // 8 / 4   # words

        print(f"== {corpus}  (index {args.index_bits} bits, {entries} entries, "
              f"XLEN {xlen})")
        for k in ("near call", "far call", "near tail call", "far tail call",
                  "local jump (not a transfer to a function)"):
            if n[k]:
                print(f"   {n[k]:8d}  {k}")
        print(f"   {transfers:8d}  transfers priced")
        print(f"   {len(freq):8d}  distinct targets, table {min(entries, len(freq))}; "
              f"{100*q:.1f}% of transfers reach the top {entries}")
        print(f"   {100*p:8.1f}%  of calls have an A partner "
              f"(8-op set, measured)")
        print(f"   words saved: pairing {pairing:9.0f}   wart {wart:7.0f}   "
              f"table {-table:7.0f}   NET {pairing + wart - table:9.0f} "
              f"= {(pairing + wart - table) * 4 / 1024:.1f} KiB")
        print()


if __name__ == "__main__":
    main()

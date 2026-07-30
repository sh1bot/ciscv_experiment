"""
util/duse.py — def-to-first-use distance histogram over a corpus .s file.

How far did the compiler move a value's consumer away from its producer? A
producer whose consumer is the very next instruction (distance 1) is a pair
candidate the packet scheduler can take without reordering anything; distance
2+ means our reorder window has to close the gap. The histogram is therefore a
direct measure of how much pipeline scheduling the compiler did TO us.

Scope is one basic block: the scan restarts at a label or a branch/jump, so
nothing is counted across control flow. Only register operands are considered
(no memory dependences), and a value re-defined before it is read counts as
never used — both make this an approximation, but a consistent one, which is
what comparing two builds of the same source needs.

Usage:  python3 util/duse.py tests/foo.s [tests/bar.s ...]
"""
import collections
import re
import sys

_INSN = re.compile(r"^\t(\S+)\s*(.*)$")
_REG = re.compile(r"x\d+|zero|ra|sp|gp|tp|fp|t\d|s\d+|a\d+")
# Instructions with no register destination: their first operand is a source.
_NO_DEST = ("b", "j", "s", "c.s", "c.b", "c.j", "ret", "c.ret", "ecall", "fence")
MAX = 16                      # scheduler/reorder.py WINDOW_SIZE


def operands(ops):
    return [m.group(0) for m in _REG.finditer(ops)]


def flush(block, hist, unused):
    for i, (mn, ops) in enumerate(block):
        if mn.startswith(_NO_DEST):
            continue
        regs = operands(ops)
        if not regs or regs[0] == "zero":
            continue
        d = regs[0]
        for k in range(i + 1, min(i + MAX + 1, len(block))):
            kmn, kops = block[k]
            kregs = operands(kops)
            reads = kregs if kmn.startswith(_NO_DEST) else kregs[1:]
            if d in reads:
                hist[k - i] += 1
                break
            if kregs and not kmn.startswith(_NO_DEST) and kregs[0] == d:
                break          # redefined before any read
        else:
            unused[0] += 1


def main():
    for path in sys.argv[1:]:
        hist, unused, block = collections.Counter(), [0], []
        for line in open(path):
            m = _INSN.match(line)
            if not m:
                if line.strip().endswith(":"):
                    flush(block, hist, unused)
                    block = []
                continue
            block.append((m.group(1), m.group(2)))
            if m.group(1).startswith(("b", "j", "c.b", "c.j", "ret", "c.ret")):
                flush(block, hist, unused)
                block = []
        flush(block, hist, unused)
        n = sum(hist.values())
        far = sum(v for k, v in hist.items() if k >= 7)
        print(f"{path.split('/')[-1]:30} defs={n:>7}  "
              + "  ".join(f"{k}:{100 * hist[k] / n:.1f}%" for k in range(1, 7))
              + f"  >=7:{100 * far / n:.1f}%")


if __name__ == "__main__":
    main()

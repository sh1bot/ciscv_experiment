"""
util/objdump_to_asm.py — turn `llvm-objdump -d` output into corpus assembly.

The test corpus (tests/*.s) is POST-LINK disassembly, not compiler output:
relocations are already resolved, so no `%hi`/`%lo` survives and the immediate
statistics are the ones a real binary carries. This reproduces that format from
a linked ELF so new test cases match godot.s / testcase0.s exactly.

Conventions reproduced:
  * `# <name>:     file format <fmt>` header, then `# Disassembly of section .text:`
  * each symbol start becomes `.globl sym` + `sym:` (the parser keys function
    entries off `.globl`, see analysis/parser.py pass 1)
  * every branch/jump target that is not itself a symbol start gets a
    `.Lbranch_%08x:` label, and the operand is rewritten to reference it
  * operands lose llvm-objdump's spaces after commas

Immediates are left in llvm-objdump's hex form; the parser reads them with
int(t, 0), so hex and decimal are equivalent to it.

Usage:  llvm-objdump-18 -d --no-show-raw-insn foo.so | \
            python3 util/objdump_to_asm.py --name foo > foo.s

Pass --no-aliases through to llvm-objdump for the `-noalias` variant, which is
where the literal `c.*` opcodes are visible — the alias form prints `mv` for
`c.mv`, so a compressed binary looks uncompressed unless you ask for no-aliases.
"""
import argparse
import re
import sys

# "   207ec:      \tbltu\ta0, a4, 0x20818 <sqlite3_status64+0x34>"
_INSN = re.compile(r"^\s*([0-9a-f]+):\s*\t?(\S+)\s*(.*)$")
# "00000000000207e4 <sqlite3_status64>:"
_SYM = re.compile(r"^([0-9a-f]+)\s+<([^>]+)>:\s*$")
# a branch/jump operand: "0x20818 <sqlite3_status64+0x34>"
_TARGET = re.compile(r"0x([0-9a-f]+)\s*<[^>]+>")


def parse(lines):
    """(fmt, syms{addr: name}, insns[(addr, mnem, ops)], targets{addr})."""
    fmt, syms, insns, targets = "", {}, [], set()
    for line in lines:
        line = line.rstrip("\n")
        if "file format" in line:
            fmt = fmt or line.split("file format", 1)[1].strip()
            continue
        m = _SYM.match(line)
        if m:
            syms[int(m.group(1), 16)] = m.group(2)
            continue
        m = _INSN.match(line)
        if not m:
            continue
        addr, mnem, ops = int(m.group(1), 16), m.group(2), m.group(3).strip()
        t = _TARGET.search(ops)
        if t:
            targets.add(int(t.group(1), 16))
        insns.append((addr, mnem, ops))
    return fmt, syms, insns, targets


def rewrite_ops(ops, syms, labels):
    """Replace `0xADDR <whatever>` with the symbol or .Lbranch label it names."""
    def sub(m):
        a = int(m.group(1), 16)
        return syms.get(a) or labels.get(a) or m.group(0)
    ops = _TARGET.sub(sub, ops)
    return re.sub(r",\s+", ",", ops)


def emit(name, fmt, syms, insns, targets, out):
    # A target landing on a symbol start uses the symbol; everything else needs
    # a synthetic label, matching the corpus's .Lbranch_%08x convention.
    labels = {a: f".Lbranch_{a:08x}" for a in sorted(targets) if a not in syms}
    print(f"# {name}:     file format {fmt}\n\n\n", file=out)
    print("# Disassembly of section .text:\n", file=out)
    for addr, mnem, ops in insns:
        if addr in syms:
            print(f"\n\t.globl {syms[addr]}\n{syms[addr]}:", file=out)
        elif addr in labels:
            print(f"\n{labels[addr]}:", file=out)
        ops = rewrite_ops(ops, syms, labels)
        print(f"\t{mnem}\t{ops}" if ops else f"\t{mnem}", file=out)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--name", required=True, help="name for the header comment")
    ap.add_argument("input", nargs="?", help="objdump output (default stdin)")
    a = ap.parse_args()
    src = open(a.input) if a.input else sys.stdin
    fmt, syms, insns, targets = parse(src)
    if not insns:
        sys.exit("no instructions found — is this llvm-objdump -d output?")
    emit(a.name, fmt, syms, insns, targets, sys.stdout)


if __name__ == "__main__":
    main()

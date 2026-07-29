"""
util/corpus_scores.py — score every corpus against REAL RVC.

For each tests/<name>.s with a matching <name>-noalias.s, report packet size
against the compression the binary actually shipped. The `rvc_eligible`
estimator is a ceiling (no branch-offset check, no RV32/RV64 gating, float RVC
out of scope — see CLAUDE.md); the -noalias disassembly is ground truth, since
the alias form prints `mv` for `c.mv` and a compressed binary reads as
uncompressed unless you ask for no-aliases.

The break-even line: packets cost 4*(N-P) and RVC costs 4N-2C, so packets win
exactly when P > C/2 — pairs must exceed half the compressed-instruction count.
That single number says whether a corpus is winnable, and by how far.

Usage:  python3 util/corpus_scores.py [name ...]
"""
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TESTS = os.path.join(ROOT, "tests")


def real_rvc(name):
    """(instructions, compressed) from the -noalias disassembly."""
    path = os.path.join(TESTS, f"{name}-noalias.s")
    if not os.path.exists(path):
        return None
    n = c = 0
    with open(path) as fh:
        for line in fh:
            if not line.startswith("\t"):
                continue
            mnem = line[1:].split(None, 1)[0] if line[1:].strip() else ""
            if not mnem or not mnem[0].islower():
                continue
            n += 1
            if mnem.startswith("c."):
                c += 1
    return n, c


def schedule(name):
    """(instructions, packets, pairs) from a scheduler run."""
    out = subprocess.run([sys.executable, os.path.join(ROOT, "__main__.py"),
                          os.path.join(TESTS, f"{name}.s")],
                         capture_output=True, text=True).stdout
    m = None
    for m in re.finditer(r"instructions: (\d+)\s+packets: (\d+)\s+pairs: (\d+)", out):
        pass
    return tuple(int(g) for g in m.groups()) if m else None


def main():
    names = sys.argv[1:] or sorted(
        f[:-2] for f in os.listdir(TESTS)
        if f.endswith(".s") and not f.endswith("-noalias.s")
        and os.path.exists(os.path.join(TESTS, f[:-2] + "-noalias.s")))
    print(f"{'corpus':16}{'insns':>8}{'pairs':>8}{'packets':>9}"
          f"{'packet %':>10}{'real RVC':>10}{'vs RVC':>9}{'P/(C/2)':>9}")
    print("-" * 79)
    for name in names:
        s, r = schedule(name), real_rvc(name)
        if not s or not r:
            print(f"{name:16} (missing scheduler result or -noalias variant)")
            continue
        N, packets, pairs = s
        n_dis, comp = r
        if n_dis != N:
            print(f"{name:16} ! -noalias has {n_dis} instructions, "
                  f"scheduled file has {N} — not the same build")
        base, pk = 4 * N, 4 * packets
        rvc = 2 * comp + 4 * (n_dis - comp)
        rvc = rvc * N / n_dis          # scale if the two files differ
        print(f"{name:16}{N:>8}{pairs:>8}{packets:>9}"
              f"{100*pk/base:>9.1f}%{100*rvc/base:>9.1f}%"
              f"{100*pk/rvc:>8.1f}%{100*pairs/(comp/2*N/n_dis):>8.1f}%")
    print("\npacket % and real RVC are size against a 4-byte-per-instruction "
          "baseline;\nvs RVC under 100% means packets are smaller. P/(C/2) is "
          "progress toward the\nbreak-even point where pairs exceed half the "
          "compressed-instruction count.")


if __name__ == "__main__":
    main()

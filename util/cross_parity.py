"""
util/cross_parity.py — score packets against RVC the way the comparison should
be made: each scheme measured on the build made FOR it.

The usual table asks how many pairs a build needs to beat the RVC in that SAME
build. That question quietly makes us pay RVC's bill. Enabling `c` changes
codegen before any compression happens — register allocation clusters into
x8-x15 to keep instructions compressible, and the extra moves that forces are
instructions we then have to pair. Packets have full 5-bit register fields and
gain nothing from that clustering, so measuring them on a `+C` build charges
them for a constraint they do not have.

The honest question is the one a chip designer actually faces: ship the RVC
toolchain and its binary, or ship the packet toolchain and its binary? So:

    RVC bytes    = 4*N_c - 2*C        measured on the +C build
    packet bytes = 4*(N_n - P)        measured on the no-C build
    parity       <=>  P >= N_n - N_c + C/2

Against the same-build target of `C/2`, this moves the goalposts by exactly
`N_n - N_c` — the instruction-count difference between the two builds. That
term is small and usually NEGATIVE (a no-C build emits slightly fewer
instructions), so the honest target is slightly easier, not harder.

Needs a matched pair of builds — same source, same flags, `c` in the `-march`
of one and not the other. Where only one build exists (godot, testcase0: we
have the binary, not the source) the cross-build number cannot be computed and
the row says so rather than guessing.

Usage:  python3 util/cross_parity.py [pair ...]
        where a pair is `rvc_name:noc_name`, e.g. musl-rv64:musl-norvc-rv64
"""
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TESTS = os.path.join(ROOT, "tests")

# Matched (+C, no-C) builds in tests/. Anything not listed has no twin.
PAIRS = [
    ("musl-rv64",    "musl-norvc-rv64"),
    ("musl-rv32",    "musl-norvc-rv32"),
    ("musl-os-rv64", "musl-osnoc-rv64"),
    ("musl-os-rv32", "musl-osnoc-rv32"),
]


def counts(name):
    """(instructions, compressed) from the -noalias disassembly."""
    n = c = 0
    with open(os.path.join(TESTS, f"{name}-noalias.s")) as fh:
        for line in fh:
            if not line.startswith("\t"):
                continue
            body = line[1:].strip()
            if not body:
                continue
            mnem = body.split(None, 1)[0]
            if not mnem or not mnem[0].islower():
                continue
            n += 1
            if mnem.startswith("c."):
                c += 1
    return n, c


def schedule(name):
    """(instructions, pairs) from a scheduler run."""
    out = subprocess.run([sys.executable, os.path.join(ROOT, "__main__.py"),
                          os.path.join(TESTS, f"{name}.s")],
                         capture_output=True, text=True).stdout
    m = None
    for m in re.finditer(r"instructions: (\d+)\s+packets: (\d+)\s+pairs: (\d+)", out):
        pass
    return (int(m.group(1)), int(m.group(3))) if m else None


def main():
    args = sys.argv[1:]
    pairs = [tuple(a.split(":", 1)) for a in args] if args else PAIRS
    print(f"{'program':16}{'RVC bytes':>11}{'pkt bytes':>11}{'vs RVC':>9}"
          f"{'same-build':>12}{'cross-build':>13}{'delta':>8}")
    print("-" * 80)
    for rvc_name, noc_name in pairs:
        n_c, comp = counts(rvc_name)
        n_n, _ = counts(noc_name)
        sched = schedule(noc_name)
        if not sched:
            print(f"{rvc_name:16} (no scheduler result for {noc_name})")
            continue
        n_sched, pairs_n = sched
        if n_sched != n_n:
            print(f"{rvc_name:16} ! {noc_name} disassembly has {n_n} "
                  f"instructions, scheduled file has {n_sched}")
        rvc_bytes = 4 * n_c - 2 * comp
        pkt_bytes = 4 * (n_sched - pairs_n)
        cross = round(n_sched - n_c + comp / 2) - pairs_n
        # What the same-build table would have said, for comparison.
        same_sched = schedule(rvc_name)
        same = (round(comp / 2 * same_sched[0] / n_c) - same_sched[1]
                if same_sched else None)
        print(f"{rvc_name:16}{rvc_bytes:>11}{pkt_bytes:>11}"
              f"{100 * pkt_bytes / rvc_bytes:>8.1f}%"
              f"{('%+d' % same) if same is not None else 'n/a':>12}"
              f"{cross:>+13}{(cross - same) if same is not None else 0:>+8}")
    print("-" * 80)
    print("RVC bytes are measured on the +C build, packet bytes on the no-C "
          "build:\neach scheme on the build made for it.  'same-build' is the "
          "old target (C/2\npairs, both measured on the +C build); 'cross-build' "
          "is N_n - N_c + C/2.\n'delta' is how much the honest question moves "
          "the goalposts — negative is\neasier.")


if __name__ == "__main__":
    main()

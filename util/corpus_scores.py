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
    """(instructions, compressed, compressed_nops) from the -noalias dump.

    The nop count is split by width because padding is zero-rated on BOTH
    sides: RVC does not get charged for a nop we are not charged for either.
    A `c.nop` would cost RVC two bytes and a 32-bit one four, so the widths
    cannot be lumped together -- though measured, every padding nop in every
    corpus is the 32-bit form and `c_nops` is zero throughout."""
    path = os.path.join(TESTS, f"{name}-noalias.s")
    if not os.path.exists(path):
        return None
    n = c = c_nops = 0
    with open(path) as fh:
        for line in fh:
            if not line.startswith("\t"):
                continue
            parts = line[1:].split(None, 1)
            mnem = parts[0] if line[1:].strip() else ""
            if not mnem or not mnem[0].islower():
                continue
            n += 1
            if mnem.startswith("c."):
                c += 1
                if mnem == "c.nop":
                    c_nops += 1
    return n, c, c_nops


def schedule(name):
    """(instructions, packets, pairs) from a scheduler run."""
    out = subprocess.run([sys.executable, os.path.join(ROOT, "__main__.py"),
                          os.path.join(TESTS, f"{name}.s")],
                         capture_output=True, text=True).stdout
    m = None
    for m in re.finditer(r"instructions: (\d+)\s+packets: (\d+)\s+pairs: (\d+)", out):
        pass
    if not m:
        return None
    pad = re.search(r"padding nops: (\d+) discarded", out)
    return tuple(int(g) for g in m.groups()) + (int(pad.group(1)) if pad else 0,)


def main():
    names = sys.argv[1:] or sorted(
        f[:-2] for f in os.listdir(TESTS)
        if f.endswith(".s") and not f.endswith("-noalias.s")
        and os.path.exists(os.path.join(TESTS, f[:-2] + "-noalias.s")))
    print(f"{'corpus':16}{'insns':>8}{'pairs':>8}{'pad':>6}{'packets':>9}"
          f"{'packet %':>10}{'real RVC':>10}{'vs RVC':>9}{'P/(C/2)':>9}"
          f"{'to parity':>11}")
    print("-" * 96)
    to_parity_total = 0
    for name in names:
        s, r = schedule(name), real_rvc(name)
        if not s or not r:
            print(f"{name:16} (missing scheduler result or -noalias variant)")
            continue
        N, packets, pairs, pad = s
        n_dis, comp, c_nops = r
        if comp == 0:
            # A no-C build has no real RVC to score against; those corpora
            # exist for the cross-build parity table (util/cross_parity.py).
            print(f"{name:16} (no compressed instructions — skipped; "
                  f"see cross_parity)")
            continue
        if n_dis != N + pad:
            print(f"{name:16} ! -noalias has {n_dis} instructions, "
                  f"scheduled file has {N}+{pad} discarded — not the same build")
        # Padding is ZERO-RATED ON BOTH SIDES. We discard the nops, so they
        # cost us nothing; RVC is not charged for them either, rather than us
        # banking the difference as a win. Neither scheme should be scored on
        # bytes that exist only to move the next thing onto a boundary, and
        # whether a packet ISA would really inherit the PLT's 16-byte stride
        # is a psABI question we do not get to decide by arithmetic.
        #
        # The books balance exactly: RVC drops 4 bytes per 32-bit pad (2 per
        # c.nop, of which there are none) and its instruction count drops by
        # the same `pad` our own already excludes, so `n_rvc == N` and the
        # break-even line returns to its plain form, P > C/2. The `pad` column
        # is now a fact about the corpus, not a term in the score.
        n_rvc = n_dis - pad
        rvc = 2 * comp + 4 * (n_rvc - comp) - (2 * min(c_nops, pad))
        comp_eff = comp - min(c_nops, pad)
        base, pk = 4 * N, 4 * packets
        rvc = rvc * N / n_rvc          # scale if the two files differ
        # Break-even: packets cost 4*(N-P), RVC costs 4N-2C, so packets win
        # exactly when P > C/2. This column is how many more pairs that takes;
        # negative means already past parity, by that margin.
        need = round(comp_eff / 2 * N / n_rvc) - pairs
        to_parity_total += need
        print(f"{name:16}{N:>8}{pairs:>8}{pad:>6}{packets:>9}"
              f"{100*pk/base:>9.1f}%{100*rvc/base:>9.1f}%"
              f"{100*pk/rvc:>8.1f}%{100*pairs/(comp_eff/2*N/n_rvc):>8.1f}%"
              f"{need:>+11}")
    print("-" * 96)
    print(f"{'TOTAL to parity':16}{to_parity_total:>+74}")
    print("\n`pad` is nops discarded as purposeless. They are ZERO-RATED ON "
          "BOTH SIDES: we do\nnot encode them and RVC is not charged for them "
          "either, so the column describes the\ncorpus and does not move the "
          "score.\n")
    print("packet % and real RVC are size against a 4-byte-per-instruction "
          "baseline;\nvs RVC under 100% means packets are smaller. P/(C/2) is "
          "progress toward the\nbreak-even point where pairs exceed half the "
          "compressed-instruction count.\n\n'to parity' is that same gap "
          "expressed in PAIRS: how many more this corpus\nneeds before packets "
          "beat real RVC. Negative means already past, by that\nmargin. It is "
          "the number to watch — every other column is a ratio.")


if __name__ == "__main__":
    main()

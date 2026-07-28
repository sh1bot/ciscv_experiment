"""
util/rules_conform.py — check scheduler/rules.py against encoding.yaml.

A first step toward generating rules.py from the yaml. This does NOT generate
anything: it reports, per frame, where the two disagree on the facts the yaml
owns. Deliberately narrow — it checks only what can be compared mechanically
today, and says plainly what it is not checking.

CHECKED
  * mnemonic sets      — the ops a frame's `ops` clusters allow per slot, vs the
                         rule's a_mnemonic_set / b_mnemonic_set;
  * immediate widths   — a declared `imm: {bits}` op contract vs the range the
                         rule's own check actually accepts, probed by binary
                         search over synthetic instructions;
  * frame coverage     — rules with no frame, frames with no rule.

NOT CHECKED (scheduler-owned, see scheduler/RULES.md and yaml_migration.md)
  deadness and chaining, operand-form constraints, register-class windows,
  order sensitivity, commutative operand fitting, relocation policy.

Usage:  python3 util/rules_conform.py [--verbose]
Exit status is 1 if any disagreement is found, so it can gate a commit.
"""
import os, sys

import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from scheduler.rules import RULES, NotPair
from util.encoding_render import op_name, op_contracts

# Pseudo-op names the yaml may use that are register-operand choices on a real
# mnemonic rather than opcodes of their own.
PSEUDO_BASE = {"li": "addi", "mv": "addi", "addi4spn": "addi",
               "addi_rsd": "addi", "addi_other": "addi",
               "j": "jal", "ret": "jalr", "beqz": "beq", "bnez": "bne"}


def frame_slot_ops(frame, slot):
    """Real mnemonics a frame allows in one slot, pseudo-ops mapped to the
    mnemonic that carries them."""
    out = set()
    for cluster in frame.get("ops") or []:
        for entry in cluster.get(slot, []):
            n = op_name(entry)
            out.add(PSEUDO_BASE.get(n, n))
    return out


def load_frames():
    spec = yaml.safe_load(open(os.path.join(ROOT, "encoding.yaml")))
    frames = {}
    for node in spec["doc"]:
        if "frame" not in node:
            continue
        f = node["frame"]
        for rn in f.get("rules_py_names") or [x.strip() for x in f["name"].split(",")]:
            frames[rn] = f
    return frames


def accepted_range(rule, mnemonic, slot, lo=-4096, hi=4096):
    """The contiguous immediate range `rule` accepts for `mnemonic` in `slot`,
    probed with synthetic instructions. Returns (lo, hi) or None if the rule
    never accepts the mnemonic at all (its other constraints may be what
    rejects it — this is a probe, not a proof)."""
    from isa.instruction import Instruction

    def mk(mn, imm):
        i = Instruction(mnemonic=mn, operands=[], raw=mn)
        i.rd, i.rs1, i.rs2, i.imm = 10, 10, 11, imm
        i.live_out = frozenset()
        return i

    def ok(imm):
        a = mk(mnemonic if slot == "a" else "add", imm if slot == "a" else 0)
        b = mk(mnemonic if slot == "b" else "add", imm if slot == "b" else 0)
        # feed a chain so chain-shaped rules can match: b reads a's dest
        b.rs1 = a.rd
        try:
            rule.check(a, b)
            return True
        except NotPair:
            return False
        except Exception:
            return False

    if not any(ok(v) for v in (0, 1, 2, 4, 8, 16, 31)):
        return None
    seen = [v for v in range(lo, hi + 1) if ok(v)]
    return (min(seen), max(seen)) if seen else None


def bits_to_range(bits, signed):
    if signed:
        return -(1 << (bits - 1)), (1 << (bits - 1)) - 1
    return 0, (1 << bits) - 1


def main():
    verbose = "--verbose" in sys.argv
    frames = load_frames()
    rules = {r.name: r for r in RULES}
    problems = 0

    for rn, rule in rules.items():
        if rn not in frames:
            print(f"✗ {rn}: no frame in encoding.yaml")
            problems += 1
    for rn in frames:
        if rn not in rules:
            print(f"✗ yaml frame names rule '{rn}', absent from RULES")
            problems += 1

    for rn, rule in rules.items():
        frame = frames.get(rn)
        if frame is None:
            continue
        notes = []
        # A frame naming several rules spreads its clusters across them, so a
        # per-slot set comparison is not meaningful for any one of them.
        multi = len(frame.get("rules_py_names") or []) > 1
        # When rules.py treats the slots symmetrically it accepts either order,
        # while the yaml lists only the canonical one; compare against the union.
        symmetric = (rule.a_mnemonic_set is not None
                     and rule.a_mnemonic_set == rule.b_mnemonic_set)
        for slot, attr in (("a", "a_mnemonic_set"), ("b", "b_mnemonic_set")):
            if multi:
                continue
            want = frame_slot_ops(frame, slot)
            if symmetric:
                want = frame_slot_ops(frame, "a") | frame_slot_ops(frame, "b")
            got = getattr(rule, attr)
            if not want or got is None:
                continue
            got = {PSEUDO_BASE.get(m, m) for m in got}
            if want != got:
                if want - got:
                    notes.append(f"{slot}: yaml has {sorted(want - got)}, rules.py lacks them")
                if got - want:
                    notes.append(f"{slot}: rules.py has {sorted(got - want)}, yaml lacks them")

        contracts = op_contracts(frame)
        for mn, c in contracts.items():
            bits = c.get("bits")
            if not bits or c.get("scale"):     # scaled fields need the width; skip
                continue
            base = PSEUDO_BASE.get(mn, mn)
            for slot in ("a", "b"):
                if base not in frame_slot_ops(frame, slot):
                    continue
                want = bits_to_range(bits, c.get("signed", True))
                got = accepted_range(rule, base, slot)
                if got is None:
                    if verbose:
                        notes.append(f"{slot}: {base} never accepted by the probe "
                                     f"(other constraints may gate it)")
                    continue
                if got != want:
                    notes.append(f"{slot}: {base} yaml {bits}b = {want}, "
                                 f"rules.py accepts {got}")
        if multi and verbose:
            print(f"· {rn}: frame '{frame['name']}' covers "
                  f"{frame['rules_py_names']}; op sets not compared per-rule")
        if notes:
            problems += 1
            print(f"✗ {rn}")
            for n in notes:
                print(f"    {n}")
        elif verbose:
            print(f"✓ {rn}")

    print(f"\n{problems} frame(s) disagree with encoding.yaml.")
    print("Only mnemonic sets and immediate widths are compared; deadness, "
          "chaining,\noperand form, register classes and ordering stay "
          "scheduler-owned.")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())

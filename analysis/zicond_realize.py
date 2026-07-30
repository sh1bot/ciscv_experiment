"""analysis/zicond_realize.py — what the scheduler actually gets for the Zicond
frames, as opposed to the adjacency ceiling.

`analysis/zicond_select.py` counts sites in the emitted order.  This runs the
real pipeline (liveness -> dependence graph -> list schedule -> greedy pair) with
candidate rules appended to `RULES`, so the reorderer is free to bring the four
instructions of a select together and the greedy pairer decides which partner
each czero actually takes.  Nothing in `scheduler/rules.py` or `encoding.yaml` is
touched — the rules live here, for measurement only.

Four configurations, each the previous one plus a rule, so the marginal pair
delta of each frame reads off the table:

  base                 today's RULES
  +back                `czero-select-or`   — FINDINGS §4 candidate 4
  +cond                `cond-czero-pair`   — the condition setup in the A slot
  +arm                 `chain-czero-pair`  — the arm value in the A slot instead

Rules are appended, so every existing frame keeps first refusal and the deltas
are honest marginals rather than re-attributions.

Usage:  python3 -m analysis.zicond_realize [--only +arm] [corpus.s ...]
"""

from __future__ import annotations
import os
import sys
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from analysis.parser import parse_file
from analysis.liveness import compute_global_liveness, compute_local_liveness
from analysis.depgraph import build_dep_graph
from scheduler.reorder import schedule, ScheduleMode
from scheduler.pairing import greedy_pair, stamp_slot_eligibility
from scheduler.rules import RULES, PairingRule, NotPair
from analysis.zicond_select import (CZERO, MAX_FIELDS, front_fields, arm_fields,
                                    back_fields, _needs_imm, imm_room)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TESTS = os.path.join(ROOT, "tests")

# Ops that may compute a condition in the A slot.  Deliberately generous: the
# field and immediate checks do the real filtering, and the op histogram in
# zicond_select says which entries a frame would actually have to buy.
_COND_MN = frozenset({
    "slt", "sltu", "slti", "sltiu", "xor", "xori", "and", "andi",
    "or", "ori", "sub", "subw", "add", "addi", "addw", "sext.w",
})
# Ops that may materialise an arm value: chain-alu-pair's A-slot set, which is
# where this shape would live if czero joined that frame's B-slot op list.
_ARM_MN = frozenset({
    "addi", "addiw", "andi", "add", "and", "or", "xor", "slli", "srli",
    "lui", "sub", "sext.w",
})


def _chain_escapes(reg: int, a, b) -> bool:
    """True if a's result survives the packet — the chain temp is not free."""
    return not (b.rd == reg or reg not in b.live_out)


def _czero_select_or(a, b) -> None:
    """A masks an arm; B ors it with the other arm, after which A's result dies.

    Fields: the surviving arm, the condition, A's result register, and B's
    destination — four at most, since A's temp is consumed inside the packet.
    """
    if a.rs2 is None or a.rd is None:
        raise NotPair("no-condition")
    if b.rs1 != a.rd and b.rs2 != a.rd:
        raise NotPair("no-chain")
    if _chain_escapes(a.rd, a, b):
        raise NotPair("escape")
    if back_fields(a, b) > MAX_FIELDS:
        raise NotPair("too-many-fields")


def _cond_czero_pair(a, b) -> None:
    """A computes the condition; B tests it.  The condition register is live out
    of the packet (the second czero reads it), so it is encoded, not a temp."""
    if a.rd is None or b.rs2 is None or b.rs2 != a.rd:
        raise NotPair("no-condition-chain")
    if a.base_from_auipc:
        raise NotPair("A-relocatable-offset")
    if _needs_imm(a):
        raise NotPair("no-imm-room")
    if front_fields(a, b) > MAX_FIELDS:
        raise NotPair("too-many-fields")


def _chain_czero_pair(a, b) -> None:
    """A materialises the value B masks; that value dies at B.  chain-alu-pair's
    shape with a czero in the B slot."""
    if a.rd is None or b.rs1 is None or b.rs1 != a.rd:
        raise NotPair("no-chain")
    if _chain_escapes(a.rd, a, b):
        raise NotPair("escape")
    # An auipc-fed addi is the low half of an address materialisation: its
    # immediate is a %pcrel_lo relocation that objdump has resolved into a
    # literal, so it cannot be narrowed.  Same policy as the load frames.
    if a.base_from_auipc:
        raise NotPair("A-relocatable-offset")
    fields = arm_fields(a, b)
    if fields > MAX_FIELDS:
        raise NotPair("too-many-fields")
    if not imm_room(a, fields):
        raise NotPair("big-imm")


BACK = PairingRule(name="czero-select-or", check=_czero_select_or,
                   a_mnemonic_set=CZERO, b_mnemonic_set=frozenset({"or"}))
COND = PairingRule(name="cond-czero-pair", check=_cond_czero_pair,
                   a_mnemonic_set=_COND_MN, b_mnemonic_set=CZERO)
ARM = PairingRule(name="chain-czero-pair", check=_chain_czero_pair,
                  a_mnemonic_set=_ARM_MN, b_mnemonic_set=CZERO)

CONFIGS = [
    ("base", []),
    ("+back", [BACK]),
    ("+cond", [BACK, COND]),
    ("+arm", [BACK, COND, ARM]),
]


def run(path: str, extra: list) -> tuple:
    """(instructions, packets, pairs, czero pair/total, per-rule hits) for one config."""
    base_len = len(RULES)
    RULES.extend(extra)
    try:
        insns = packets = pairs = 0
        czero_total = czero_paired = 0
        hits = Counter()
        _blocks, fns = parse_file(open(path).read())
        for fn in fns:
            for bl in fn.blocks:
                stamp_slot_eligibility(bl.instructions)
            gl = compute_global_liveness(fn.blocks)
            for bl in fn.blocks:
                if not bl.instructions:
                    continue
                compute_local_liveness(bl, gl)
                graph = build_dep_graph(bl)
                ordered = schedule(bl, graph, ScheduleMode.LIST)
                bl.instructions = ordered
                compute_local_liveness(bl, gl)
                czero_total += sum(1 for i in ordered if i.mnemonic in CZERO)
                for item in greedy_pair(ordered):
                    packets += 1
                    if item[0] == 'pair':
                        pairs += 1
                        insns += 2
                        hits[item[3]] += 1
                        czero_paired += sum(1 for i in item[1:3]
                                            if i.mnemonic in CZERO)
                    else:
                        insns += 1
        return insns, packets, pairs, czero_total, czero_paired, hits
    finally:
        del RULES[base_len:]


def realize_all(paths: list, only: str = None) -> None:
    """Run every configuration, or just the one named by `only`.

    Configurations are cumulative, so a change confined to the last rule can be
    re-measured with `--only +arm` and read against the `+cond` totals already
    recorded, instead of repeating the whole ladder.
    """
    print(f"\n{'corpus':14}{'config':8}{'insns':>8}{'packets':>9}{'pairs':>8}"
          f"{'vs base':>9}{'czero paired':>14}{'back':>7}{'cond':>7}{'arm':>7}")
    print("-" * 92)
    for path in paths:
        name = os.path.basename(path)[:-2]
        base_pairs = None
        for label, extra in CONFIGS:
            if only is not None and label != only:
                continue
            insns, packets, pairs, cz_all, cz_paired, hits = run(path, extra)
            if base_pairs is None:
                base_pairs = pairs
            share = f"{cz_paired}/{cz_all}"
            if cz_all:
                share += f" {100.0 * cz_paired / cz_all:.0f}%"
            print(f"{name:14}{label:8}{insns:>8}{packets:>9}{pairs:>8}"
                  f"{pairs - base_pairs:>+9}{share:>14}"
                  f"{hits['czero-select-or']:>7}{hits['cond-czero-pair']:>7}"
                  f"{hits['chain-czero-pair']:>7}")
            sys.stdout.flush()
        print("-" * 92)
    print("  vs base      = marginal pairs over today's RULES")
    print("  czero paired = czero instructions that landed in a packet")
    print("  back/cond/arm = pairs attributed to each candidate rule; existing")
    print("  frames keep first refusal, so hits can exceed the marginal delta.")


def main(argv: list) -> int:
    only = None
    paths = []
    it = iter(argv)
    for arg in it:
        if arg == "--only":
            only = next(it)
        else:
            paths.append(arg)
    if not paths:
        paths = [os.path.join(TESTS, f) for f in sorted(os.listdir(TESTS))
                 if f.endswith(".s") and not f.endswith("-noalias.s")]
    realize_all(paths, only)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

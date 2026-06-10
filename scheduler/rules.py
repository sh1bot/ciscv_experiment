"""
scheduler/rules.py — Pairing policy: rule definitions and slot disqualifiers.

This is the only file that needs to change when iterating on pairing policy.
The mechanism (can_pair, greedy_pair, stamp_slot_eligibility) lives in pairing.py.
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Callable, Optional

from isa.instruction import Instruction


# ---------------------------------------------------------------------------
# PairingRule dataclass
# ---------------------------------------------------------------------------

@dataclass
class PairingRule:
    name: str

    # check(a, b) -> None means encoding accepts; str -> encoding rejects (reason).
    check: Callable

    # Properties that must be True on a for the rule to be applicable.
    a_prerequisites: list = field(default_factory=list)

    # Properties that must be True on b for the rule to be applicable.
    b_prerequisites: list = field(default_factory=list)


# ---------------------------------------------------------------------------
# Rule implementations
# ---------------------------------------------------------------------------

def _rsd_alu_pair(a: Instruction, b: Instruction) -> Optional[str]:
    """Both instructions are two-register ALU ops with dest=src1.

    rd==rs2 (instead of rd==rs1) is only valid for commutative operations,
    where the operands can be swapped in the encoding.
    """
    supported = {"add", "sub", "and", "or", "xor", "addw", "subw"}
    for slot, insn in (("A", a), ("B", b)):
        if insn.mnemonic not in supported:
            return f"rsd-alu-pair: {slot}-slot mnemonic not in supported set ({insn.mnemonic})"
        if insn.rd != insn.rs1 and not insn.is_commutative:
            return f"rsd-alu-pair: {slot}-slot rd==rs2 but {insn.mnemonic} is not commutative"
    return None


# ---------------------------------------------------------------------------
# Policy tables — edit these when iterating on pairing rules
# ---------------------------------------------------------------------------

RULES: list[PairingRule] = [
    PairingRule(
        name="rsd-alu-pair",
        a_prerequisites=["is_rsd"],
        b_prerequisites=["is_rsd"],
        check=_rsd_alu_pair,
    ),
]

A_SLOT_DISQUALIFIERS: list[str] = [
    "is_unknown",
]

B_SLOT_DISQUALIFIERS: list[str] = [
    "is_unknown",
]

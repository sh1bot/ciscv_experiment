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

_RSD_ALU_MN = frozenset({
    "addi", "andi",                          # immediate forms (nzimm, -64..64)
    "add",  "addw",
    "sub",  "subw",
    "and",  "andn",
    "or",   "xor",
})
_RSD_IMM_MN   = frozenset({"addi", "andi"})  # ops that carry an immediate
_RSD_IMM_LO, _RSD_IMM_HI = -64, 64          # signed, nonzero


def _rsd_alu_pair(a: Instruction, b: Instruction) -> Optional[str]:
    """Paired ALU ops: RSD form, immediates -64..64 nonzero.

    Immediate-form ops (addi, andi) require a nonzero immediate in the
    range -64..64.  R-type ops allow rd==rs2 only when commutative.
    """
    for slot, insn in (("A", a), ("B", b)):
        if insn.mnemonic not in _RSD_ALU_MN:
            return f"rsd-alu-pair: {slot}-slot mnemonic not in supported set ({insn.mnemonic})"
        if insn.mnemonic in _RSD_IMM_MN:
            imm = insn.imm
            if imm is None or imm == 0 or not (_RSD_IMM_LO <= imm <= _RSD_IMM_HI):
                return f"rsd-alu-pair: {slot}-slot immediate must be nonzero and in {_RSD_IMM_LO}..{_RSD_IMM_HI} (got {imm})"
        elif insn.rd != insn.rs1 and not insn.is_commutative:
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

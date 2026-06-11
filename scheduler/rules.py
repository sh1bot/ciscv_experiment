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

    # Per-slot self-diagnosis: diagnose_a(insn) / diagnose_b(insn) -> reason str
    # or None if the instruction passes all per-slot constraints for that slot.
    # Used by stamp_solo_reasons() to explain why an instruction can never pair
    # in a given slot, independent of any specific partner.
    diagnose_a: Optional[Callable] = None
    diagnose_b: Optional[Callable] = None


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
_RSD_ALU_REGS = frozenset(range(16))         # x0..x15 (4-bit register field)
_RSD_IMM_MN   = frozenset({"addi", "andi"})  # ops that carry an immediate
_RSD_IMM_LO, _RSD_IMM_HI = -64, 64          # signed, nonzero


def _rsd_alu_diagnose(insn: Instruction) -> Optional[str]:
    """Per-slot self-diagnosis for rsd-alu-pair (slot-symmetric)."""
    if not insn.is_rsd:
        return "rsd-alu-pair: not RSD form"
    if insn.mnemonic not in _RSD_ALU_MN:
        return f"rsd-alu-pair: unsupported mnemonic ({insn.mnemonic})"
    for reg, fname in ((insn.rd, "rd"), (insn.rs1, "rs1")):
        if reg is not None and reg not in _RSD_ALU_REGS:
            return f"rsd-alu-pair: {fname} (x{reg}) not in x0..x15"
    if insn.rs2 is not None and insn.rs2 not in _RSD_ALU_REGS:
        return f"rsd-alu-pair: rs2 (x{insn.rs2}) not in x0..x15"
    if insn.mnemonic in _RSD_IMM_MN:
        imm = insn.imm
        if imm is None or imm == 0 or not (_RSD_IMM_LO <= imm <= _RSD_IMM_HI):
            return f"rsd-alu-pair: immediate out of range (got {imm}, need nonzero {_RSD_IMM_LO}..{_RSD_IMM_HI})"
    elif insn.rd != insn.rs1 and not insn.is_commutative:
        return f"rsd-alu-pair: rd==rs2 but {insn.mnemonic} is not commutative"
    return None


def _rsd_alu_pair(a: Instruction, b: Instruction) -> Optional[str]:
    """Paired ALU ops: RSD form, registers in x0..x15, immediates -64..64 nonzero.

    Immediate-form ops (addi, andi) require a nonzero immediate in the
    range -64..64.  R-type ops allow rd==rs2 only when commutative.

    Per-slot constraints are already checked by _rsd_alu_diagnose; this
    function only runs when both instructions pass their per-slot diagnosis.
    """
    for slot, insn in (("A", a), ("B", b)):
        reason = _rsd_alu_diagnose(insn)
        if reason is not None:
            return f"{slot}-slot: {reason}"
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
        diagnose_a=_rsd_alu_diagnose,
        diagnose_b=_rsd_alu_diagnose,
    ),
]

A_SLOT_DISQUALIFIERS: list[str] = [
    "is_unknown",
]

B_SLOT_DISQUALIFIERS: list[str] = [
    "is_unknown",
]

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
# Shared helpers
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


def _alu_diagnose(rule_name: str, insn: Instruction) -> Optional[str]:
    """Per-slot self-diagnosis shared by both ALU pairing rules.

    Checks mnemonic membership, register range, and immediate constraints.
    Does NOT check RSD form — that is rsd-alu-pair's additional requirement.
    """
    if insn.mnemonic not in _RSD_ALU_MN:
        return f"{rule_name}: unsupported mnemonic ({insn.mnemonic})"
    for reg, fname in ((insn.rd, "rd"), (insn.rs1, "rs1")):
        if reg is not None and reg not in _RSD_ALU_REGS:
            return f"{rule_name}: {fname} (x{reg}) not in x0..x15"
    if insn.rs2 is not None and insn.rs2 not in _RSD_ALU_REGS:
        return f"{rule_name}: rs2 (x{insn.rs2}) not in x0..x15"
    if insn.mnemonic in _RSD_IMM_MN:
        imm = insn.imm
        if imm is None or imm == 0 or not (_RSD_IMM_LO <= imm <= _RSD_IMM_HI):
            return f"{rule_name}: immediate out of range (got {imm}, need nonzero {_RSD_IMM_LO}..{_RSD_IMM_HI})"
    return None


# ---------------------------------------------------------------------------
# rsd-alu-pair
# ---------------------------------------------------------------------------

def _rsd_alu_diagnose(insn: Instruction) -> Optional[str]:
    reason = _alu_diagnose("rsd-alu-pair", insn)
    if reason:
        return reason
    if not insn.is_rsd:
        return "rsd-alu-pair: not RSD form"
    if insn.rd != insn.rs1 and not insn.is_commutative:
        return "rsd-alu-pair: rd==rs2 but not commutative"
    return None


def _rsd_alu_pair(a: Instruction, b: Instruction) -> Optional[str]:
    """Both instructions RSD form, x0..x15, immediates nonzero -64..64."""
    for insn in (a, b):
        r = _rsd_alu_diagnose(insn)
        if r:
            return r
    return None


# ---------------------------------------------------------------------------
# chain-alu-pair
# ---------------------------------------------------------------------------

def _chain_alu_diagnose(insn: Instruction) -> Optional[str]:
    """Per-slot self-diagnosis for chain-alu-pair (slot-symmetric).

    No RSD requirement — chain-alu-pair allows free choice of rd and rs1.
    """
    return _alu_diagnose("chain-alu-pair", insn)


def _chain_alu_pair(a: Instruction, b: Instruction) -> Optional[str]:
    """A computes a value that B immediately consumes; that value is dead after B.

    A has free choice of rd and rs1.  B must use A's rd as its rs1 input
    (or rs2 if B is commutative).  A's rd must be dead after B — either B
    overwrites it (b.rd == a.rd) or it is not live in b.live_out.
    """
    for insn in (a, b):
        r = _chain_alu_diagnose(insn)
        if r:
            return r
    if a.rd is None:
        return "chain-alu-pair: A has no destination register"
    uses_chain = (b.rs1 == a.rd) or (b.is_commutative and b.rs2 == a.rd)
    if not uses_chain:
        return f"chain-alu-pair: B does not consume A's result (x{a.rd})"
    if b.rd != a.rd and a.rd in b.live_out:
        return f"chain-alu-pair: A's result (x{a.rd}) escapes after B"
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
    PairingRule(
        name="chain-alu-pair",
        check=_chain_alu_pair,
        diagnose_a=_chain_alu_diagnose,
        diagnose_b=_chain_alu_diagnose,
    ),
]

A_SLOT_DISQUALIFIERS: list[str] = [
    "is_unknown",
]

B_SLOT_DISQUALIFIERS: list[str] = [
    "is_unknown",
]

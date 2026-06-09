"""
scheduler/pairing.py — Pairing rules and can_pair() function.

The pairing model is encoding-based allowlist: a pair (a, b) is valid iff at
least one PairingRule accepts it. can_pair() returns None on success or a
reason string if all applicable rules reject.

Within a packet, a executes first (A-slot) and b executes second (B-slot).
Pairing rules express only hardware structural constraints — not register
data-dependency constraints (those are handled by the dependency graph).
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
# Pairing rules
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


RULES: list[PairingRule] = [
    PairingRule(
        name="rsd-alu-pair",
        a_prerequisites=["is_rsd"],
        b_prerequisites=["is_rsd"],
        check=_rsd_alu_pair,
    ),
]


# ---------------------------------------------------------------------------
# can_pair()
# ---------------------------------------------------------------------------

def can_pair(a: Instruction, b: Instruction) -> Optional[str]:
    """Return None if a and b may share a 32-bit packet, or a reason string if not."""
    if a.is_unknown:
        return "A-slot disqualified: is_unknown"
    if b.is_unknown:
        return "B-slot disqualified: is_unknown"
    reasons: list = []
    for rule in RULES:
        if not all(getattr(a, p) for p in rule.a_prerequisites):
            continue
        if not all(getattr(b, p) for p in rule.b_prerequisites):
            continue
        # Rule is applicable — check it
        result = rule.check(a, b)
        if result is None:
            return None   # encoding accepts — pair is valid
        reasons.append(result)

    if reasons:
        return "; ".join(reasons)
    return "no applicable encoding"


# ---------------------------------------------------------------------------
# Greedy-advance pairing pass
# ---------------------------------------------------------------------------

def greedy_pair(instructions: list[Instruction]) -> list:
    """Apply the greedy-advance pairing model to an ordered instruction list.

    Returns a list of items, each either:
      ('solo', insn) or ('pair', insn_a, insn_b)
    """
    result = []
    free = None

    for curr in instructions:
        if free is not None:
            reason = can_pair(free, curr)
            if reason is None:
                result.append(('pair', free, curr))
                free = None
            else:
                # Record rejection reasons per solo_reasons policy
                _record_solo_reasons(free, curr, reason)
                result.append(('solo', free))
                free = curr
        else:
            free = curr

    if free is not None:
        result.append(('solo', free))

    return result


def _record_solo_reasons(free: Instruction, curr: Instruction, reason: str) -> None:
    """Record rejection reasons onto both instructions."""
    free.solo_reasons.add(reason)
    curr.solo_reasons.add(reason)

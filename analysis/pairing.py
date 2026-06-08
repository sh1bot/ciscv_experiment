"""
analysis/pairing.py — Pairing rules and can_pair() function.

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

    # Properties that must be True on a for the rule to be applicable.
    a_prerequisites: list = field(default_factory=list)

    # Properties that must be True on b for the rule to be applicable.
    b_prerequisites: list = field(default_factory=list)

    # check(a, b) -> None means encoding accepts; str -> encoding rejects (reason).
    check: Callable = None


# ---------------------------------------------------------------------------
# Slot disqualifiers
# ---------------------------------------------------------------------------

A_SLOT_DISQUALIFIERS = [
    "is_branch",   # branch in A-slot: processor leaves before B executes
    "is_jump",     # unconditional jumps also transfer control
    "is_return",
    "is_call",     # calls transfer control
    "is_tail",     # tail-call pseudo transfers control
]

B_SLOT_DISQUALIFIERS = [
    # none initially
]


# ---------------------------------------------------------------------------
# Pairing rules
# ---------------------------------------------------------------------------

def _rsd_alu_pair(a: Instruction, b: Instruction) -> Optional[str]:
    """Both instructions are two-register ALU ops with dest=src1."""
    supported = {"add", "sub", "and", "or", "xor", "addw", "subw"}
    if a.mnemonic in supported and b.mnemonic in supported:
        return None
    return f"rsd-alu-pair: mnemonic not in supported set ({a.mnemonic}, {b.mnemonic})"


def _alu_branch_pair(a: Instruction, b: Instruction) -> Optional[str]:
    """ALU in A-slot, branch in B-slot."""
    # A-slot: register ALU instruction (has rd, uses rs1/rs2, no memory)
    if a.reads_memory or a.writes_memory or a.has_side_effects:
        return "alu-branch-pair: A-slot has side effects or memory access"
    if a.rd is None:
        return "alu-branch-pair: A-slot has no destination register"
    if not b.is_branch:
        return "alu-branch-pair: B-slot is not a branch"
    return None


def _load_alu_pair(a: Instruction, b: Instruction) -> Optional[str]:
    """Load in A-slot, ALU in B-slot."""
    if not a.reads_memory:
        return "load-alu-pair: A-slot is not a load"
    if b.reads_memory or b.writes_memory or b.has_side_effects:
        return "load-alu-pair: B-slot has memory/side-effect"
    if b.rd is None:
        return "load-alu-pair: B-slot has no dest"
    return None


def _alu_load_pair(a: Instruction, b: Instruction) -> Optional[str]:
    """ALU in A-slot, load in B-slot."""
    if a.reads_memory or a.writes_memory or a.has_side_effects:
        return "alu-load-pair: A-slot has side effects"
    if a.rd is None:
        return "alu-load-pair: A-slot has no dest"
    if not b.reads_memory:
        return "alu-load-pair: B-slot is not a load"
    return None


def _alu_store_pair(a: Instruction, b: Instruction) -> Optional[str]:
    """ALU in A-slot, store in B-slot."""
    if a.reads_memory or a.writes_memory or a.has_side_effects:
        return "alu-store-pair: A-slot has side effects"
    if a.rd is None:
        return "alu-store-pair: A-slot has no dest"
    if not b.writes_memory:
        return "alu-store-pair: B-slot is not a store"
    return None


def _store_alu_pair(a: Instruction, b: Instruction) -> Optional[str]:
    """Store in A-slot, ALU in B-slot."""
    if not a.writes_memory:
        return "store-alu-pair: A-slot is not a store"
    if b.reads_memory or b.writes_memory or b.has_side_effects:
        return "store-alu-pair: B-slot has side effects"
    if b.rd is None:
        return "store-alu-pair: B-slot has no dest"
    return None


def _alu_alu_pair(a: Instruction, b: Instruction) -> Optional[str]:
    """Two general ALU instructions (not both must be rsd)."""
    if a.reads_memory or a.writes_memory or a.has_side_effects:
        return "alu-alu-pair: A-slot has side effects"
    if b.reads_memory or b.writes_memory or b.has_side_effects:
        return "alu-alu-pair: B-slot has side effects"
    if a.rd is None:
        return "alu-alu-pair: A-slot has no dest"
    if b.rd is None:
        return "alu-alu-pair: B-slot has no dest"
    return None


RULES: list[PairingRule] = [
    PairingRule(
        name="rsd-alu-pair",
        a_prerequisites=["is_rsd"],
        b_prerequisites=["is_rsd"],
        check=_rsd_alu_pair,
    ),
    PairingRule(
        name="alu-branch-pair",
        a_prerequisites=[],
        b_prerequisites=["is_branch"],
        check=_alu_branch_pair,
    ),
    PairingRule(
        name="load-alu-pair",
        a_prerequisites=["reads_memory"],
        b_prerequisites=[],
        check=_load_alu_pair,
    ),
    PairingRule(
        name="alu-load-pair",
        a_prerequisites=[],
        b_prerequisites=["reads_memory"],
        check=_alu_load_pair,
    ),
    PairingRule(
        name="alu-store-pair",
        a_prerequisites=[],
        b_prerequisites=["writes_memory"],
        check=_alu_store_pair,
    ),
    PairingRule(
        name="store-alu-pair",
        a_prerequisites=["writes_memory"],
        b_prerequisites=[],
        check=_store_alu_pair,
    ),
    PairingRule(
        name="alu-alu-pair",
        a_prerequisites=[],
        b_prerequisites=[],
        check=_alu_alu_pair,
    ),
]


# ---------------------------------------------------------------------------
# can_pair()
# ---------------------------------------------------------------------------

def can_pair(a: Instruction, b: Instruction) -> Optional[str]:
    """Return None if a and b may share a 32-bit packet, or a reason string if not."""
    # Per-slot disqualifiers: fast-out
    for prop in A_SLOT_DISQUALIFIERS:
        if getattr(a, prop):
            return f"A-slot disqualified: {prop}"
    for prop in B_SLOT_DISQUALIFIERS:
        if getattr(b, prop):
            return f"B-slot disqualified: {prop}"

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
        reasons.append(f"{rule.name}: {result}")

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
    """Record rejection reasons onto free and curr per §10 policy."""
    # A-slot disqualifier reasons go to free only
    # B-slot disqualifier reasons go to curr only
    # Rule-check rejections go to both
    if reason.startswith("A-slot disqualified:"):
        free.solo_reasons.add(reason)
    elif reason.startswith("B-slot disqualified:"):
        curr.solo_reasons.add(reason)
    else:
        free.solo_reasons.add(reason)
        curr.solo_reasons.add(reason)

"""
scheduler/pairing.py — Pairing mechanism: can_pair(), greedy_pair(), stamp_slot_eligibility().

The pairing model is encoding-based allowlist: a pair (a, b) is valid iff at
least one PairingRule accepts it. can_pair() returns None on success or a
reason string if all applicable rules reject.

Within a packet, a executes first (A-slot) and b executes second (B-slot).
Pairing rules express only hardware structural constraints — not register
data-dependency constraints (those are handled by the dependency graph).

Policy (rules, disqualifier lists) lives in scheduler/rules.py.
"""

from __future__ import annotations
from typing import Optional

from isa.instruction import Instruction
from scheduler.rules import RULES, A_SLOT_DISQUALIFIERS, B_SLOT_DISQUALIFIERS

# Re-export PairingRule so callers that imported it from here still work.
from scheduler.rules import PairingRule  # noqa: F401


def stamp_slot_eligibility(instructions: list[Instruction]) -> None:
    """Precompute a_slot_ok / b_slot_ok on each instruction.

    Disqualifiers are intrinsic to the instruction — they depend only on the
    instruction's own properties, never on its neighbours or position.  This
    pass runs once after parsing so that scheduling and pairing code can read
    the flags cheaply without re-evaluating the disqualifier lists.
    """
    for insn in instructions:
        insn.a_slot_ok = not any(getattr(insn, p) for p in A_SLOT_DISQUALIFIERS)
        insn.b_slot_ok = not any(getattr(insn, p) for p in B_SLOT_DISQUALIFIERS)


def can_pair(a: Instruction, b: Instruction) -> Optional[str]:
    """Return None if a and b may share a 32-bit packet, or a reason string if not."""
    if not a.a_slot_ok:
        for prop in A_SLOT_DISQUALIFIERS:
            if getattr(a, prop):
                return f"A-slot disqualified: {prop}"
    if not b.b_slot_ok:
        for prop in B_SLOT_DISQUALIFIERS:
            if getattr(b, prop):
                return f"B-slot disqualified: {prop}"
    reasons: list = []
    for rule in RULES:
        if not all(getattr(a, p) for p in rule.a_prerequisites):
            continue
        if not all(getattr(b, p) for p in rule.b_prerequisites):
            continue
        result = rule.check(a, b)
        if result is None:
            return None   # encoding accepts — pair is valid
        reasons.append(result)

    if reasons:
        return "; ".join(reasons)
    return "no applicable encoding"


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

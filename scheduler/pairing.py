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


def stamp_solo_reasons(instructions: list[Instruction]) -> None:
    """Precompute per-instruction solo reasons from rule self-diagnosis.

    Checks mnemonic_set membership, then runs diagnose_a / diagnose_b against
    every instruction and records reasons on the instruction itself.
    """
    for insn in instructions:
        for rule in RULES:
            if rule.mnemonic_set is not None and insn.mnemonic not in rule.mnemonic_set:
                insn.solo_reasons.add(f"{rule.name}: unsupported mnemonic ({insn.mnemonic})")
                continue
            if rule.diagnose_a is not None:
                reason = rule.diagnose_a(insn)
                if reason is not None:
                    insn.solo_reasons.add(reason)
            if rule.diagnose_b is not None and rule.diagnose_b is not rule.diagnose_a:
                reason = rule.diagnose_b(insn)
                if reason is not None:
                    insn.solo_reasons.add(reason)


def can_pair(a: Instruction, b: Instruction) -> Optional[str]:
    """Return None if a and b may share a 32-bit packet, or a reason string if not.

    Callers must ensure a.a_slot_ok and b.b_slot_ok before calling; disqualified
    instructions must be handled upstream and never passed here.
    """
    _rule, reason = _try_pair(a, b)
    return reason


def _try_pair(a: Instruction, b: Instruction):
    """Return (rule, None) on success or (None, reason_str) on failure."""
    reasons: list = []
    for rule in RULES:
        if rule.mnemonic_set is not None:
            if a.mnemonic not in rule.mnemonic_set or b.mnemonic not in rule.mnemonic_set:
                continue
        if not all(getattr(a, p) for p in rule.a_prerequisites):
            continue
        if not all(getattr(b, p) for p in rule.b_prerequisites):
            continue
        result = rule.check(a, b)
        if result is None:
            return rule, None   # encoding accepts — pair is valid
        reasons.append(result)

    if reasons:
        return None, "; ".join(reasons)
    return None, "no applicable encoding"


def greedy_pair(instructions: list[Instruction]) -> list:
    """Apply the greedy-advance pairing model to an ordered instruction list.

    Returns a list of items, each either:
      ('solo', insn) or ('pair', insn_a, insn_b)
    """
    result = []
    free = None

    for curr in instructions:
        if free is not None:
            if not free.a_slot_ok or not curr.b_slot_ok:
                # Slot-disqualified instruction cannot participate in this pair.
                # Record the reason on the disqualified instruction only.
                if not free.a_slot_ok:
                    for prop in A_SLOT_DISQUALIFIERS:
                        if getattr(free, prop):
                            free.solo_reasons.add(f"A-slot disqualified: {prop}")
                            break
                if not curr.b_slot_ok:
                    for prop in B_SLOT_DISQUALIFIERS:
                        if getattr(curr, prop):
                            curr.solo_reasons.add(f"B-slot disqualified: {prop}")
                            break
                result.append(('solo', free))
                free = curr
            else:
                rule, reason = _try_pair(free, curr)
                if rule is not None:
                    result.append(('pair', free, curr, rule.name))
                    free = None
                else:
                    free.solo_reasons.add(reason)
                    curr.solo_reasons.add(reason)
                    result.append(('solo', free))
                    free = curr
        else:
            free = curr

    if free is not None:
        result.append(('solo', free))

    return result

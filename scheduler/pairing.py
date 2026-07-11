"""
scheduler/pairing.py — Pairing mechanism: can_pair(), greedy_pair(), stamp_slot_eligibility().

The pairing model is encoding-based allowlist: a pair (a, b) is valid iff at
least one PairingRule accepts it. A rule's check() raises NotPair(reason) to
reject a candidate and returns None to accept it; this module catches NotPair
at each check() call site. can_pair() returns None on success or a joined
reason string if all applicable rules reject.

Within a packet, a executes first (A-slot) and b executes second (B-slot).
Pairing rules express only hardware structural constraints — not register
data-dependency constraints (those are handled by the dependency graph).

Policy (rules, disqualifier lists) lives in scheduler/rules.py.
"""

from __future__ import annotations
from typing import Optional

from isa.instruction import Instruction
from scheduler.rules import RULES, A_SLOT_DISQUALIFIERS, B_SLOT_DISQUALIFIERS, ALL_BRANCH_MN, NotPair

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


def _a_eligible_rules(a: Instruction) -> list:
    """Return the subset of RULES for which a passes A-slot mnemonic and prerequisites.

    This is computed once per A candidate and reused across all B candidates,
    avoiding redundant re-evaluation of A's properties in the inner loop.
    """
    eligible = []
    for rule in RULES:
        if rule.a_mnemonic_set is not None and a.mnemonic not in rule.a_mnemonic_set:
            continue
        if not all(getattr(a, p) for p in rule.a_prerequisites):
            continue
        eligible.append(rule)
    return eligible


def find_b_partners(a: Instruction, candidates: list[Instruction]) -> list[tuple]:
    """Return [(b, rule), ...] for each candidate that forms a valid pair with a.

    A's eligible rules are computed once; each B candidate is tested only
    against those rules.  If a is ineligible for all rules the result is always
    empty and no B candidates are annotated.
    """
    eligible = _a_eligible_rules(a)
    if not eligible:
        return []
    results = []
    for b in candidates:
        if not b.b_slot_ok:
            continue
        for rule in eligible:
            if rule.b_mnemonic_set is not None and b.mnemonic not in rule.b_mnemonic_set:
                continue
            if not all(getattr(b, p) for p in rule.b_prerequisites):
                continue
            try:
                rule.check(a, b)
            except NotPair:
                continue
            results.append((b, rule))
            break
    return results


def can_pair(a: Instruction, b: Instruction) -> Optional[str]:
    """Return None if a and b may share a 32-bit packet, or a reason string if not.

    Callers must ensure a.a_slot_ok and b.b_slot_ok before calling; disqualified
    instructions must be handled upstream and never passed here.
    """
    eligible = _a_eligible_rules(a)
    if not eligible:
        return "no applicable encoding"
    reasons: list = []
    for rule in eligible:
        if rule.b_mnemonic_set is not None and b.mnemonic not in rule.b_mnemonic_set:
            continue
        if not all(getattr(b, p) for p in rule.b_prerequisites):
            continue
        try:
            rule.check(a, b)
            return None
        except NotPair as exc:
            reasons.append(exc.reason)
    if reasons:
        return "; ".join(reasons)
    return "no applicable encoding"


def greedy_pair(instructions: list[Instruction]) -> list:
    """Apply the greedy-advance pairing model to an ordered instruction list.

    Returns a list of items, each either:
      ('solo', insn) or ('pair', insn_a, insn_b, rule_name)
    """
    result = []
    free = None
    insns = list(instructions)
    n = len(insns)
    i = 0

    while i < n:
        curr = insns[i]
        if free is not None:
            if not free.a_slot_ok:
                for prop in A_SLOT_DISQUALIFIERS:
                    if getattr(free, prop):
                        free.solo_reasons["A-slot DQ"].add(prop)
                        break
                result.append(('solo', free))
                free = curr
                i += 1
            elif not curr.b_slot_ok:
                for prop in B_SLOT_DISQUALIFIERS:
                    if getattr(curr, prop):
                        curr.solo_reasons["B-slot DQ"].add(prop)
                        break
                result.append(('solo', free))
                free = curr
                i += 1
            else:
                matches = find_b_partners(free, [curr])
                if matches:
                    _b, rule = matches[0]
                    result.append(('pair', free, curr, rule.name))
                    free = None
                    i += 1
                else:
                    # Annotate curr only when free had eligible rules.
                    # For each such rule report why curr failed it: failed
                    # prerequisites (by name) or the check() reason string.
                    eligible = _a_eligible_rules(free)
                    if eligible:
                        for rule in eligible:
                            if rule.b_mnemonic_set is not None and curr.mnemonic not in rule.b_mnemonic_set:
                                curr.solo_reasons["mnemonic not supported"].add(rule.name)
                                continue
                            failed = [p for p in rule.b_prerequisites if not getattr(curr, p)]
                            if failed:
                                for p in failed:
                                    curr.solo_reasons[p].update(f"{rule.name}: {p}" for p in failed)
                                continue
                            try:
                                rule.check(free, curr)
                            except NotPair as exc:
                                curr.solo_reasons[exc.reason].add(rule.name)
                    result.append(('solo', free))
                    free = curr
                    i += 1
        else:
            free = curr
            i += 1

    if free is not None:
        result.append(('solo', free))

    return _backward_pair(result)


def _backward_pair(packets: list) -> list:
    """Second pass: scan backwards over solos looking for branch B-slot pairs.

    For each solo branch, scan back through preceding solos for a valid A-slot
    partner.  Only claim the pair if no intervening instruction reads the
    candidate's output or writes any of its inputs (i.e. moving the candidate
    later is safe).
    """
    # Work on a mutable copy; track which entries are consumed.
    result = list(packets)
    n = len(result)
    consumed = [False] * n

    for j in range(n - 1, -1, -1):
        if consumed[j] or result[j][0] != 'solo':
            continue
        branch = result[j][1]
        if branch.mnemonic not in ALL_BRANCH_MN or not branch.b_slot_ok:
            continue

        for k in range(j - 1, -1, -1):
            if consumed[k] or result[k][0] != 'solo':
                continue
            cand = result[k][1]
            if not find_b_partners(cand, [branch]):
                continue

            # Safety check: nothing between k and j reads cand's output
            # or writes any register cand reads.
            cand_rd   = cand.rd
            cand_uses = cand.uses_regs
            conflict = False
            for m in range(k + 1, j):
                if consumed[m]:
                    continue
                p = result[m]
                insns = [p[1]] if p[0] == 'solo' else [p[1], p[2]]
                for insn in insns:
                    if cand_rd is not None and cand_rd in insn.uses_regs:
                        conflict = True
                        break
                    if insn.rd is not None and insn.rd in cand_uses:
                        conflict = True
                        break
                if conflict:
                    break

            if not conflict:
                rule_name = find_b_partners(cand, [branch])[0][1].name
                result[k] = ('pair', cand, branch, rule_name)
                consumed[j] = True
                break

    return [p for i, p in enumerate(result) if not consumed[i]]

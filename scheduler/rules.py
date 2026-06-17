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

    # Per-slot mnemonic allowlists: checked before prerequisites and check(),
    # like slot-specific prerequisites for mnemonic membership.
    a_mnemonic_set: Optional[frozenset] = None
    b_mnemonic_set: Optional[frozenset] = None

    # Properties that must be True on a for the rule to be applicable.
    a_prerequisites: list = field(default_factory=list)

    # Properties that must be True on b for the rule to be applicable.
    b_prerequisites: list = field(default_factory=list)

    # Per-slot self-diagnosis: diagnose_a(insn) / diagnose_b(insn) -> reason str
    # or None if the instruction passes all per-slot constraints for that slot.
    # Called after mnemonic_set passes; need not re-check mnemonic membership.
    diagnose_a: Optional[Callable] = None
    diagnose_b: Optional[Callable] = None


# ---------------------------------------------------------------------------
# Shared constants
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


# ---------------------------------------------------------------------------
# Shared per-slot helpers (mnemonic already confirmed by rule.mnemonic_set)
# ---------------------------------------------------------------------------

def _alu_diagnose_regs_imm(rule_name: str, insn: Instruction) -> Optional[str]:
    """Check register range and immediate constraints only; mnemonic already ok."""
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
    reason = _alu_diagnose_regs_imm("rsd-alu-pair", insn)
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
    """Per-slot self-diagnosis for chain-alu-pair (no RSD requirement)."""
    return _alu_diagnose_regs_imm("chain-alu-pair", insn)


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
# dual-op-pair
# ---------------------------------------------------------------------------
# Two instructions drawn from the same canonical opcode tuple that share their
# input operands, perform different operations, and (where both have one) write
# distinct output registers.  The pair packs into a single 32-bit word.
#
# Match kinds (per tuple):
#   "arith2"      both R-type; share rs1, rs2 positionally; two distinct dests.
#   "load_addi"   load + addi; share the base register and the (width-scaled)
#                 immediate offset; two distinct dests.
#   "load_shadd"  load + shNadd; share the base register only, with the load
#                 offset forced to zero — a register post-increment form.
#   "store_shadd" store + shNadd; the store's {base, value} registers equal the
#                 shadd's two source registers; the store carries the
#                 (width-scaled) offset immediate.
#
# Width-scaled immediate (load_addi, store_shadd): the memory op implies a data
# width; the shared/offset immediate must be a nonzero unsigned 5-bit value with
# remap, scaled by that width — i.e. a nonzero multiple of the width in
# [width, 32*width].  This trades granularity for reach.
#
# Canonical order is (tuple[0], tuple[1]).  The reverse order is accepted only
# when the two instructions are fully independent (neither destination is a
# source operand of the pair).  In canonical order the B-slot instruction may
# write one of the shared source registers (a WAR that resolves correctly
# because B executes second); the reverse order may not.

_DUAL_TUPLES: dict = {
    # arith2 — sum/difference, min/max, mul hi/lo, div/rem (+ unsigned, word)
    ("add", "sub"):       "arith2",
    ("addw", "subw"):     "arith2",
    ("min", "max"):       "arith2",
    ("minu", "maxu"):     "arith2",
    ("mul", "mulh"):      "arith2",
    ("mul", "mulhu"):     "arith2",
    ("mul", "mulhsu"):    "arith2",
    ("div", "rem"):       "arith2",
    ("divu", "remu"):     "arith2",
    ("divw", "remw"):     "arith2",
    ("divuw", "remuw"):   "arith2",
    # load + addi — 32/64-bit only; zero load offset; addi imm = width-scaled stride
    ("ld",  "addi"):      "load_addi",
    ("lw",  "addi"):      "load_addi",
    ("lwu", "addi"):      "load_addi",
    # store + addi — 32/64-bit only; zero store offset; addi imm = width-scaled stride
    ("sd",  "addi"):      "store_addi",
    ("sw",  "addi"):      "store_addi",
    # load + shNadd — zero load offset
    ("ld",  "sh3add"):    "load_shadd",
    ("lw",  "sh2add"):    "load_shadd",
    ("lwu", "sh2add"):    "load_shadd",
    # store + shNadd — zero store offset
    ("sd",  "sh3add"):    "store_shadd",
    ("sw",  "sh2add"):    "store_shadd",
    # consecutive same-width loads — same base, offsets differ by exactly data width
    ("ld",  "ld"):        "mem_pair",
    ("lw",  "lw"):        "mem_pair",
    ("lwu", "lwu"):       "mem_pair",
    # consecutive same-width stores — same base, offsets differ by exactly data width
    ("sd",  "sd"):        "mem_pair",
    ("sw",  "sw"):        "mem_pair",
    # independent single-output pairs — no shared operands required
    # ("addi", "addi") is overloaded: it covers three pseudo-ops (li, mv,
    # addi4spn) giving 6 order-insensitive combinations: li+li, mv+mv,
    # addi4spn+addi4spn, li+mv, li+addi4spn, mv+addi4spn.
    ("addi", "addi"):     "indep_pair",
}

_DUAL_MN = frozenset(m for pair in _DUAL_TUPLES for m in pair)


def _width_stride_ok(mem: Instruction, stride_insn: Instruction) -> bool:
    """stride_insn.imm is a nonzero uimm5-with-remap scaled by mem's data width."""
    shift = mem.access_shift if mem.access_shift is not None else 0
    return stride_insn.uimm_fits(5, shift, nonzero='remap')


def _is_li_mv_addi4spn(insn: Instruction) -> bool:
    """True for the three addi pseudo-ops that qualify for indep_pair."""
    return insn.is_li or insn.is_mv or insn.is_addi4spn


def _dual_shared_ok(kind: str, first: Instruction, second: Instruction) -> Optional[str]:
    """Operand-sharing and immediate checks by canonical role (order-independent)."""
    if kind == "arith2":
        if None in (first.rs1, first.rs2, second.rs1, second.rs2):
            return "dual-op-pair: missing register operand"
        if first.rs1 != second.rs1 or first.rs2 != second.rs2:
            return "dual-op-pair: source operands differ"
        return None
    if kind == "load_addi":
        if first.rs1 != second.rs1:
            return "dual-op-pair: base register differs from addi source"
        if first.imm not in (0, None):
            return "dual-op-pair: load offset must be zero"
        if not _width_stride_ok(first, second):
            return (f"dual-op-pair: addi immediate not a nonzero "
                    f"{first.access_width}-scaled uimm5")
        return None
    if kind == "store_addi":
        if first.rs1 != second.rs1:
            return "dual-op-pair: base register differs from addi source"
        if first.imm not in (0, None):
            return "dual-op-pair: store offset must be zero"
        if not _width_stride_ok(first, second):
            return (f"dual-op-pair: addi immediate not a nonzero "
                    f"{first.access_width}-scaled uimm5")
        return None
    if kind == "load_shadd":
        if first.rs1 != second.rs1:
            return "dual-op-pair: load base differs from shadd source"
        if first.imm not in (0, None):
            return "dual-op-pair: load offset must be zero"
        return None
    if kind == "store_shadd":
        if None in (first.rs1, first.rs2, second.rs1, second.rs2):
            return "dual-op-pair: missing register operand"
        if {first.rs1, first.rs2} != {second.rs1, second.rs2}:
            return "dual-op-pair: store regs differ from shadd sources"
        if first.imm not in (0, None):
            return "dual-op-pair: store offset must be zero"
        return None
    if kind == "mem_pair":
        if not first.is_local or not second.is_local:
            return "dual-op-pair: not an sp-relative 32/64-bit load/store (is_local)"
        if first.imm is None or second.imm is None:
            return "dual-op-pair: missing memory offset"
        width = first.access_width or (1 << (first.access_shift or 0))
        if abs(first.imm - second.imm) != width:
            return f"dual-op-pair: offsets must differ by exactly {width}"
        shift = first.access_shift or 0
        for insn in (first, second):
            if not insn.uimm_fits(5, shift):
                return f"dual-op-pair: offset {insn.imm} exceeds 5-bit range (max {31 << shift})"
        return None
    if kind == "indep_pair":
        # Restricted to: li (is_li), mv (is_mv), addi4spn (is_addi4spn).
        # addi4spn also requires the immediate fits the 5-bit encoding [4,128].
        for insn in (first, second):
            if not _is_li_mv_addi4spn(insn):
                return (f"dual-op-pair: not a li/mv/addi4spn pattern "
                        f"(x{insn.rd} = x{insn.rs1} + {insn.imm})")
            if insn.is_addi4spn and not insn.uimm_fits(5, 2, nonzero='remap'):
                return f"dual-op-pair: addi4spn immediate {insn.imm} out of range [4,128]"
        # Check both directions of independence (reversed_order never set for
        # symmetric tuples, so the outer function only checks A→B).
        if second.rd is not None and second.rd in first.uses_regs:
            return f"dual-op-pair: B result (x{second.rd}) feeds A"
        return None
    return "dual-op-pair: unknown match kind"


def _dual_op_pair(a: Instruction, b: Instruction) -> Optional[str]:
    """Two ops from a canonical tuple sharing inputs and producing distinct outputs."""
    if (a.mnemonic, b.mnemonic) in _DUAL_TUPLES:
        kind = _DUAL_TUPLES[(a.mnemonic, b.mnemonic)]
        first, second, reversed_order = a, b, False
    elif (b.mnemonic, a.mnemonic) in _DUAL_TUPLES:
        kind = _DUAL_TUPLES[(b.mnemonic, a.mnemonic)]
        first, second, reversed_order = b, a, True
    else:
        return f"dual-op-pair: ({a.mnemonic}, {b.mnemonic}) not a recognised tuple"

    reason = _dual_shared_ok(kind, first, second)
    if reason:
        return reason

    # Distinct outputs (where both instructions produce one).
    if a.rd is not None and a.rd == b.rd:
        return "dual-op-pair: same destination register"

    # The A-slot op must not feed the B-slot op — these are independent
    # operations, not a producer/consumer chain.  This also forbids the A-slot
    # op from clobbering a shared source before B reads it.
    if a.rd is not None and a.rd in b.uses_regs:
        return f"dual-op-pair: A result (x{a.rd}) feeds B"

    # Reverse (non-canonical) order is only legal when fully independent.
    # Canonical order may let the B-slot op write a shared source (a WAR that
    # resolves correctly because B executes second).
    if reversed_order and b.rd is not None and b.rd in a.uses_regs:
        return f"dual-op-pair: B result (x{b.rd}) conflicts with sources; reorder not allowed"

    return None



# ---------------------------------------------------------------------------
# pre-inc-pair
# ---------------------------------------------------------------------------
# A is in RSD form: rd == rs1 (or commutative: rd == rs2).  A writes its
# result back to its own source register — a pre-increment or accumulate.
# B reads A's rd as its rs1 — the updated pointer (for loads/stores) or the
# left-hand side of a comparison.  For memory B, the offset must be zero.
#
# Canonical order only: B depends on A's result, so the pair cannot be
# reversed.  A's rd must not be destroyed by B (B.rd != A.rd) since
# the updated pointer or value typically survives the pair.

_PRE_INC_TUPLES: frozenset = frozenset({
    ("addi",   "ld"),   # pre-increment pointer (8-byte stride) then load qword
    ("addi",   "sd"),   # pre-increment pointer then store qword
    ("sh2add", "lw"),   # scaled-index update then load word
    ("sh2add", "sw"),   # scaled-index update then store word
    ("add",    "slt"),  # accumulate then compare
})

_PRE_INC_A_MN = frozenset(a for a, _ in _PRE_INC_TUPLES)
_PRE_INC_B_MN = frozenset(b for _, b in _PRE_INC_TUPLES)


def _pre_inc_pair(a: Instruction, b: Instruction) -> Optional[str]:
    """A (RSD form) updates a register; B reads that register as rs1."""
    if (a.mnemonic, b.mnemonic) not in _PRE_INC_TUPLES:
        return f"pre-inc-pair: ({a.mnemonic}, {b.mnemonic}) not a recognised tuple"
    if not a.is_rsd:
        return "pre-inc-pair: A not in RSD form (rd not a source register)"
    if a.rd is None:
        return "pre-inc-pair: A has no destination"
    if b.rs1 != a.rd:
        return f"pre-inc-pair: B rs1 (x{b.rs1}) does not match A result (x{a.rd})"
    if b.has_mem_operand and b.imm not in (0, None):
        return "pre-inc-pair: B memory offset must be zero"
    if b.rd is not None and b.rd == a.rd:
        return "pre-inc-pair: A and B write same register"
    return None

RULES: list[PairingRule] = [
    PairingRule(
        name="rsd-alu-pair",
        a_mnemonic_set=_RSD_ALU_MN,
        b_mnemonic_set=_RSD_ALU_MN,
        a_prerequisites=["is_rsd"],
        b_prerequisites=["is_rsd"],
        check=_rsd_alu_pair,
        diagnose_a=_rsd_alu_diagnose,
        diagnose_b=_rsd_alu_diagnose,
    ),
    PairingRule(
        name="chain-alu-pair",
        a_mnemonic_set=_RSD_ALU_MN,
        b_mnemonic_set=_RSD_ALU_MN,
        check=_chain_alu_pair,
        diagnose_a=_chain_alu_diagnose,
        diagnose_b=_chain_alu_diagnose,
    ),
    PairingRule(
        name="dual-op-pair",
        a_mnemonic_set=_DUAL_MN,
        b_mnemonic_set=_DUAL_MN,
        check=_dual_op_pair,
    ),
    PairingRule(
        name="pre-inc-pair",
        a_mnemonic_set=_PRE_INC_A_MN,
        b_mnemonic_set=_PRE_INC_B_MN,
        a_prerequisites=["is_rsd"],
        check=_pre_inc_pair,
    ),
]

A_SLOT_DISQUALIFIERS: list[str] = [
    "is_unknown",
]

B_SLOT_DISQUALIFIERS: list[str] = [
    "is_unknown",
]

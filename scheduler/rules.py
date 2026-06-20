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

def _alu_diagnose_regs_imm(rule_name: str, insn: Instruction,
                           exclude: Optional[int] = None) -> Optional[str]:
    """Check register range and immediate constraints only; mnemonic already ok.

    exclude: a register number that is exempt from the range check (the chain
    register, which is not encoded in the packet and may be anywhere).
    """
    for reg, fname in ((insn.rd, "rd"), (insn.rs1, "rs1")):
        if reg is not None and reg != exclude and reg not in _RSD_ALU_REGS:
            return f"{rule_name}: {fname} (x{reg}) not in x0..x15"
    if insn.rs2 is not None and insn.rs2 != exclude and insn.rs2 not in _RSD_ALU_REGS:
        return f"{rule_name}: rs2 (x{insn.rs2}) not in x0..x15"
    if insn.mnemonic in _RSD_IMM_MN:
        imm = insn.imm
        # imm==0 on addi encodes as add rd, rs1, x0 — allow it through.
        if imm is not None and imm != 0 and not (_RSD_IMM_LO <= imm <= _RSD_IMM_HI):
            return f"{rule_name}: immediate out of range (got {imm}, need {_RSD_IMM_LO}..{_RSD_IMM_HI})"
        if imm is None:
            return f"{rule_name}: missing immediate"
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
    # a.rd is the chain register — not encoded in the packet; exempt from range.
    r = _alu_diagnose_regs_imm("chain-alu-pair", a, exclude=a.rd)
    if r:
        return r
    r = _alu_diagnose_regs_imm("chain-alu-pair", b, exclude=a.rd)
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
# load-chain-alu-pair / store-chain-alu-pair
# ---------------------------------------------------------------------------
# Two variants of chain-alu-pair where one slot is an sp-relative memory access
# carrying an 8-bit scaled offset.  The ALU slot draws from the same table
# (_RSD_ALU_MN) and uses the same register/immediate checks as chain-alu-pair,
# so all three rules evolve together as the allowed-op set is tuned.
#
#   load-chain:  A = sp-relative load (8-bit scaled offset); B = ALU op that
#                consumes the loaded value as its input.  The value is dead
#                after B.
#   store-chain: A = ALU op; B = sp-relative store (8-bit scaled offset) that
#                writes A's result to the stack.  The result is dead after B.

_SP_LOAD_MN  = frozenset({"lw", "lwu", "ld"})
_SP_STORE_MN = frozenset({"sw", "sd"})
_ALL_LOAD_MN = frozenset({"lb", "lbu", "lh", "lhu", "lw", "lwu", "ld"})
_ZERO_BRANCH_MN = frozenset({"beqz", "bnez"})


def _sp_mem_diagnose(rule_name: str, insn: Instruction) -> Optional[str]:
    """sp-relative memory op with a nonnegative 8-bit scaled offset."""
    if insn.rs1 != 2:
        return f"{rule_name}: memory base is not sp (x2)"
    shift = insn.access_shift or 0
    if not insn.uimm_fits(8, shift):
        max_off = ((1 << 8) - 1) << shift
        return f"{rule_name}: offset {insn.imm} exceeds 8-bit scaled range (max {max_off})"
    return None


def _load_chain_diagnose_a(insn: Instruction) -> Optional[str]:
    return _sp_mem_diagnose("load-chain-alu-pair", insn)


def _load_chain_diagnose_b(insn: Instruction) -> Optional[str]:
    return _alu_diagnose_regs_imm("load-chain-alu-pair", insn)


def _load_chain_alu_pair(a: Instruction, b: Instruction) -> Optional[str]:
    """A loads from the stack; B (ALU) consumes the loaded value, which is then dead."""
    r = _load_chain_diagnose_a(a)
    if r:
        return r
    # a.rd is the loaded chain value — not encoded; exempt from range check.
    r = _alu_diagnose_regs_imm("load-chain-alu-pair", b, exclude=a.rd)
    if r:
        return r
    if a.rd is None:
        return "load-chain-alu-pair: load has no destination register"
    uses_chain = (b.rs1 == a.rd) or (b.is_commutative and b.rs2 == a.rd)
    if not uses_chain:
        return f"load-chain-alu-pair: B does not consume loaded value (x{a.rd})"
    if b.rd != a.rd and a.rd in b.live_out:
        return f"load-chain-alu-pair: loaded value (x{a.rd}) escapes after B"
    return None


def _store_chain_diagnose_a(insn: Instruction) -> Optional[str]:
    return _alu_diagnose_regs_imm("store-chain-alu-pair", insn)


def _store_chain_diagnose_b(insn: Instruction) -> Optional[str]:
    return _sp_mem_diagnose("store-chain-alu-pair", insn)


def _store_chain_alu_pair(a: Instruction, b: Instruction) -> Optional[str]:
    """A (ALU) computes a value; B stores it to the stack, after which it is dead."""
    # a.rd is the chain result — not encoded in the packet; exempt from range check.
    r = _alu_diagnose_regs_imm("store-chain-alu-pair", a, exclude=a.rd)
    if r:
        return r
    r = _store_chain_diagnose_b(b)
    if r:
        return r
    if a.rd is None:
        return "store-chain-alu-pair: A has no destination register"
    if b.rs2 != a.rd:
        return f"store-chain-alu-pair: store value (x{b.rs2}) is not A's result (x{a.rd})"
    if a.rd in b.live_out:
        return f"store-chain-alu-pair: stored value (x{a.rd}) escapes after B"
    return None


# ---------------------------------------------------------------------------
# load-sp-branch / load-base-branch
# ---------------------------------------------------------------------------
# Load a value; branch on whether it is zero/nonzero; value kept alive.
# The two variants differ in base register and offset range:
#
#   load-sp-branch:   A = any load with sp (x2) as base, 10-bit unsigned
#                     byte offset.  Captures deep stack frames.
#   load-base-branch: A = any load with any base register, 5-bit unsigned
#                     byte offset.  Covers shallow struct fields.
#
# rd is NOT required to be dead — the common case is a null-check where the
# pointer is tested and then used on the non-null path.  Dead-after cases
# are also matched as a subset.

def _load_branch_check(a: Instruction, b: Instruction,
                       rule: str, imm_bits: int) -> Optional[str]:
    if a.rs1 is None:
        return f"{rule}: load has no base register"
    if a.rd is None:
        return f"{rule}: load has no destination"
    if a.base_from_auipc:
        return f"{rule}: load base is auipc-derived (GOT access)"
    if not a.uimm_fits(imm_bits):
        return f"{rule}: offset {a.imm} exceeds {imm_bits}-bit unsigned range"
    if b.rs1 != a.rd:
        return f"{rule}: branch tests x{b.rs1} but load produces x{a.rd}"
    return None


def _load_sp_branch(a: Instruction, b: Instruction) -> Optional[str]:
    """sp-relative load (uimm10 byte offset) -> beqz/bnez; rd kept alive."""
    if a.rs1 != 2:
        return "load-sp-branch: base is not sp (x2)"
    return _load_branch_check(a, b, "load-sp-branch", 10)


def _load_base_branch(a: Instruction, b: Instruction) -> Optional[str]:
    """Any-base load (uimm5 byte offset) -> beqz/bnez; rd kept alive."""
    return _load_branch_check(a, b, "load-base-branch", 5)


# ---------------------------------------------------------------------------
# deref-chain-load-pair / base-chain-load-pair
# ---------------------------------------------------------------------------
# Two load+load chains that pack into a single word as
# {opcode-tuple, rtmp, rd, rb, imm10}, where imm10 is a 10-bit width-scaled
# unsigned offset and the other load's offset is implied zero.
#
#   deref-chain: A = load rtmp, imm10(rb);  B = load rd, 0(rtmp)
#                — pointer chase: A's loaded value is B's base address.
#   base-chain:  A = load rtmp, 0(rb);       B = load rd, imm10(rtmp)
#                — pointer chase with the offset on the second load; A's loaded
#                value is B's base address.
#
# In both, B dereferences A's loaded value (rtmp), which is dead after B.  They
# differ only in which load carries the imm10 offset.

_CHAIN_LOAD_MN = frozenset({"lb", "lbu", "lh", "lhu", "lw", "lwu", "ld"})


def _deref_chain_load_pair(a: Instruction, b: Instruction) -> Optional[str]:
    """A loads a pointer at imm10(rb); B dereferences it at 0(rtmp); rtmp then dead."""
    if a.rs1 is None or a.rd is None:
        return "deref-chain-load-pair: A missing base/dest register"
    if a.base_from_auipc:
        return "deref-chain-load-pair: A is an auipc+load GOT access (reloc offset)"
    shift = a.access_shift or 0
    if not a.uimm_fits(10, shift):
        max_off = ((1 << 10) - 1) << shift
        return f"deref-chain-load-pair: A offset {a.imm} exceeds 10-bit scaled range (max {max_off})"
    if b.rs1 != a.rd:
        return f"deref-chain-load-pair: B base (x{b.rs1}) is not A's result (x{a.rd})"
    if b.imm not in (0, None):
        return "deref-chain-load-pair: B offset must be zero"
    if b.rd != a.rd and a.rd in b.live_out:
        return f"deref-chain-load-pair: pointer (x{a.rd}) escapes after B"
    return None


def _base_chain_load_pair(a: Instruction, b: Instruction) -> Optional[str]:
    """A loads a pointer at 0(rb); B dereferences it at imm10(rtmp); rtmp then dead."""
    if a.rs1 is None or a.rd is None:
        return "base-chain-load-pair: A missing base/dest register"
    if a.base_from_auipc:
        return "base-chain-load-pair: A is an auipc+load GOT access (reloc offset)"
    if a.imm not in (0, None):
        return "base-chain-load-pair: A offset must be zero"
    if b.rs1 != a.rd:
        return f"base-chain-load-pair: B base (x{b.rs1}) is not A's result (x{a.rd})"
    shift = b.access_shift or 0
    if not b.uimm_fits(10, shift):
        max_off = ((1 << 10) - 1) << shift
        return f"base-chain-load-pair: B offset {b.imm} exceeds 10-bit scaled range (max {max_off})"
    if b.rd != a.rd and a.rd in b.live_out:
        return f"base-chain-load-pair: pointer (x{a.rd}) escapes after B"
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

_MEM_PAIR_MN = frozenset({"lb", "lbu", "lh", "lhu", "lw", "lwu", "ld",
                          "sb", "sh", "sw", "sd"})


def _mem_pair(a: Instruction, b: Instruction) -> Optional[str]:
    """Adjacent same-width same-base loads or stores; offsets differ by one data width."""
    if a.mnemonic != b.mnemonic:
        return f"mem-pair: mnemonic mismatch ({a.mnemonic} vs {b.mnemonic})"
    if a.rs1 is None or b.rs1 is None or a.rs1 != b.rs1:
        return f"mem-pair: base registers differ"
    if a.imm is None or b.imm is None:
        return "mem-pair: missing memory offset"
    width = a.access_width or (1 << (a.access_shift or 0))
    if abs(a.imm - b.imm) != width:
        return f"mem-pair: offsets must differ by exactly {width}"
    shift = a.access_shift or 0
    imm_bits = 8 if a.is_local else 5
    for insn in (a, b):
        if not insn.uimm_fits(imm_bits, shift):
            max_off = ((1 << imm_bits) - 1) << shift
            return f"mem-pair: offset {insn.imm} exceeds {imm_bits}-bit scaled range (max {max_off})"
    if a.writes_memory:
        return None  # stores: no further constraint
    # loads: destinations must differ
    if a.rd is not None and a.rd == b.rd:
        return "mem-pair: same destination register"
    return None


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
    # (adjacent load/store pairs are handled by the dedicated mem-pair rule)
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
        if first.rs1 is None or second.rs1 is None:
            return "dual-op-pair: missing base register"
        if first.rs1 != second.rs1:
            return "dual-op-pair: base registers differ"
        if first.imm is None or second.imm is None:
            return "dual-op-pair: missing memory offset"
        width = first.access_width or (1 << (first.access_shift or 0))
        if abs(first.imm - second.imm) != width:
            return f"dual-op-pair: offsets must differ by exactly {width}"
        shift = first.access_shift or 0
        # sp-relative pairs get an 8-bit scaled offset (255 * data_width);
        # general-base pairs use a 5-bit scaled offset (31 * data_width).
        imm_bits = 8 if first.is_local else 5
        max_off = ((1 << imm_bits) - 1) << shift
        for insn in (first, second):
            if not insn.uimm_fits(imm_bits, shift):
                return f"dual-op-pair: offset {insn.imm} exceeds {imm_bits}-bit scaled range (max {max_off})"
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
# li-branch-pair
# ---------------------------------------------------------------------------
# A loads a small constant into a temporary; B is any conditional comparison
# branch that uses that temporary as one of its two operands (either slot),
# after which the temporary is dead.
#
# A slot: li rtmp, imm8  (addi rtmp, x0, imm  — 8-bit signed immediate)
# B slot: beq/bne/blt/bge/bltu/bgeu  rs, rtmp, off
#      or beq/bne/blt/bge/bltu/bgeu  rtmp, rs, off
#
# rtmp is dead after B — it carried only the comparison constant.
# rs (the non-constant operand) may be any register and survives.

_LI_BRANCH_A_MN = frozenset({"addi"})
_LI_BRANCH_B_MN = frozenset({"beq", "bne", "blt", "bge", "bltu", "bgeu"})


def _li_branch_pair(a: Instruction, b: Instruction) -> Optional[str]:
    """A loads an 8-bit constant; B compares it against a register and branches."""
    if not a.is_li:
        return "li-branch-pair: A not li form (must be addi rd, x0, imm)"
    if not a.imm_fits(8):
        return f"li-branch-pair: immediate {a.imm} out of 8-bit signed range [-128..127]"
    if b.rs1 != a.rd and b.rs2 != a.rd:
        return f"li-branch-pair: B does not use A's result (x{a.rd})"
    if a.rd in b.live_out:
        return f"li-branch-pair: A's result (x{a.rd}) escapes after B"
    return None


# ---------------------------------------------------------------------------
# bit-branch-pair
# ---------------------------------------------------------------------------
# A isolates a single bit (mask or shift); B branches on whether it is zero.
# A's result register is dead after B — it carries only the bit to the branch.
#
# A slot: andi rd, rs, pow2_imm   (any rd, including rd==rs RSD form)
#      or slli/srli/srai rd, rs, N
# B slot: beqz rd  or  bnez rd  (rs1 == A's rd; rd dead after B)
#
# The RSD case (rd==rs in A) is intentionally included — if the source is
# also dead it is valid, and the encoding can decide whether to compress it.

_BIT_BRANCH_A_MN = frozenset({"andi", "slli", "srli"})
_BIT_BRANCH_B_MN = frozenset({"beqz", "bnez", "beq", "bne"})


def _is_pow2_imm(v) -> bool:
    return v is not None and v > 0 and (v & (v - 1)) == 0


def _shift_for_zero_test(imm) -> Optional[tuple]:
    """If `andi rd, rs, imm; beqz/bnez` can be rewritten as `slli/srli rd, rs, N;
    beqz/bnez` (same branch type), return (shift_op, N).  Covers:
      2^N - 1  (low N bits all zero?)  → slli (64-N); beqz/bnez
      ~(2^N-1) (high bits all zero?)   → srli N;      beqz/bnez
    Does not cover plain pow2 (single bit) — those encode as andi directly."""
    if imm is None:
        return None
    if imm > 1 and (imm & (imm + 1)) == 0:          # 2^N - 1
        return ("slli", 64 - imm.bit_length())
    if imm < -1 and (-imm & (-imm - 1)) == 0:        # ~(2^N - 1) = -(2^N)
        return ("srli", (-imm).bit_length() - 1)
    return None


def _bit_branch_pair(a: Instruction, b: Instruction) -> Optional[str]:
    """A isolates or masks bits; B branches on zero/nonzero; A's result is dead after B.

    andi with a pow2 immediate isolates a single bit and encodes directly.
    andi with a 2^N-1 or ~(2^N-1) immediate will be rewritten to slli/srli at
    emit time — accepted here because the zero/nonzero test is equivalent.
    slli/srli are accepted directly for any shift amount.
    All forms require a zero-test branch (beqz/bnez or beq/bne with rs2==x0).
    """
    if a.rd is None:
        return "bit-branch-pair: A has no destination"
    # beq/bne with zero are aliases for beqz/bnez; non-zero comparisons not supported
    if b.mnemonic in ("beq", "bne") and b.rs2 != 0:
        return "bit-branch-pair: beq/bne B slot requires rs2==zero"
    if a.mnemonic == "andi":
        if not _is_pow2_imm(a.imm) and _shift_for_zero_test(a.imm) is None:
            return f"bit-branch-pair: andi immediate {a.imm} not pow2 or shift-expressible"
    # slli/srli: any shift amount is accepted
    if b.rs1 != a.rd:
        return f"bit-branch-pair: B tests x{b.rs1} but A's result is x{a.rd}"
    if b.rd != a.rd and a.rd in b.live_out:
        return f"bit-branch-pair: A's result (x{a.rd}) escapes after B"
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


_EPILOGUE_A_MN = frozenset({"addi"})
_EPILOGUE_B_MN = frozenset({"jalr", "ret"})


def _epilogue_pair(a: Instruction, b: Instruction) -> Optional[str]:
    """A restores sp; B is an unconditional return or jump.

    A: addi sp, sp, +N  — 7-bit uimm×16, nonzero (max 2032)
    B: ret or jalr rd∈{0,1} with 12-bit signed offset
    """
    # Canonicalise: addi must be A regardless of call order
    if a.mnemonic in _EPILOGUE_B_MN:
        a, b = b, a
    if a.mnemonic not in _EPILOGUE_A_MN or b.mnemonic not in _EPILOGUE_B_MN:
        return f"epilogue-pair: ({a.mnemonic}, {b.mnemonic}) not a recognised tuple"
    if a.rd != 2 or a.rs1 != 2:
        return "epilogue-pair: A not addi sp, sp"
    if not a.uimm_fits(7, 4, nonzero=True):
        return (f"epilogue-pair: sp adjustment {a.imm} not a nonzero "
                f"7-bit uimm×16 (max {127*16})")
    if b.rd not in (0, 1):
        return f"epilogue-pair: B rd (x{b.rd}) must be x0 or x1"
    if not b.imm_fits(12):
        return f"epilogue-pair: jalr offset {b.imm} out of 12-bit range"
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
        name="load-chain-alu-pair",
        a_mnemonic_set=_SP_LOAD_MN,
        b_mnemonic_set=_RSD_ALU_MN,
        check=_load_chain_alu_pair,
        diagnose_a=_load_chain_diagnose_a,
        diagnose_b=_load_chain_diagnose_b,
    ),
    PairingRule(
        name="store-chain-alu-pair",
        a_mnemonic_set=_RSD_ALU_MN,
        b_mnemonic_set=_SP_STORE_MN,
        check=_store_chain_alu_pair,
        diagnose_a=_store_chain_diagnose_a,
        diagnose_b=_store_chain_diagnose_b,
    ),
    PairingRule(
        name="load-sp-branch",
        a_mnemonic_set=_ALL_LOAD_MN,
        b_mnemonic_set=_ZERO_BRANCH_MN,
        a_prerequisites=["reads_stack"],
        check=_load_sp_branch,
    ),
    PairingRule(
        name="load-base-branch",
        a_mnemonic_set=_ALL_LOAD_MN,
        b_mnemonic_set=_ZERO_BRANCH_MN,
        check=_load_base_branch,
    ),
    PairingRule(
        name="deref-chain-load-pair",
        a_mnemonic_set=_CHAIN_LOAD_MN,
        b_mnemonic_set=_CHAIN_LOAD_MN,
        check=_deref_chain_load_pair,
    ),
    PairingRule(
        name="base-chain-load-pair",
        a_mnemonic_set=_CHAIN_LOAD_MN,
        b_mnemonic_set=_CHAIN_LOAD_MN,
        check=_base_chain_load_pair,
    ),
    PairingRule(
        name="mem-pair",
        a_mnemonic_set=_MEM_PAIR_MN,
        b_mnemonic_set=_MEM_PAIR_MN,
        check=_mem_pair,
    ),
    PairingRule(
        name="dual-op-pair",
        a_mnemonic_set=_DUAL_MN,
        b_mnemonic_set=_DUAL_MN,
        check=_dual_op_pair,
    ),
    PairingRule(
        name="li-branch-pair",
        a_mnemonic_set=_LI_BRANCH_A_MN,
        b_mnemonic_set=_LI_BRANCH_B_MN,
        a_prerequisites=["is_li"],
        check=_li_branch_pair,
    ),
    PairingRule(
        name="bit-branch-pair",
        a_mnemonic_set=_BIT_BRANCH_A_MN,
        b_mnemonic_set=_BIT_BRANCH_B_MN,
        check=_bit_branch_pair,
    ),
    PairingRule(
        name="pre-inc-pair",
        a_mnemonic_set=_PRE_INC_A_MN,
        b_mnemonic_set=_PRE_INC_B_MN,
        a_prerequisites=["is_rsd"],
        check=_pre_inc_pair,
    ),
    PairingRule(
        name="epilogue-pair",
        a_mnemonic_set=_EPILOGUE_A_MN | _EPILOGUE_B_MN,
        b_mnemonic_set=_EPILOGUE_A_MN | _EPILOGUE_B_MN,
        check=_epilogue_pair,
    ),
]

A_SLOT_DISQUALIFIERS: list[str] = [
    "is_unknown",
]

B_SLOT_DISQUALIFIERS: list[str] = [
    "is_unknown",
]

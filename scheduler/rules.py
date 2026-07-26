"""
scheduler/rules.py — Pairing policy: rule definitions and slot disqualifiers.

This is the only file that needs to change when iterating on pairing policy.
The mechanism (can_pair, greedy_pair, stamp_slot_eligibility) lives in pairing.py.
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Callable, Optional, Final
from collections.abc import Iterable
from functools import wraps

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


# ---------------------------------------------------------------------------
# pairing failure exception
# ---------------------------------------------------------------------------

class NotPair(Exception):
    """Raised by a pairing rule when an encoding rejects a candidate pair."""

    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(self.reason)


# ---------------------------------------------------------------------------
# Shared constants
# ---------------------------------------------------------------------------

_RSD_ALU_MN = frozenset({
    "addi", "addiw", "andi",                 # immediate forms
    "add",  "addw",
    "sub",  "subw",
    "and",  "andn",
    "or",   "xor",
    "slli", "srli", "srai",                  # shift-immediate forms
    "mul",  "mulhu",                          # multiply
    })
_RSD_ALU_REGS = frozenset(range(16))         # x0..x15 (4-bit register field)
_RSD_IMM_MN   = frozenset({"addi", "addiw", "andi"})  # signed nzimm -64..64
_RSD_SHIFT_MN = frozenset({"slli", "srli", "srai"})   # shift amount 1..32
_RSD_IMM_LO, _RSD_IMM_HI = -64, 64
_RSD_SHIFT_LO, _RSD_SHIFT_HI = 1, 32


# ---------------------------------------------------------------------------
# Shared per-slot helpers (mnemonic already confirmed by rule.mnemonic_set)
# ---------------------------------------------------------------------------

def _imm_in_range(insn: Instruction) -> None:
    """Immediate / shift-amount range check for an RSD ALU op (mnemonic already ok).

    Register-range checks are not done here: they belong to the uses_low_regs
    family of decorators, which every caller already applies.
    """
    if insn.mnemonic in _RSD_IMM_MN:
        imm = insn.imm
        # imm==0 on addi/addiw encodes as add/addw rd, rs1, x0 — allow it through.
        if imm is not None and imm != 0 and not (_RSD_IMM_LO <= imm <= _RSD_IMM_HI):
            raise NotPair("big-imm")
        if imm is None:
            raise NotPair("MALFORMED: missing-immediate")
    elif insn.mnemonic in _RSD_SHIFT_MN:
        imm = insn.imm
        if imm is None:
            raise NotPair("MALFORMED: missing-shift-amount")
        if not (_RSD_SHIFT_LO <= imm <= _RSD_SHIFT_HI):
            raise NotPair("big-imm")


# ---------------------------------------------------------------------------
# decorators
# ---------------------------------------------------------------------------

def no_escape(func: Callable):
    @wraps(func)
    def check_escape(a: Instruction, b: Instruction):
        if a.rd != b.rd and a.rd in b.live_out and a.rd:
            raise NotPair("A-result-escapes")
        return func(a, b)
    return check_escape


def must_chain(func: Callable):
    @wraps(func)
    def check_chain(a: Instruction, b: Instruction):
        if a.rd is None: raise NotPair("not-chain")
        if a.rd != b.rs1 and not (b.is_commutative and a.rd == b.rs2): raise NotPair("not-chain")
        return func(a, b)
    return check_chain


def must_chain_rs1(func: Callable):
    @wraps(func)
    def check_chain1(a: Instruction, b: Instruction):
        if a.rd is None: raise NotPair("not-chain")
        if a.rd != b.rs1: raise NotPair("not-chain")
        return func(a, b)
    return check_chain1


# loads/stores use rs1 as base register.
must_chain_base: Final[Callable] = must_chain_rs1


def must_chain_rs2(func: Callable):
    @wraps(func)
    def check_chain2(a: Instruction, b: Instruction):
        if a.rd is None: raise NotPair("not-chain")
        if a.rd != b.rs2: raise NotPair("not-chain")
        return func(a, b)
    return check_chain2


# stores use rs2 as source value
must_chain_stored: Final[Callable] = must_chain_rs2


def must_chain_either(func: Callable):
    @wraps(func)
    def check_chain3(a: Instruction, b: Instruction):
        if a.rd is None: raise NotPair("not-chain")
        if a.rd not in b.uses_regs: raise NotPair("not-chain")
        return func(a, b)
    return check_chain3


def must_not_chain(func: Callable):
    @wraps(func)
    def check_chain3(a: Instruction, b: Instruction):
        if a.rd and a.rd in b.uses_regs: raise NotPair("unwanted-chain")
        return func(a, b)
    return check_chain3


def a_base_not_from_auipc(func: Callable):
    @wraps(func)
    def check_a_base_from_auipc(a: Instruction, b: Instruction):
        if a.base_from_auipc:
            raise NotPair("A-relocatable-offset")
        return func(a, b)
    return check_a_base_from_auipc


def b_base_not_from_auipc(func: Callable):
    @wraps(func)
    def check_b_base_from_auipc(a: Instruction, b: Instruction):
        if b.base_from_auipc:
            raise NotPair("B-relocatable-offset")
        return func(a, b)
    return check_b_base_from_auipc


def a_is_rsd(func: Callable):
    @wraps(func)
    def check_a_is_rsd(a: Instruction, b: Instruction):
        if not a.is_rsd:
            raise NotPair("A-is-not-rsd")
        return func(a, b)
    return check_a_is_rsd


def a_is_rsd_or_li(func: Callable):
    @wraps(func)
    def check_a_is_rsd_or_li(a: Instruction, b: Instruction):
        if not a.is_rsd and not a.is_li:
            raise NotPair("A-is-not-rsd-or-li")
        return func(a, b)
    return check_a_is_rsd_or_li


def b_is_rsd(func: Callable):
    @wraps(func)
    def check_b_is_rsd(a: Instruction, b: Instruction):
        if not b.is_rsd:
            raise NotPair("B-is-not-rsd")
        return func(a, b)
    return check_b_is_rsd


def b_is_rsd_or_li(func: Callable):
    @wraps(func)
    def check_b_is_rsd_or_li(a: Instruction, b: Instruction):
        if not b.is_rsd and not b.is_li:
            raise NotPair("B-is-not-rsd-or-li")
        return func(a, b)
    return check_b_is_rsd_or_li


# is_rsd is structural ("rd is also a source"): it accepts rd==rs2 as well as
# rd==rs1.  But the compressed two-address encoding only realizes rd==rs2 by
# swapping operands, which is legal only for a commutative op.  These decorators
# refine the loose is_rsd gate down to what the encoding can actually express;
# li forms (is_rsd is False) pass through untouched.
def a_rsd_swappable(func: Callable):
    @wraps(func)
    def check_a_rsd_swappable(a: Instruction, b: Instruction):
        if a.is_rsd and a.rd != a.rs1 and not a.is_commutative:
            raise NotPair("A-rd==rs2-requires-commutative")
        return func(a, b)
    return check_a_rsd_swappable


def b_rsd_swappable(func: Callable):
    @wraps(func)
    def check_b_rsd_swappable(a: Instruction, b: Instruction):
        if b.is_rsd and b.rd != b.rs1 and not b.is_commutative:
            raise NotPair("B-rd==rs2-requires-commutative")
        return func(a, b)
    return check_b_rsd_swappable


def exclusive_rd(func: Callable):
    @wraps(func)
    def check_rd_exclusive(a: Instruction, b: Instruction):
        if a.rd and b.rd and a.rd == b.rd:  # zeroes and Nones aren't collisions
            raise NotPair("rd-collision")
        return func(a, b)
    return check_rd_exclusive


def _get_fields(insn: Iterable[Instruction], fields: Iterable[str], f: Callable):
    field_names = {
            "a.rd": (0, "rd"),
            "a.rs1": (0, "rs1"),
            "a.rs2": (0, "rs2"),
            "b.rd": (1, "rd"),
            "b.rs1": (1, "rs1"),
            "b.rs2": (1, "rs2"),
    }
    def per_field(name: str):
        n, field = field_names[name]
        return f(getattr(insn[n], field, None))
    return map(per_field, fields)


def _confirm_low_regs(a: Instruction, b: Instruction, fields: Iterable[str]):
    r_is_low = lambda r: r is None or r in _RSD_ALU_REGS
    if not all(_get_fields((a, b), fields, r_is_low)):
        raise NotPair("big-reg")


def uses_low_regs(func: Callable):
    all_regs = ("a.rd", "a.rs1", "a.rs2", "b.rd", "b.rs1", "b.rs2")
    @wraps(func)
    def check_low_regs(a: Instruction, b: Instruction):
        _confirm_low_regs(a, b, all_regs)
        return func(a, b)
    return check_low_regs


def chain_uses_low_regs(func: Callable):
    @wraps(func)
    def check_low_regs1(a: Instruction, b: Instruction):
        if b.is_commutative and b.rs2 == a.rd:
            _confirm_low_regs(a, b, ("a.rs1", "a.rs2", "b.rd", "b.rs1"))
        else:
            _confirm_low_regs(a, b, ("a.rs1", "a.rs2", "b.rd", "b.rs2"))
        return func(a, b)
    return check_low_regs1


def a_imm_ok(func: Callable):
    """A-slot's immediate / shift amount is in the RSD-encodable range."""
    @wraps(func)
    def check_a_imm_ok(a: Instruction, b: Instruction):
        _imm_in_range(a)
        return func(a, b)
    return check_a_imm_ok


def b_imm_ok(func: Callable):
    """B-slot's immediate / shift amount is in the RSD-encodable range."""
    @wraps(func)
    def check_b_imm_ok(a: Instruction, b: Instruction):
        _imm_in_range(b)
        return func(a, b)
    return check_b_imm_ok


def uses_low_regs_here(*these_regs: str):
    def uses_low_regs_dec(func: Callable):
        @wraps(func)
        def check_low_regs_here(a: Instruction, b: Instruction):
            _confirm_low_regs(a, b, these_regs)
            return func(a, b)
        return check_low_regs_here
    return uses_low_regs_dec


# ---------------------------------------------------------------------------
# rsd-alu-pair
# ---------------------------------------------------------------------------

@a_is_rsd_or_li
@b_is_rsd_or_li
@a_rsd_swappable
@b_rsd_swappable
@uses_low_regs
@a_imm_ok
@b_imm_ok
@exclusive_rd
def _rsd_alu_pair(a: Instruction, b: Instruction) -> None:
    """Both instructions RSD or li form, x0..x15, immediates in range, and the
    two slots write distinct destination registers.

    Distinct destinations: rsd-alu-pair exists to pack two independent, both-live
    ALU results.  If a.rd == b.rd then either B consumes A (a producer/consumer
    chain — handled, more capably, by chain-alu-pair, whose shared register need
    not be in x0..x15) or B does not (making A's write dead).  Either way this
    rule should not claim the pair; require distinct destinations.
    """


# ---------------------------------------------------------------------------
# chain-alu-pair
# ---------------------------------------------------------------------------

@chain_uses_low_regs
@must_chain
@no_escape
@a_imm_ok
@b_imm_ok
def _chain_alu_pair(a: Instruction, b: Instruction) -> None:
    """A computes a value that B immediately consumes; that value is dead after B.

    A has free choice of rd and rs1.  B must use A's rd as its rs1 input
    (or rs2 if B is commutative).  A's rd must be dead after B — either B
    overwrites it (b.rd == a.rd) or it is not live in b.live_out.

    Because a.rd is produced and consumed within the packet and dies there, it
    is not encoded and is exempt from the x0..x15 range limit — hence
    @chain_uses_low_regs (which skips a.rd and B's consuming source), matching
    load-chain / store-chain.
    """
    pass


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
ALL_BRANCH_MN = frozenset({"beq", "bne", "blt", "bge", "bltu", "bgeu", "beqz", "bnez"})


def _sp_mem_check(insn: Instruction) -> None:
    """sp-relative memory op with a nonnegative 8-bit scaled offset."""
    if insn.rs1 != 2:
        raise NotPair("not-SP-base")
    shift = insn.access_shift or 0
    if not insn.uimm_fits(8, shift):
        raise NotPair("big-imm")


def a_sp_mem(func: Callable):
    """A-slot is an sp-relative memory op with an in-range 8-bit scaled offset."""
    @wraps(func)
    def check_a_sp_mem(a: Instruction, b: Instruction):
        _sp_mem_check(a)
        return func(a, b)
    return check_a_sp_mem


def b_sp_mem(func: Callable):
    """B-slot is an sp-relative memory op with an in-range 8-bit scaled offset."""
    @wraps(func)
    def check_b_sp_mem(a: Instruction, b: Instruction):
        _sp_mem_check(b)
        return func(a, b)
    return check_b_sp_mem


# a.rd (the chain register) is dead after B and not encoded in the packet, so it
# is exempt from range checks.  @must_chain / @must_chain_stored already reject
# a.rd is None, so the ALU/mem checks below never see a destination-less A.
@chain_uses_low_regs
@must_chain
@no_escape
@a_sp_mem
@b_imm_ok
def _load_chain_alu_pair(a: Instruction, b: Instruction) -> None:
    """A loads from the stack; B (ALU) consumes the loaded value, which is then dead."""
    return None


@chain_uses_low_regs
@must_chain_stored
@no_escape
@a_imm_ok
@b_sp_mem
def _store_chain_alu_pair(a: Instruction, b: Instruction) -> Optional[str]:
    """A (ALU) computes a value; B stores it to the stack, after which it is dead."""
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
                       imm_bits: int) -> None:
    if a.rbase is None:
        raise NotPair("load has no base register")
    if a.rd is None:
        raise NotPair("load has no destination")
    if not a.uimm_fits(imm_bits):
        raise NotPair(f"offset exceeds {imm_bits}-bit unsigned range")
    return None


@a_base_not_from_auipc
@must_chain
def _load_sp_branch(a: Instruction, b: Instruction) -> None:
    """sp-relative load (uimm10 byte offset) -> beqz/bnez; rd kept alive."""
    if a.rbase != 2:
        raise NotPair("not-SP-base")
    _load_branch_check(a, b, 10)


@a_base_not_from_auipc
@must_chain
def _load_base_branch(a: Instruction, b: Instruction) -> None:
    """Any-base load (uimm5 byte offset) -> beqz/bnez; rd kept alive."""
    _load_branch_check(a, b, 5)


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


@must_chain_base
@no_escape
@a_base_not_from_auipc
def _deref_chain_load_pair(a: Instruction, b: Instruction) -> None:
    """A loads a pointer at imm10(rb); B dereferences it at 0(rtmp); rtmp then dead."""
    if a.rbase is None or a.rd is None:
        raise NotPair("A missing base/dest register")
    shift = a.access_shift or 0
    if not a.uimm_fits(10, shift):
        max_off = ((1 << 10) - 1) << shift
        raise NotPair(f"A offset exceeds 10-bit scaled range (max {max_off})")
    if b.imm != 0:
        raise NotPair("B offset must be zero")
    return None


@must_chain_base
@no_escape
@a_base_not_from_auipc
def _base_chain_load_pair(a: Instruction, b: Instruction) -> None:
    """A loads a pointer at 0(rb); B dereferences it at imm10(rtmp); rtmp then dead."""
    if a.rbase is None or a.rd is None:
        raise NotPair("A missing base/dest register")
    if a.imm != 0:
        raise NotPair("A offset must be zero")
    shift = b.access_shift or 0
    if not b.uimm_fits(10, shift):
        raise NotPair("big-imm")
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
#   "mem_addi"    load + addi; share the base register and the (width-scaled)
#                 immediate offset; two distinct dests.
#   "mem_shadd"   load/store + shNadd; share the base register only, with the
#                 load offset forced to zero — a register post-increment form.
#
# Width-scaled immediate (mem_addi): the memory op implies a data width; the
# shared/offset immediate must be a nonzero unsigned 5-bit value with remap,
# scaled by that width — i.e. a nonzero multiple of the width in
# [width, 32*width].  This trades granularity for reach.
#
# Canonical order is (tuple[0], tuple[1]).  The reverse order is accepted only
# when the two instructions are fully independent (neither destination is a
# source operand of the pair).  In canonical order the B-slot instruction may
# write one of the shared source registers (a WAR that resolves correctly
# because B executes second); the reverse order may not.

_MEM_PAIR_MN = frozenset({"lb", "lbu", "lh", "lhu", "lw", "lwu", "ld",
                          "sb", "sh", "sw", "sd"})


@exclusive_rd
def _mem_pair(a: Instruction, b: Instruction) -> None:
    """Adjacent same-width same-base loads or stores; offsets differ by one data width."""
    if a.mnemonic != b.mnemonic:
        raise NotPair("opcode-mismatch")
    if a.rbase != b.rbase or a.rbase is None:
        raise NotPair("base-reg-mismatch")
    if a.imm is None or b.imm is None:
        raise NotPair("MALFORMED: memory offset absent")
    width = a.access_width or (1 << (a.access_shift or 0))
    if abs(a.imm - b.imm) != width:
        raise NotPair(f"bad-delta")
    shift = a.access_shift or 0
    imm_bits = 8 if a.is_local else 5
    for insn in (a, b):
        if not insn.uimm_fits(imm_bits, shift):
            max_off = ((1 << imm_bits) - 1) << shift
            raise NotPair(f"offset exceeds {imm_bits}-bit scaled range (max {max_off})")
    return None


# ---------------------------------------------------------------------------
# arith-mem-pair
# ---------------------------------------------------------------------------
# Independent RSD arithmetic op (A) paired with a small-offset memory op (B).
# No producer-consumer relationship required — they share no operands.
# The dep graph prevents scheduling A before B when a true dependency exists.
#
# A slot: add/sub/and/or/addi  rd, rd, rs2_or_imm
#         rd and rs1 in x0..x15; addi imm in -64..64 inclusive, excluding 0
# B slot: any load or store with non-negative offset aligned to access width
#         and fitting a 2-bit scaled field (0, 1×w, 2×w, 3×w)

_ARITH_MEM_A_MN = frozenset({"add", "sub", "and", "or", "addi"})
_ARITH_MEM_B_MN = _MEM_PAIR_MN


def _arith_mem_small_offset_ok(insn: Instruction) -> bool:
    """B-slot: non-negative offset, aligned, fits 2-bit scaled field (0..3×width)."""
    # use imm as offset for loads/stores
    off = insn.imm
    if off is None or off < 0:
        return False
    width = insn.access_width
    if not width:
        return False
    return off % width == 0 and off <= 3 * width


@a_is_rsd
@must_not_chain
@uses_low_regs_here("a.rd", "a.rs1")  # deliberately relax a.rs2 (shared with imm5)
def _arith_mem_pair(a: Instruction, b: Instruction) -> None:
    """RSD arith (x0..x15, small imm) paired with small-offset mem op."""
    if a.mnemonic == "addi":
        # Immediate field is [-64, 64] inclusive, excluding 0 (encode a zero
        # immediate as a move from x0 instead).
        if a.imm is None or a.imm == 0 or not (-64 <= a.imm <= 64):
            raise NotPair("A-big-imm")
    if not _arith_mem_small_offset_ok(b):
        raise NotPair("B-big-imm")
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
    ("ld",  "addi"):      "mem_addi",
    ("lw",  "addi"):      "mem_addi",
    ("lwu", "addi"):      "mem_addi",
    # store + addi — 32/64-bit only; zero store offset; addi imm = width-scaled stride
    ("sd",  "addi"):      "mem_addi",
    ("sw",  "addi"):      "mem_addi",
    # load + shNadd — zero load offset
    ("ld",  "sh3add"):    "mem_shadd",
    ("lw",  "sh2add"):    "mem_shadd",
    ("lwu", "sh2add"):    "mem_shadd",
    # store + shNadd — zero store offset
    ("sd",  "sh3add"):    "mem_shadd",
    ("sw",  "sh2add"):    "mem_shadd",
    # (adjacent load/store pairs are handled by the dedicated mem-pair rule)
    # independent single-output pairs — no shared operands required
    # ("addi", "addi") is overloaded: it covers three pseudo-ops (li, mv,
    # addi4spn) giving 6 order-insensitive combinations: li+li, mv+mv,
    # addi4spn+addi4spn, li+mv, li+addi4spn, mv+addi4spn.
    ("addi", "addi"):     "indep_pair",
}

def _role_tuples(role: str) -> frozenset:
    """The (a.mnemonic, b.mnemonic) tuples belonging to one dual-op family."""
    return frozenset(k for k, v in _DUAL_TUPLES.items() if v == role)


def _role_mnems(role: str) -> frozenset:
    """The mnemonics appearing in a dual-op family (both slots, order-insensitive)."""
    return frozenset(m for k in _role_tuples(role) for m in k)


def _width_stride_ok(mem: Instruction, stride_insn: Instruction) -> bool:
    """stride_insn.imm is a nonzero uimm5-with-remap scaled by mem's data width."""
    shift = mem.access_shift if mem.access_shift is not None else 0
    return stride_insn.uimm_fits(5, shift, nonzero='remap')


def _is_li_mv_addi4spn(insn: Instruction) -> bool:
    """True for the three addi pseudo-ops that qualify for indep_pair."""
    return insn.is_li or insn.is_mv or insn.is_addi4spn

# The dual-op families below share one mechanism (distinct destinations, order-
# insensitive tuple match, mutual independence) but differ in how the two ops
# share operands.  Each family is its own PairingRule so its stats stand alone,
# rather than being hidden under a single "dual-op" tally.

def _canonical_dual(a: Instruction, b: Instruction, tuples: frozenset):
    """Order-insensitive tuple match: return (first, second, reversed_order) in
    canonical order, or raise if (a, b) is not one of `tuples`."""
    if (a.mnemonic, b.mnemonic) in tuples:
        return a, b, False
    if (b.mnemonic, a.mnemonic) in tuples:
        return b, a, True
    raise NotPair("bad-tuple")


def _reject_dependence(a: Instruction, b: Instruction, reversed_order: bool) -> None:
    """Every dual-op family packs two INDEPENDENT ops.  The A-slot op must not
    feed the B-slot op (a producer/consumer chain, or clobbering a shared source
    before B reads it).  A reversed (non-canonical) order is only legal when the
    B-slot op also does not feed A."""
    if a.rd is not None and a.rd in b.uses_regs:
        raise NotPair("unwanted-chain")
    if reversed_order and b.rd is not None and b.rd in a.uses_regs:
        raise NotPair("cannot-reorder")


def dual_family(role: str):
    """Turn a per-family operand-sharing check — written in canonical
    first/second terms — into a full rule check(a, b): distinct destinations
    (@exclusive_rd), order-insensitive tuple match, the family check, then mutual
    independence."""
    tuples = _role_tuples(role)
    def deco(shared_ok: Callable):
        @exclusive_rd
        @wraps(shared_ok)
        def check(a: Instruction, b: Instruction):
            first, second, reversed_order = _canonical_dual(a, b, tuples)
            shared_ok(first, second)
            _reject_dependence(a, b, reversed_order)
        return check
    return deco


@dual_family("arith2")
def _dual_arith2(a: Instruction, b: Instruction) -> None:
    """Two R-type ops sharing rs1 and rs2 positionally (sum/diff, min/max, ...)."""
    if None in (a.rs1, a.rs2, b.rs1, b.rs2):
        raise NotPair("MALFORMED: missing register operand")
    if a.rs1 != b.rs1 or a.rs2 != b.rs2:
        raise NotPair("source-operand-mismatch")


@dual_family("mem_addi")
def _dual_mem_addi(a: Instruction, b: Instruction) -> None:
    """Mem + addi pointer stride: base == addi source, zero mem offset, addi
    immediate a nonzero width-scaled uimm5."""
    if a.rs1 != b.rs1:
        raise NotPair("base-reg-mismatch")
    if a.imm != 0:
        raise NotPair("nonzero-offset")
    if not _width_stride_ok(a, b):
        raise NotPair("B-addi-imm-mismatch")


@dual_family("mem_shadd")
def _dual_mem_shadd(a: Instruction, b: Instruction) -> None:
    """Load + shNadd index: load base == shadd source, zero load offset."""
    if a.rs1 != b.rs1:
        raise NotPair("base-reg-mismatch")
    if a.imm != 0:
        raise NotPair("nonzero-offset")


@dual_family("indep_pair")
def _dual_indep(a: Instruction, b: Instruction) -> None:
    """Two fully independent small pseudo-ops (li / mv / addi4spn)."""
    for insn in (a, b):
        if not _is_li_mv_addi4spn(insn):
            raise NotPair("is-not-li_mv_addi4spn")
        if insn.is_addi4spn and not insn.uimm_fits(5, 2, nonzero='remap'):
            raise NotPair(f"addi4spn immediate {insn.imm} out of range [4,128]")
    # A→B independence is enforced by _reject_dependence; also require B↛A
    # (reversed_order is never set for this symmetric tuple).
    if b.rd is not None and b.rd in a.uses_regs:
        raise NotPair("B result feeds A")



# ---------------------------------------------------------------------------
# chain-li-branch
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


@must_chain
@no_escape
def _chain_li_branch(a: Instruction, b: Instruction) -> None:
    """A loads an 8-bit constant; B compares it against a register and branches."""
    if not a.is_li:
        raise NotPair("A not li form (must be addi rd, x0, imm)")
    if not a.imm_fits(8):
        raise NotPair("immediate out of 8-bit signed range [-128..127]")
    return None


# ---------------------------------------------------------------------------
# addi-branch-pair
# ---------------------------------------------------------------------------
# Loop-counter / pointer-stride pattern: addi/addiw updates a register in
# place (RSD form), then a comparison branch uses that register as one of its
# two operands, after which the register is dead.
#
# A slot: addi/addiw  rd, rd, imm8   (RSD form, rd in x0..x15, imm fits 8-bit)
# B slot: beq/bne/blt/bge/bltu/bgeu  rd, rs, label
#      or beq/bne/blt/bge/bltu/bgeu  rs, rd, label

_ADDI_BRANCH_A_MN = frozenset({"addi", "addiw"})
_ADDI_BRANCH_B_MN = frozenset({"beq", "bne", "blt", "bge", "bltu", "bgeu"})


@must_chain
def _addi_branch_pair(a: Instruction, b: Instruction) -> None:
    """addi/addiw RSD + comparison branch consuming the result."""
    if not a.is_rsd:
        raise NotPair("A-is-not-rsd")
    if a.rd not in _RSD_ALU_REGS:
        raise NotPair("A-big-reg")
    if not a.imm_fits(8):
        raise NotPair("B-big-imm")
    return None


# ---------------------------------------------------------------------------
# chain-bit-test-branch
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


@must_chain
@no_escape
def _chain_bit_test_branch(a: Instruction, b: Instruction) -> None:
    """A isolates or masks bits; B branches on zero/nonzero; A's result is dead after B.

    andi with a pow2 immediate isolates a single bit and encodes directly.
    andi with a 2^N-1 or ~(2^N-1) immediate will be rewritten to slli/srli at
    emit time — accepted here because the zero/nonzero test is equivalent.
    slli/srli are accepted directly for any shift amount.
    All forms require a zero-test branch (beqz/bnez or beq/bne with rs2==x0).
    """
    if a.rd is None:
        raise NotPair("A has no destination")
    # beq/bne with zero are aliases for beqz/bnez; non-zero comparisons not supported
    if b.mnemonic in ("beq", "bne") and b.rs2 != 0:
        raise NotPair("beq/bne B slot requires rs2==zero")
    if a.mnemonic == "andi":
        if not _is_pow2_imm(a.imm) and _shift_for_zero_test(a.imm) is None:
            raise NotPair(f"andi immediate {a.imm} not pow2 or shift-expressible")
    # slli/srli: any shift amount is accepted
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


@a_is_rsd
@must_chain_base
@exclusive_rd
def _pre_inc_pair(a: Instruction, b: Instruction) -> None:
    """A (RSD form) updates a register; B reads that register as rs1."""
    if (a.mnemonic, b.mnemonic) not in _PRE_INC_TUPLES:
        raise NotPair("bad-tuple")
    # TOOD: check immediate range?
    if b.has_mem_operand and b.imm != 0:
        raise NotPair("B-nonzero-immediate")
    return None


_EPILOGUE_A_MN = frozenset({"addi"})
_EPILOGUE_B_MN = frozenset({"jalr", "ret"})


def _prologue_pair(a: Instruction, b: Instruction) -> None:
    """A reserves stack frame, B stores return address at top of frame
    A: addi sp, sp, -N  - 7-bit uimm*16, nonzero
    B: sw ra, N-4(sp)  - store return address
    """
    if a.rd != 2 or a.rs1 != 2:
        raise NotPair("A-not-addi-sp")
    if not a.nimm_fits(7, 4, nonzero=True):
        raise NotPair("A-big-imm")
    if b.rs1 != 2:
        raise NotPair("B-not-SP-base")
    if b.rs2 != 1:
        raise NotPair("B-not-RA-src")
    if b.imm + b.access_width + a.imm != 0:
        raise NotPair("B-bad-delta")


def _epilogue_pair(a: Instruction, b: Instruction) -> None:
    """A restores sp; B is an unconditional return or jump.

    Order-sensitive: the packet runs A then B, and B is a control transfer, so
    the addi must be A (executes first) and the ret/jalr must be B (executes
    last).  The reverse would run the transfer first and skip the addi, so it is
    not a valid packet -- is_control_transfer also keeps ret/jalr out of A.

    A: addi sp, sp, +N  — 7-bit uimm×16, nonzero (max 2032)
    B: ret or jalr rd∈{0,1} with 12-bit signed offset
    """
    if a.rd != 2 or a.rs1 != 2:
        raise NotPair("A-not-addi-sp")
    if not a.uimm_fits(7, 4, nonzero=True):
        raise NotPair("A-big-imm")
    if b.rd not in (0, 1):
        raise NotPair("B rd must be x0 or x1")
    if not b.imm_fits(12):
        raise NotPair("B-big-imm")


# ---------------------------------------------------------------------------
# arith-jump-pair / mvload-jump-pair
# ---------------------------------------------------------------------------
# Pack a productive instruction into the same packet as a trailing unconditional
# control transfer (the packet's B slot always executes last).  Ported from the
# legacy scheduler's arith_jump / mv_load_jump rules — its single largest
# advantage on real code, and offset-independent (unlike its memory rules).
#
#   arith-jump-pair:   A = RSD ALU op (or li), x0..x15, imm in range
#   mvload-jump-pair:  A = mv / li, or a load with a small (0..3×width) offset
#   B (both):          ret / jr / indirect jalr (imm 0) / direct j / jal x0
#
# Calls (which save a link register) are excluded from the B slot.  Direct jumps
# carry a target offset that is not range-checked here — the same optimism the
# *-branch rules apply (see CLAUDE.md); returns and register-indirect jumps need
# no offset field and are always encodable.

_SMALL_JUMP_MN = frozenset({"ret", "jalr", "j", "jal"})
_MVLOAD_JUMP_A_MN = frozenset({"addi"}) | _ALL_LOAD_MN


def _is_small_jump(insn: Instruction) -> bool:
    """B-slot control transfer: return, register-indirect jump (jr / jalr with
    zero offset), or direct jump (j / jal x0).  Calls are excluded."""
    if insn.is_call:
        return False
    m = insn.mnemonic
    if m == "ret":
        return True
    if m == "jalr":
        return insn.imm in (0, None)
    if m in ("j", "jal"):
        return insn.rd in (0, None)
    return False


@a_is_rsd_or_li
@a_rsd_swappable
@uses_low_regs_here("a.rd", "a.rs1", "a.rs2")
@a_imm_ok
def _arith_jump_pair(a: Instruction, b: Instruction) -> None:
    """RSD ALU op (or li) followed by a small unconditional control transfer."""
    if not _is_small_jump(b):
        raise NotPair("B-not-small-jump")


def _mvload_jump_pair(a: Instruction, b: Instruction) -> None:
    """mv / li, or a small-offset load, followed by a small control transfer."""
    if not _is_small_jump(b):
        raise NotPair("B-not-small-jump")
    if a.is_mv or a.is_li:
        return None
    if a.reads_memory:
        if not _arith_mem_small_offset_ok(a):
            raise NotPair("A-big-imm")
        return None
    raise NotPair("A is not mv/li or a small-offset load")

RULES: list[PairingRule] = [
    PairingRule(
        name="rsd-alu-pair",
        a_mnemonic_set=_RSD_ALU_MN,
        b_mnemonic_set=_RSD_ALU_MN,
        check=_rsd_alu_pair,
    ),
    PairingRule(
        name="chain-alu-pair",
        a_mnemonic_set=_RSD_ALU_MN,
        b_mnemonic_set=_RSD_ALU_MN,
        check=_chain_alu_pair,
    ),
    PairingRule(
        name="load-chain-alu-pair",
        a_mnemonic_set=_SP_LOAD_MN,
        b_mnemonic_set=_RSD_ALU_MN,
        check=_load_chain_alu_pair,
    ),
    PairingRule(
        name="store-chain-alu-pair",
        a_mnemonic_set=_RSD_ALU_MN,
        b_mnemonic_set=_SP_STORE_MN,
        check=_store_chain_alu_pair,
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
        name="arith-mem-pair",
        a_mnemonic_set=_ARITH_MEM_A_MN,
        b_mnemonic_set=_ARITH_MEM_B_MN,
        a_prerequisites=["is_rsd"],
        check=_arith_mem_pair,
    ),
    PairingRule(
        name="dual-arith2-pair",
        a_mnemonic_set=_role_mnems("arith2"),
        b_mnemonic_set=_role_mnems("arith2"),
        check=_dual_arith2,
    ),
    PairingRule(
        name="dual-mem-addi-pair",
        a_mnemonic_set=_role_mnems("mem_addi"),
        b_mnemonic_set=_role_mnems("mem_addi"),
        check=_dual_mem_addi,
    ),
    PairingRule(
        name="dual-mem-shadd-pair",
        a_mnemonic_set=_role_mnems("mem_shadd"),
        b_mnemonic_set=_role_mnems("mem_shadd"),
        check=_dual_mem_shadd,
    ),
    PairingRule(
        name="dual-indep-pair",
        a_mnemonic_set=_role_mnems("indep_pair"),
        b_mnemonic_set=_role_mnems("indep_pair"),
        check=_dual_indep,
    ),
    PairingRule(
        name="chain-li-branch",
        a_mnemonic_set=_LI_BRANCH_A_MN,
        b_mnemonic_set=_LI_BRANCH_B_MN,
        a_prerequisites=["is_li"],
        check=_chain_li_branch,
    ),
    PairingRule(
        name="addi-branch-pair",
        a_mnemonic_set=_ADDI_BRANCH_A_MN,
        b_mnemonic_set=_ADDI_BRANCH_B_MN,
        a_prerequisites=["is_rsd"],
        check=_addi_branch_pair,
    ),
    PairingRule(
        name="chain-bit-test-branch",
        a_mnemonic_set=_BIT_BRANCH_A_MN,
        b_mnemonic_set=_BIT_BRANCH_B_MN,
        check=_chain_bit_test_branch,
    ),
    PairingRule(
        name="pre-inc-pair",
        a_mnemonic_set=_PRE_INC_A_MN,
        b_mnemonic_set=_PRE_INC_B_MN,
        a_prerequisites=["is_rsd"],
        check=_pre_inc_pair,
    ),
    PairingRule(
        name="prologue-pair",
        a_mnemonic_set=frozenset({"addi"}),
        b_mnemonic_set=frozenset({"sw", "sd"}),
        check=_prologue_pair,
    ),
    PairingRule(
        name="epilogue-pair",
        a_mnemonic_set=_EPILOGUE_A_MN,
        b_mnemonic_set=_EPILOGUE_B_MN,
        check=_epilogue_pair,
    ),
    PairingRule(
        name="arith-jump-pair",
        a_mnemonic_set=_RSD_ALU_MN,
        b_mnemonic_set=_SMALL_JUMP_MN,
        check=_arith_jump_pair,
    ),
    PairingRule(
        name="mvload-jump-pair",
        a_mnemonic_set=_MVLOAD_JUMP_A_MN,
        b_mnemonic_set=_SMALL_JUMP_MN,
        check=_mvload_jump_pair,
    ),
]

A_SLOT_DISQUALIFIERS: list[str] = [
    "is_unknown",
    # A control transfer can only be the B (last) slot — the hardware runs A
    # before B, so a transfer in A would never reach B.
    "is_control_transfer",
]

B_SLOT_DISQUALIFIERS: list[str] = [
    "is_unknown",
]

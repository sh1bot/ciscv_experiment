"""
isa/abi.py — ABI-implied liveness effects for call/return/tail instructions.
"""

from __future__ import annotations
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from isa.instruction import Instruction

from isa.registers import ARG_REGS, CALLER_SAVED, CALLEE_SAVED, RET_REGS

_SP = 2   # x2

# Pre-computed sets used repeatedly
_CALL_USES   = ARG_REGS | frozenset({_SP})
_CALL_DEFS   = CALLER_SAVED
_CALL_SEED   = RET_REGS | CALLEE_SAVED

_RET_USES    = RET_REGS | CALLEE_SAVED

_TAIL_USES   = ARG_REGS | frozenset({_SP})

_EMPTY = frozenset()


def call_liveness_effect(insn: "Instruction") -> tuple[
    frozenset,  # implicit uses  (indices 0–63)
    frozenset,  # implicit defs (clobbered)
    frozenset,  # live_out_seed: registers conservatively live after this insn
    bool,       # terminates_function
]:
    """Return the ABI-implied liveness effect of an instruction.

    Priority: mnemonic is checked first; structural jalr/jal patterns are fallback.
    """
    m = insn.mnemonic
    rd = insn.rd
    rs1 = insn.rs1

    # --- Call (pseudo) ---
    if m == "call":
        return (_CALL_USES, _CALL_DEFS, _CALL_SEED, False)

    # --- Return (pseudo) ---
    if m == "ret":
        return (_RET_USES, _EMPTY, _RET_USES, True)

    # --- Tail call (pseudo) ---
    if m == "tail":
        return (_TAIL_USES, _EMPTY, _TAIL_USES, True)

    # --- Encoded call: jal/jalr writing ra (x1) or t0 (x5) ---
    if m in ("jal", "jalr") and rd in (1, 5):
        return (_CALL_USES, _CALL_DEFS, _CALL_SEED, False)

    # --- Encoded return: jalr x0, ra, 0 ---
    if m == "jalr" and rd == 0 and rs1 == 1 and insn.imm == 0:
        return (_RET_USES, _EMPTY, _RET_USES, True)

    # --- jalr x0, rs1 (any form not matching return above) -> tail call ---
    if m == "jalr" and rd == 0:
        return (_TAIL_USES, _EMPTY, _TAIL_USES, True)

    # --- Unclassified jalr (rd != x0, ra, t0) ---
    if m == "jalr":
        return (_EMPTY, _EMPTY, _EMPTY, True)

    # --- Not a call/return/tail ---
    return (_EMPTY, _EMPTY, _EMPTY, False)

"""
isa/abi.py — ABI-implied liveness effects for call/return/tail instructions.
"""

from __future__ import annotations
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from isa.instruction import Instruction

from isa.registers import (
    ARG_REGS, CALLER_SAVED, CALLEE_SAVED, RET_REGS,
    FARG_REGS, FCALLER_SAVED, FCALLEE_SAVED, FRET_REGS,
)

_SP = 2   # x2

# Pre-computed sets used repeatedly
_CALL_INT_USES   = ARG_REGS | frozenset({_SP})
_CALL_INT_DEFS   = CALLER_SAVED
_CALL_FLOAT_USES = FARG_REGS
_CALL_FLOAT_DEFS = FCALLER_SAVED
_CALL_INT_SEED   = RET_REGS | CALLEE_SAVED
_CALL_FLOAT_SEED = FRET_REGS | FCALLEE_SAVED

_RET_INT_USES    = RET_REGS | CALLEE_SAVED
_RET_FLOAT_USES  = FRET_REGS | FCALLEE_SAVED

_TAIL_INT_USES   = ARG_REGS | frozenset({_SP})
_TAIL_FLOAT_USES = FARG_REGS

_EMPTY = frozenset()


def call_liveness_effect(insn: "Instruction") -> tuple[
    frozenset,  # implicit int uses
    frozenset,  # implicit int defs
    frozenset,  # implicit float uses
    frozenset,  # implicit float defs
    frozenset,  # live_out_seed int
    frozenset,  # live_out_seed float
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
        return (_CALL_INT_USES, _CALL_INT_DEFS,
                _CALL_FLOAT_USES, _CALL_FLOAT_DEFS,
                _CALL_INT_SEED, _CALL_FLOAT_SEED, False)

    # --- Return (pseudo) ---
    if m == "ret":
        return (_RET_INT_USES, _EMPTY,
                _RET_FLOAT_USES, _EMPTY,
                _RET_INT_USES, _RET_FLOAT_USES, True)

    # --- Tail call (pseudo) ---
    if m == "tail":
        return (_TAIL_INT_USES, _EMPTY,
                _TAIL_FLOAT_USES, _EMPTY,
                _TAIL_INT_USES, _TAIL_FLOAT_USES, True)

    # --- Encoded call: jal/jalr writing ra (x1) or t0 (x5) ---
    if m in ("jal", "jalr") and rd in (1, 5):
        return (_CALL_INT_USES, _CALL_INT_DEFS,
                _CALL_FLOAT_USES, _CALL_FLOAT_DEFS,
                _CALL_INT_SEED, _CALL_FLOAT_SEED, False)

    # --- Encoded return: jalr x0, ra, 0 ---
    if m == "jalr" and rd == 0 and rs1 == 1 and insn.imm == 0:
        return (_RET_INT_USES, _EMPTY,
                _RET_FLOAT_USES, _EMPTY,
                _RET_INT_USES, _RET_FLOAT_USES, True)

    # --- jalr x0, rs1 (any form not matching return above) -> tail call ---
    if m == "jalr" and rd == 0:
        return (_TAIL_INT_USES, _EMPTY,
                _TAIL_FLOAT_USES, _EMPTY,
                _TAIL_INT_USES, _TAIL_FLOAT_USES, True)

    # --- Unclassified jalr (rd != x0, ra, t0) ---
    if m == "jalr":
        return (_EMPTY, _EMPTY, _EMPTY, _EMPTY, _EMPTY, _EMPTY, True)

    # --- Not a call/return/tail ---
    return (_EMPTY, _EMPTY, _EMPTY, _EMPTY, _EMPTY, _EMPTY, False)

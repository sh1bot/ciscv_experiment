"""
analysis/liveness.py — Register liveness analysis.

Two-phase approach:
1. Global pass: iterative backward dataflow over the CFG of a function.
2. Local pass: backward propagation within each block, assigning
   live_in/live_out to every individual Instruction.
"""

from __future__ import annotations
from dataclasses import dataclass, field
from collections import deque
from typing import Optional

from isa.abi import call_liveness_effect
from isa.registers import CALLEE_SAVED, FCALLEE_SAVED


@dataclass
class LivenessResult:
    """Result of global liveness pass over a set of blocks."""
    live_in_int:    dict = field(default_factory=dict)   # label -> frozenset[int]
    live_out_int:   dict = field(default_factory=dict)
    live_in_float:  dict = field(default_factory=dict)
    live_out_float: dict = field(default_factory=dict)


def _block_use_def(block) -> tuple[frozenset, frozenset, frozenset, frozenset, bool, frozenset, frozenset]:
    """Compute use[B], def[B] for a block.

    Returns:
      use_int, def_int, use_float, def_float,
      terminates, exit_seed_int, exit_seed_float
    """
    use_int   = set()
    def_int   = set()
    use_float = set()
    def_float = set()
    terminates = False
    exit_seed_int   = frozenset()
    exit_seed_float = frozenset()

    for insn in block.instructions:
        # ABI effects first
        (imp_use_i, imp_def_i,
         imp_use_f, imp_def_f,
         seed_i, seed_f,
         term) = call_liveness_effect(insn)

        if term:
            terminates = True
            exit_seed_int   = seed_i
            exit_seed_float = seed_f

        # For ABI-controlled instructions (call/ret/tail and their encoded forms),
        # use ONLY the ABI implicit uses, not the raw register operands.
        # This prevents ra (x1) from appearing as a live use of "ret".
        is_abi = bool(imp_use_i or imp_use_f or imp_def_i or imp_def_f or
                      seed_i or seed_f or term)

        if not is_abi:
            # Integer uses: instruction's explicit uses
            for r in insn.uses_int:
                if r not in def_int:
                    use_int.add(r)
        # ABI implicit uses always added
        for r in imp_use_i:
            if r not in def_int:
                use_int.add(r)

        # Integer defs
        def_int.update(insn.defs_int)
        def_int.update(imp_def_i)

        if not is_abi:
            # Float uses
            for r in insn.uses_float:
                if r not in def_float:
                    use_float.add(r)
        for r in imp_use_f:
            if r not in def_float:
                use_float.add(r)

        # Float defs
        def_float.update(insn.defs_float)
        def_float.update(imp_def_f)

    return (frozenset(use_int), frozenset(def_int),
            frozenset(use_float), frozenset(def_float),
            terminates, exit_seed_int, exit_seed_float)


def compute_global_liveness(blocks: list) -> LivenessResult:
    """Standard iterative backward dataflow over a list of BasicBlocks.

    Blocks may belong to multiple functions; each function_entry block
    gets ARG_REGS / FARG_REGS permanently unioned into its live_in.

    The list of blocks is treated as a single analysis unit (function scope).
    Blocks are identified by their .label attribute.
    """
    result = LivenessResult()

    # Initialize
    for bb in blocks:
        key = bb.label
        result.live_in_int[key]    = frozenset()
        result.live_out_int[key]   = frozenset()
        result.live_in_float[key]  = frozenset()
        result.live_out_float[key] = frozenset()

    # Pre-compute use/def for each block
    use_def = {}
    for bb in blocks:
        use_def[bb.label] = _block_use_def(bb)

    # Worklist — iterate until stable
    worklist = deque(blocks)
    in_worklist = {bb.label: True for bb in blocks}

    while worklist:
        bb = worklist.popleft()
        key = bb.label
        in_worklist[key] = False

        (use_i, def_i, use_f, def_f,
         terminates, exit_seed_i, exit_seed_f) = use_def[key]

        # --- Compute live_out ---
        if terminates:
            # No successors contribute; live_out = exit_seed (applied every iteration)
            new_out_i = exit_seed_i
            new_out_f = exit_seed_f
        else:
            new_out_i = exit_seed_i  # always union exit_seed (may be empty)
            new_out_f = exit_seed_f
            for succ in bb.successors:
                new_out_i = new_out_i | result.live_in_int[succ.label]
                new_out_f = new_out_f | result.live_in_float[succ.label]

        # --- Compute live_in ---
        new_in_i = use_i | (new_out_i - def_i)
        new_in_f = use_f | (new_out_f - def_f)

        # ENTRY_SEED: function-entry blocks with at least one instruction have
        # CALLEE_SAVED registers considered live at entry (the caller placed values
        # there; the function must preserve them). Applied without kill analysis
        # (additive per-iteration term per plan §7).
        # Empty blocks (no instructions) are excluded to avoid spurious liveness.
        if bb.is_function_entry and bb.instructions:
            new_in_i = new_in_i | CALLEE_SAVED
            new_in_f = new_in_f | FCALLEE_SAVED

        # Check for change
        if (new_in_i  != result.live_in_int[key]  or
            new_out_i != result.live_out_int[key]  or
            new_in_f  != result.live_in_float[key] or
            new_out_f != result.live_out_float[key]):

            result.live_in_int[key]    = new_in_i
            result.live_out_int[key]   = new_out_i
            result.live_in_float[key]  = new_in_f
            result.live_out_float[key] = new_out_f

            # Re-add predecessors to worklist
            for pred in bb.predecessors:
                if not in_worklist.get(pred.label, False):
                    worklist.append(pred)
                    in_worklist[pred.label] = True

    return result


def compute_local_liveness(block, global_result: LivenessResult) -> None:
    """Backward pass within a single block, setting live_in/live_out on each Instruction.

    Uses the block's global live_out as the initial seed.
    Mid-block call seeds are applied at call instructions.
    """
    key = block.label

    # Seed from global live_out
    live_i = global_result.live_out_int.get(key, frozenset())
    live_f = global_result.live_out_float.get(key, frozenset())

    for insn in reversed(block.instructions):
        # ABI effects
        (imp_use_i, imp_def_i,
         imp_use_f, imp_def_f,
         seed_i, seed_f,
         term) = call_liveness_effect(insn)

        # Apply mid-block call seed: union current live with seed BEFORE setting live_out
        # Per plan §7: "live_out for the call is unioned with its live_out_seed"
        if seed_i or seed_f:
            live_i = live_i | seed_i
            live_f = live_f | seed_f

        # live_out of this instruction = current live set (after seed application)
        insn.live_out  = frozenset(live_i)
        insn.flive_out = frozenset(live_f)

        # live_in = (live_out - defs) ∪ uses
        # For ABI-controlled instructions, use only ABI implicit uses (not raw operands)
        is_abi = bool(imp_use_i or imp_use_f or imp_def_i or imp_def_f or
                      seed_i or seed_f or term)
        if is_abi:
            raw_uses_i = frozenset()
            raw_uses_f = frozenset()
        else:
            raw_uses_i = insn.uses_int
            raw_uses_f = insn.uses_float
        live_i = (live_i - insn.defs_int - imp_def_i) | raw_uses_i | imp_use_i
        live_f = (live_f - insn.defs_float - imp_def_f) | raw_uses_f | imp_use_f

        insn.live_in  = frozenset(live_i)
        insn.flive_in = frozenset(live_f)

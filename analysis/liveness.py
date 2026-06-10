"""
analysis/liveness.py — Register liveness analysis.

Two-phase approach:
1. Global pass: iterative backward dataflow over the CFG of a function.
2. Local pass: backward propagation within each block, assigning
   live_in/live_out to every individual Instruction.

A single frozenset[int] domain covers all registers (indices 0–31 integer,
32–63 float); there is no separate float domain.
"""

from __future__ import annotations
from dataclasses import dataclass, field
from collections import deque
from typing import Optional

from isa.abi import call_liveness_effect
from isa.registers import ARG_REGS, CALLEE_SAVED


@dataclass
class LivenessResult:
    """Result of global liveness pass over a set of blocks."""
    live_in:    dict = field(default_factory=dict)   # label -> frozenset[int]
    live_out:   dict = field(default_factory=dict)


def _block_use_def(block) -> tuple[frozenset, frozenset, bool, frozenset]:
    """Compute use[B], def[B] for a block.

    Returns:
      use, def, terminates, exit_seed
    """
    use_set   = set()
    def_set   = set()
    terminates = False
    exit_seed  = frozenset()

    for insn in block.instructions:
        # ABI effects first
        imp_use, imp_def, seed, term = call_liveness_effect(insn)

        if term:
            terminates = True
            exit_seed  = seed

        for r in insn.uses_regs:
            if r not in def_set:
                use_set.add(r)

        # ABI implicit uses (e.g. caller-saved regs at calls)
        for r in imp_use:
            if r not in def_set:
                use_set.add(r)

        # Defs
        def_set.update(insn.defs_regs)
        def_set.update(imp_def)

    return (frozenset(use_set), frozenset(def_set), terminates, exit_seed)


def compute_global_liveness(blocks: list) -> LivenessResult:
    """Standard iterative backward dataflow over a list of BasicBlocks.

    Each function_entry block gets ARG_REGS | CALLEE_SAVED permanently
    unioned into its live_in: arguments carry caller values, and
    callee-saved registers hold caller state that must be preserved.

    The list of blocks is treated as a single analysis unit (function scope).
    Blocks are identified by id(bb) (object identity).
    """
    result = LivenessResult()

    # Initialize
    for bb in blocks:
        key = id(bb)
        result.live_in[key]  = frozenset()
        result.live_out[key] = frozenset()

    # Pre-compute use/def for each block
    use_def = {}
    for bb in blocks:
        use_def[id(bb)] = _block_use_def(bb)

    # Worklist — iterate until stable
    worklist = deque(blocks)
    in_worklist = {id(bb): True for bb in blocks}

    while worklist:
        bb = worklist.popleft()
        key = id(bb)
        in_worklist[key] = False

        use, defs, terminates, exit_seed = use_def[key]

        # --- Compute live_out ---
        if terminates:
            new_out = exit_seed
        else:
            new_out = exit_seed  # may be empty for non-terminating blocks
            for succ in bb.successors:
                if id(succ) in result.live_in:
                    new_out = new_out | result.live_in[id(succ)]

        # --- Compute live_in ---
        new_in = use | (new_out - defs)

        # ENTRY_SEED: at function entry, argument registers carry caller values
        # and callee-saved registers hold caller state that must be preserved.
        if bb.is_function_entry and bb.instructions:
            new_in = new_in | ARG_REGS | CALLEE_SAVED

        # Check for change
        if new_in != result.live_in[key] or new_out != result.live_out[key]:
            result.live_in[key]  = new_in
            result.live_out[key] = new_out

            # Re-add predecessors to worklist (only those in this function)
            for pred in bb.predecessors:
                if id(pred) in use_def and not in_worklist.get(id(pred), False):
                    worklist.append(pred)
                    in_worklist[id(pred)] = True

    return result


def compute_local_liveness(block, global_result: LivenessResult) -> None:
    """Backward pass within a single block, setting live_in/live_out on each Instruction.

    Uses the block's global live_out as the initial seed.
    Mid-block call seeds are applied at call instructions.
    """
    key = id(block)

    # Seed from global live_out
    live = global_result.live_out.get(key, frozenset())

    for insn in reversed(block.instructions):
        # ABI effects
        imp_use, imp_def, seed, term = call_liveness_effect(insn)

        # Apply mid-block call seed: union current live with seed BEFORE setting live_out
        if seed:
            live = live | seed

        # live_out of this instruction = current live set (after seed application)
        insn.live_out = frozenset(live)

        live = (live - insn.defs_regs - imp_def) | insn.uses_regs | imp_use
        insn.live_in = frozenset(live)

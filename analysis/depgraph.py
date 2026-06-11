"""
analysis/depgraph.py — Per-block dependency graph construction.

Builds a DAG of ordering constraints: RAW, WAR, WAW, memory ordering,
and barrier edges.
"""

from __future__ import annotations
from dataclasses import dataclass, field

from analysis.cfg import BasicBlock
from isa.instruction import Instruction


@dataclass
class DepGraph:
    instructions: list  # list[Instruction]
    # edges[i] = set of indices j that must come AFTER instructions[i]
    edges: list  # list[set[int]]

    @property
    def n(self) -> int:
        return len(self.instructions)


def build_dep_graph(block: BasicBlock, same_base_reorder: bool = False) -> DepGraph:
    """Build dependency graph for a basic block."""
    insns = block.instructions
    n = len(insns)
    edges: list = [set() for _ in range(n)]

    def add_edge(i: int, j: int):
        if i < j:
            edges[i].add(j)

    # Track last writer and readers for each register (unified 0–63)
    last_writer: dict = {}   # reg -> last writer index
    readers: dict = {}       # reg -> list of reader indices

    # Memory dependency tracking
    last_mem_op: int = -1        # index of last memory operation
    mem_ops: list = []           # (index, base_reg, offset, width) for same-base check

    # Barrier tracking
    last_barrier: int = -1       # index of last barrier instruction

    for i, insn in enumerate(insns):
        # --- Barrier edges ---
        is_barrier = (insn.is_atomic or insn.is_fence or insn.is_csr or
                      insn.is_call or insn.is_tail or
                      insn.mnemonic in ("ecall", "ebreak"))

        if is_barrier:
            # All preceding instructions must complete before barrier
            for j in range(i):
                add_edge(j, i)
            last_barrier = i

        # If we had a barrier, everything after must wait
        if last_barrier >= 0 and last_barrier < i:
            add_edge(last_barrier, i)
            # Actually, the barrier already has edges from all j < i,
            # so we only need barrier -> i for the instruction right after
            # (transitivity handles the rest). But to be safe for non-transitive
            # queries, add the direct edge.

        # --- Register RAW/WAW/WAR edges ---
        for reg in insn.uses_regs:
            # RAW: if there's a writer before us, we depend on it
            if reg in last_writer:
                add_edge(last_writer[reg], i)
            # Track us as a reader
            readers.setdefault(reg, []).append(i)

        for reg in insn.defs_regs:
            # WAR: all readers before us must complete before we write
            for j in readers.get(reg, []):
                add_edge(j, i)
            # WAW: last writer before us
            if reg in last_writer:
                add_edge(last_writer[reg], i)
            last_writer[reg] = i
            # Clear old readers for this reg (they all now precede us)
            readers[reg] = []

        # --- ABI-implied register effects ---
        from isa.abi import call_liveness_effect
        imp_use, imp_def, _, _ = call_liveness_effect(insn)

        for reg in imp_use:
            if reg in last_writer:
                add_edge(last_writer[reg], i)
            readers.setdefault(reg, []).append(i)

        for reg in imp_def:
            for j in readers.get(reg, []):
                add_edge(j, i)
            if reg in last_writer:
                add_edge(last_writer[reg], i)
            last_writer[reg] = i
            readers[reg] = []

        # --- Memory ordering edges ---
        is_mem = insn.has_mem_operand

        if is_mem:
            if last_mem_op >= 0:
                if same_base_reorder:
                    # Check if we can prove independence with the preceding mem op
                    dep = True
                    if mem_ops:
                        prev_idx, prev_base, prev_off, prev_w = mem_ops[-1]
                        cur_w = insn.access_width if insn.access_width is not None else 4
                        eff_prev_w = prev_w if prev_w is not None else 4
                        if (prev_base is not None and insn.rs1 == prev_base and
                                prev_off is not None and insn.imm is not None):
                            # Check no unknown instruction between them
                            has_unknown = any(
                                insns[k].is_unknown
                                for k in range(prev_idx + 1, i)
                            )
                            if not has_unknown:
                                # Check base reg not written between them
                                base_modified = any(
                                    insn.rs1 in insns[k].defs_regs
                                    for k in range(prev_idx + 1, i)
                                )
                                if not base_modified:
                                    # Check non-overlapping byte ranges
                                    a_lo = prev_off
                                    a_hi = prev_off + eff_prev_w
                                    b_lo = insn.imm
                                    b_hi = insn.imm + cur_w
                                    if a_hi <= b_lo or b_hi <= a_lo:
                                        dep = False
                    if dep:
                        add_edge(last_mem_op, i)
                else:
                    add_edge(last_mem_op, i)

        # Update memory op tracking
        if is_mem:
            mem_ops.append((i, insn.rs1, insn.imm, insn.access_width))
            last_mem_op = i

        # --- auipc + next-insn dependency (§11 PCREL heuristic) ---
        if insn.mnemonic == "auipc" and insn.rd is not None:
            # Add implicit dependency to next instruction if it reads rd
            # This prevents the scheduler from separating auipc from its paired insn
            if i + 1 < n:
                next_insn = insns[i + 1]
                if (next_insn.rs1 == insn.rd or next_insn.rs2 == insn.rd or
                        insn.rd in next_insn.uses_regs):
                    add_edge(i, i + 1)

    return DepGraph(instructions=insns, edges=edges)

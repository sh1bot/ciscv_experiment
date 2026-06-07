"""
analysis/depgraph.py — Per-basic-block dependency graph for the RV32 scheduler.

Builds a directed acyclic graph of ordering constraints required for
correctness.  Edge types:

  RAW  — read-after-write   : B reads a register A writes
  WAR  — write-after-read   : B writes a register A reads
  WAW  — write-after-write  : A and B both write the same register
  MEM  — memory ordering    : conservative sequential ordering between
                              any two memory operations; optionally relaxed
                              when --same-base-reorder is active
  BAR  — barrier            : AMO / fence / fence.i / ecall / ebreak /
                              call / tail  must come after *all* preceding
                              instructions in the block

edges[i] is the set of instruction indices that must come *after* i
(i.e. i → j means j cannot be scheduled before i).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum, auto
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    # Avoid hard import at module level so the module is usable even before
    # isa/instruction.py and analysis/cfg.py are fully written.
    from isa.instruction import Instruction
    from analysis.cfg import BasicBlock


# ---------------------------------------------------------------------------
# Edge-type annotation (stored separately for diagnostics / debugging)
# ---------------------------------------------------------------------------

class EdgeKind(Enum):
    RAW = auto()   # read-after-write
    WAR = auto()   # write-after-read
    WAW = auto()   # write-after-write
    MEM = auto()   # memory ordering (conservative or relaxed)
    BAR = auto()   # barrier ordering


# ---------------------------------------------------------------------------
# DepGraph dataclass
# ---------------------------------------------------------------------------

@dataclass
class DepGraph:
    """Dependency graph for one basic block.

    ``instructions``  — the instructions in program order (index = position).
    ``edges``         — edges[i] is the set of j > i that *must* come after i.
    ``edge_kinds``    — optional metadata: edge_kinds[(i, j)] = set[EdgeKind].
                        Populated only when the graph is built; consumers that
                        only need scheduling correctness can ignore this.
    """
    instructions: list[Instruction]
    # edges[i] = set of instruction indices j such that i must precede j
    edges: list[set[int]] = field(default_factory=list)
    # diagnostic metadata — not required by the scheduler
    edge_kinds: dict[tuple[int, int], set[EdgeKind]] = field(
        default_factory=dict
    )

    # ------------------------------------------------------------------
    # Convenience helpers
    # ------------------------------------------------------------------

    def add_edge(self, i: int, j: int, kind: EdgeKind) -> None:
        """Record that instruction i must precede instruction j."""
        if i == j:
            return
        # Normalise: i < j enforced by the builder, but guard anyway.
        self.edges[i].add(j)
        key = (i, j)
        if key not in self.edge_kinds:
            self.edge_kinds[key] = set()
        self.edge_kinds[key].add(kind)

    def predecessors(self, j: int) -> set[int]:
        """Return all i such that i must precede j."""
        return {i for i, succs in enumerate(self.edges) if j in succs}

    def topological_sort(self) -> list[int]:
        """Kahn's algorithm; raises ValueError on cycle (should never happen)."""
        n = len(self.instructions)
        in_degree = [0] * n
        for i, succs in enumerate(self.edges):
            for j in succs:
                in_degree[j] += 1
        ready = [i for i in range(n) if in_degree[i] == 0]
        order: list[int] = []
        while ready:
            i = ready.pop(0)
            order.append(i)
            for j in sorted(self.edges[i]):  # sorted for determinism
                in_degree[j] -= 1
                if in_degree[j] == 0:
                    ready.append(j)
        if len(order) != n:
            raise ValueError("Cycle detected in dependency graph")
        return order


# ---------------------------------------------------------------------------
# _is_barrier_instruction — the set of mnemonics (or properties) that force a
# full barrier.  The plan names: AMOs, fence, fence.i, ecall, ebreak, call,
# tail.
#
# ASSUMPTION [A1]: "AMO" is detected via Instruction.is_atomic.
# ASSUMPTION [A2]: "call" and "tail" are detected by mnemonic equality; the
#   plan does not specify a property for them.  is_call covers "call"; for
#   "tail" we check the mnemonic directly because is_call / is_return do not
#   obviously include it.
# ASSUMPTION [A3]: "fence.i" is a distinct mnemonic from "fence"; both are
#   covered.  is_fence may cover both — we use is_fence AND fall back on mnemonic
#   matching.
# ---------------------------------------------------------------------------

_BARRIER_MNEMONICS: frozenset[str] = frozenset({
    "fence", "fence.i", "ecall", "ebreak", "call", "tail",
})


def _is_barrier(instr: Instruction) -> bool:
    """Return True if this instruction must act as a full ordering barrier."""
    # is_atomic covers AMO instructions (lr/sc/amo*)
    if getattr(instr, "is_atomic", False):
        return True
    # is_fence should cover fence / fence.i; fall back to mnemonic check
    if getattr(instr, "is_fence", False):
        return True
    # is_call covers 'call' pseudo-instruction
    if getattr(instr, "is_call", False):
        return True
    # Explicit mnemonic matching for anything the properties might miss
    mnemonic = instr.mnemonic.lower()
    if mnemonic in _BARRIER_MNEMONICS:
        return True
    return False


# ---------------------------------------------------------------------------
# _same_base_reorder_ok — decide whether two memory operations can have their
# conservative MEM edge dropped under --same-base-reorder.
#
# The plan says: drop the MEM edge when
#   1. both ops share the same base register, AND
#   2. that register is NOT written between them, AND
#   3. they have non-overlapping byte ranges (same offset ± access width).
#
# AMBIGUITY [C1]: "same offset ± access width" is under-specified.  We
#   interpret it as: the byte intervals [offset_A, offset_A+width_A) and
#   [offset_B, offset_B+width_B) must be disjoint, where width comes from
#   the mnemonic (lb=1, lh/sh=2, lw/sw=4, ld/sd=8, etc.).
#
# ASSUMPTION [A4]: The base register for a load/store is rs1 (integer).
# ASSUMPTION [A5]: The immediate field (Instruction.imm) is the byte offset.
#   If imm is None, we cannot prove non-overlap → conservative (return False).
# ASSUMPTION [A6]: Access width is inferred from the mnemonic.  Instructions
#   not matching a known pattern default to width=4 (word).
# ---------------------------------------------------------------------------

_MNEMONIC_WIDTH: dict[str, int] = {
    # byte
    "lb": 1, "lbu": 1, "sb": 1,
    # halfword
    "lh": 2, "lhu": 2, "sh": 2,
    # word
    "lw": 4, "sw": 4, "lwu": 4,
    # double (RV64)
    "ld": 8, "sd": 8,
    # float single
    "flw": 4, "fsw": 4,
    # float double
    "fld": 8, "fsd": 8,
}
_DEFAULT_ACCESS_WIDTH = 4


def _access_width(instr: Instruction) -> int:
    return _MNEMONIC_WIDTH.get(instr.mnemonic.lower(), _DEFAULT_ACCESS_WIDTH)


def _same_base_reorder_ok(
    a: Instruction,
    b: Instruction,
    instrs_between: list[Instruction],
) -> bool:
    """Return True if the MEM edge from a to b can be dropped."""
    base_a = getattr(a, "rs1", None)
    base_b = getattr(b, "rs1", None)
    if base_a is None or base_b is None:
        return False
    if base_a != base_b:
        return False

    # The base register must not be written by any instruction between a and b.
    for mid in instrs_between:
        defs = getattr(mid, "defs_int", frozenset())
        if base_a in defs:
            return False

    # Non-overlapping byte ranges.
    imm_a = getattr(a, "imm", None)
    imm_b = getattr(b, "imm", None)
    if imm_a is None or imm_b is None:
        return False

    width_a = _access_width(a)
    width_b = _access_width(b)

    lo_a, hi_a = imm_a, imm_a + width_a  # half-open interval
    lo_b, hi_b = imm_b, imm_b + width_b

    # Disjoint iff one ends before the other starts
    return hi_a <= lo_b or hi_b <= lo_a


# ---------------------------------------------------------------------------
# build_dep_graph — main entry point
# ---------------------------------------------------------------------------

def build_dep_graph(
    block: BasicBlock,
    same_base_reorder: bool = False,
) -> DepGraph:
    """Build the dependency graph for *block*.

    Parameters
    ----------
    block:
        A single basic block.  Only ``block.instructions`` is used; CFG edges
        are not consulted.
    same_base_reorder:
        When True, relax conservative memory ordering between two load/store
        operations that share the same base register and have provably
        non-overlapping byte ranges (see ``_same_base_reorder_ok``).

    Returns
    -------
    DepGraph
        A ``DepGraph`` where ``edges[i]`` is the set of indices that *must*
        come after instruction ``i`` in any valid schedule.
    """
    instrs = block.instructions
    n = len(instrs)

    graph = DepGraph(
        instructions=instrs,
        edges=[set() for _ in range(n)],
        edge_kinds={},
    )

    if n == 0:
        return graph

    # ------------------------------------------------------------------
    # Register dependence edges (RAW, WAR, WAW) — integer and float
    # ------------------------------------------------------------------
    # For each register, track the most recent writer and all readers since the
    # last write.  We scan in program order.
    #
    # ASSUMPTION [A7]: Only intra-block dependences are tracked; live-in
    #   registers from predecessors are ignored here (liveness analysis is a
    #   separate pass).
    #
    # ASSUMPTION [A8]: Both integer and float registers are handled
    #   symmetrically using the defs_int/uses_int and defs_float/uses_float
    #   frozensets from the Instruction dataclass.

    def _add_register_deps(
        reg_attr_defs: str,
        reg_attr_uses: str,
    ) -> None:
        """Generic helper for one register file (int or float)."""
        # last_writer[r] = index of the last instruction that defined r
        last_writer: dict[int, int] = {}
        # last_readers[r] = list of indices of instructions that read r since
        #                    the last write to r (or since block start)
        last_readers: dict[int, list[int]] = {}

        for j, instr in enumerate(instrs):
            defs: frozenset[int] = getattr(instr, reg_attr_defs, frozenset())
            uses: frozenset[int] = getattr(instr, reg_attr_uses, frozenset())

            # RAW: j reads r and some earlier i wrote r → i →(RAW) j
            for r in uses:
                if r in last_writer:
                    i = last_writer[r]
                    graph.add_edge(i, j, EdgeKind.RAW)

            # WAR: j writes r and some earlier i read r → i →(WAR) j
            # WAW: j writes r and some earlier i wrote r → i →(WAW) j
            for r in defs:
                # WAW
                if r in last_writer:
                    i = last_writer[r]
                    graph.add_edge(i, j, EdgeKind.WAW)
                # WAR — all readers since the last write
                for i in last_readers.get(r, []):
                    graph.add_edge(i, j, EdgeKind.WAR)

            # Update tracking after processing dependences for j.
            for r in defs:
                last_writer[r] = j
                last_readers[r] = []  # reset readers after new definition

            for r in uses:
                if r not in last_readers:
                    last_readers[r] = []
                last_readers[r].append(j)

    _add_register_deps("defs_int", "uses_int")
    _add_register_deps("defs_float", "uses_float")

    # ------------------------------------------------------------------
    # Memory ordering edges (MEM)
    # ------------------------------------------------------------------
    # Build the list of indices of memory operations in program order.
    mem_indices: list[int] = [
        i for i, instr in enumerate(instrs)
        if getattr(instr, "reads_memory", False)
        or getattr(instr, "writes_memory", False)
    ]

    # ASSUMPTION [A9]: "each memory operation has a dependency edge from the
    #   previous memory operation" means a chain: mem[k-1] → mem[k] for all k.
    #   We do NOT add edges from every prior memory op to every later one
    #   (i.e., not a full clique) — only the immediately preceding one.
    #   This is the minimal set that enforces sequential memory order while
    #   allowing a scheduler to respect the transitive closure.
    #
    # AMBIGUITY [C2]: The plan says "from the previous memory operation" which
    #   implies a chain.  A full-clique would also be correct but more
    #   restrictive.  We use the chain interpretation.

    for k in range(1, len(mem_indices)):
        prev_mem = mem_indices[k - 1]
        curr_mem = mem_indices[k]
        a = instrs[prev_mem]
        b = instrs[curr_mem]

        if same_base_reorder and _same_base_reorder_ok(
            a, b, instrs[prev_mem + 1 : curr_mem]
        ):
            # Relaxed: drop the conservative MEM edge.
            pass
        else:
            graph.add_edge(prev_mem, curr_mem, EdgeKind.MEM)

    # ------------------------------------------------------------------
    # Barrier edges (BAR)
    # ------------------------------------------------------------------
    # A barrier instruction must come after *all* preceding instructions.
    #
    # ASSUMPTION [A10]: "ordering edges from all preceding instructions"
    #   means every instruction at index 0 .. barrier_index-1 gets an edge to
    #   the barrier instruction.  The plan does not say whether instructions
    #   *after* the barrier also get edges *from* the barrier; we do NOT add
    #   those here — forward constraints from a barrier are naturally covered
    #   by the register and memory edges of whatever follows it.
    #
    # AMBIGUITY [C3]: The plan is silent on whether barriers also block
    #   reordering of instructions *after* them with each other.  We only
    #   enforce "all prior instructions before the barrier", which is the
    #   stated requirement.
    #
    # ASSUMPTION [A11]: Unknown instructions (is_unknown=True) are explicitly
    #   NOT full barriers per the plan.  They receive conservative MEM edges
    #   against adjacent memory ops (handled above via the MEM chain because
    #   is_unknown instructions that also read/write memory appear in
    #   mem_indices).  For unknown non-memory instructions the plan says other
    #   non-memory instructions *may* still be reordered around them if
    #   register deps permit — so we add no extra edges for unknown instructions
    #   here.
    #
    # AMBIGUITY [C4]: For unknown instructions that DO access memory, the plan
    #   says they get "conservative memory ordering edges against all adjacent
    #   memory operations."  "Adjacent" is not precisely defined.  We interpret
    #   this as: the nearest preceding and nearest following memory operation
    #   get edges to/from the unknown-memory instruction.  The MEM chain above
    #   already handles the nearest-predecessor edge.  We add an explicit edge
    #   to the nearest successor mem op below.

    # First pass: collect unknown memory instructions and add nearest-successor
    # MEM edges that the simple chain above would have missed.
    # (In the chain a→b→c, if b is unknown+memory and c is ordinary memory,
    #  the chain already covers a→b and b→c.  The "adjacent" interpretation
    #  does not require additional edges beyond the chain.  We include this
    #  block for completeness in case someone removes the chain logic.)

    # Build a quick lookup: for each mem index position k, what is the next
    # mem index position?
    next_mem: dict[int, int] = {}
    for k in range(len(mem_indices) - 1):
        next_mem[mem_indices[k]] = mem_indices[k + 1]

    for i, instr in enumerate(instrs):
        if not getattr(instr, "is_unknown", False):
            continue
        reads_mem = getattr(instr, "reads_memory", False)
        writes_mem = getattr(instr, "writes_memory", False)
        if reads_mem or writes_mem:
            # Ensure edge to nearest following memory op exists.
            if i in next_mem:
                j = next_mem[i]
                graph.add_edge(i, j, EdgeKind.MEM)

    # Second pass: barrier instructions get edges from ALL preceding instrs.
    for j, instr in enumerate(instrs):
        if not _is_barrier(instr):
            continue
        for i in range(j):
            graph.add_edge(i, j, EdgeKind.BAR)

    return graph

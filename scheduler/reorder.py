"""
scheduler/reorder.py — Instruction reordering for maximum pairing.

Three modes:
  FORWARD — source order, no reordering
  LIST    — list scheduling with lookahead (default)
  BNB     — branch-and-bound (exhaustive, budgeted)
"""

from __future__ import annotations
import enum
from typing import Optional

from analysis.cfg import BasicBlock
from analysis.depgraph import DepGraph, build_dep_graph
from analysis.liveness import compute_local_liveness
from scheduler.pairing import can_pair, greedy_pair, find_b_partners
from scheduler.rules import ALL_BRANCH_MN
from isa.instruction import Instruction


class ScheduleMode(enum.Enum):
    FORWARD = "forward"
    LIST    = "list"
    BNB     = "bnb"


WINDOW_SIZE   = 16
WINDOW_OVERLAP = 0       # instructions carried from end of one window into the next
NODE_BUDGET  = 50_000
STAGNATION   = 5_000
STALL_FOR_PAIR = True    # defer instructions whose pairable branch successor isn't ready yet


def schedule(block: BasicBlock, graph: Optional[DepGraph], mode: ScheduleMode) -> list:
    """Return a topological sort of graph that maximises pair count.

    graph may be None in FORWARD mode.
    """
    if mode == ScheduleMode.FORWARD:
        return list(block.instructions)

    insns = block.instructions
    if not insns:
        return []

    if graph is None:
        graph = build_dep_graph(block)

    if mode == ScheduleMode.LIST:
        ordered = _list_schedule(insns, graph)
    else:  # BNB
        ordered = _bnb_schedule(insns, graph)

    return ordered


# ---------------------------------------------------------------------------
# Utility: compute critical path lengths (in hops)
# ---------------------------------------------------------------------------

def _compute_critical_path(n: int, edges: list) -> list:
    """Compute longest path from each node to any sink (reverse topological).

    Returns a list of ints: crit_path[i] = max hops from i to end.
    """
    crit = [0] * n
    # Process in reverse topological order (for a DAG, reverse iteration works
    # if edges only go forward — which they do since i < j always)
    for i in range(n - 1, -1, -1):
        for j in edges[i]:
            crit[i] = max(crit[i], 1 + crit[j])
    return crit


# ---------------------------------------------------------------------------
# Mode 2: List scheduling
# ---------------------------------------------------------------------------

def _count_pairings_in_ready(insn: Instruction, ready: list) -> int:
    """Count how many instructions in ready can pair with insn (either direction)."""
    others = [o for o in ready if o is not insn]
    # insn as A-slot
    as_a = len(find_b_partners(insn, [o for o in others if o.b_slot_ok]))
    # insn as B-slot: each other instruction is A, insn is the single B candidate
    as_b = sum(1 for o in others if o.a_slot_ok and find_b_partners(o, [insn])) if insn.b_slot_ok else 0
    return as_a + as_b


def _list_schedule(insns: list, graph: DepGraph) -> list:
    """List scheduling with lookahead.

    Maintains a ready queue of instructions with all dependencies satisfied.
    Uses a 'free' slot for the last unmatched instruction.
    """
    n = graph.n
    if n == 0:
        return []

    crit = _compute_critical_path(n, graph.edges)

    # Build in-degree for ready-queue tracking
    in_degree = [0] * n
    for i in range(n):
        for j in graph.edges[i]:
            in_degree[j] += 1

    emitted = [False] * n
    result: list = []
    free: Optional[Instruction] = None   # held but not yet appended to result

    # ready: sorted list of ready instruction indices; ready_set mirrors it
    ready: list = []
    ready_set: set = set()

    def _unblock(idx: int):
        """Decrement successors' in-degrees; add newly-ready to ready."""
        for j in graph.edges[idx]:
            in_degree[j] -= 1
            if in_degree[j] == 0 and not emitted[j]:
                ready.append(j)
                ready_set.add(j)

    def _emit_idx(idx: int) -> Instruction:
        """Mark instruction as emitted, unblock successors."""
        emitted[idx] = True
        ready_set.discard(idx)
        _unblock(idx)
        return insns[idx]

    def _ready_insns():
        return [insns[i] for i in ready]

    def _has_unready_pair_successor(i: int) -> bool:
        """True if i has an unready pairable dep-graph successor and no ready partner.

        Only fires when the instruction is currently stranded (nothing in the
        ready queue pairs with it) but has a pairable successor still blocked.
        Restricts the successor search to instructions that directly depend on
        i (one hop), avoiding false positives from distant transitive deps.
        """
        insn = insns[i]
        if not insn.a_slot_ok:
            return False
        # If there's already a ready b-slot partner, no need to stall.
        b_candidates = [insns[j] for j in ready_set if insns[j].b_slot_ok and j != i]
        if find_b_partners(insn, b_candidates):
            return False
        for j in graph.edges[i]:
            if (j not in ready_set and not emitted[j]
                    and insns[j].mnemonic in ALL_BRANCH_MN
                    and insns[j].b_slot_ok
                    and find_b_partners(insn, [insns[j]])):
                return True
        return False

    def _pick_best_from_ready() -> int:
        """Pick from ready: fewest pairing partners, then longest crit path.

        With STALL_FOR_PAIR: deprioritise instructions whose only pairable
        partner is a dep-graph successor not yet in the ready queue.
        """
        ready_list = _ready_insns()
        def key(i):
            stall = STALL_FOR_PAIR and _has_unready_pair_successor(i)
            return (stall, _count_pairings_in_ready(insns[i], ready_list), -crit[i])
        ready.sort(key=key)
        return ready.pop(0)

    def _pick_tier1_partner(free_insn: Instruction) -> Optional[int]:
        """Pick best B-slot partner for free_insn (free=A, partner=B)."""
        b_candidates = [insns[i] for i in ready if insns[i].b_slot_ok]
        matched_bs = {id(b): rule for b, rule in find_b_partners(free_insn, b_candidates)}
        pairable = [i for i in ready if id(insns[i]) in matched_bs]
        if not pairable:
            return None
        ready_list = _ready_insns()
        def key(i):
            return (_count_pairings_in_ready(insns[i], ready_list), -crit[i])
        return min(pairable, key=key)

    def _pick_tier2_partner(free_insn: Instruction, free_idx: int) -> Optional[int]:
        """Pick best A-slot partner for free_insn (partner=A, free=B).

        Requires no dep-graph edge from free to cand — placing cand before
        free must not violate the topological ordering.
        """
        pairable = [i for i in ready
                    if insns[i].a_slot_ok
                    and i not in graph.edges[free_idx]
                    and find_b_partners(insns[i], [free_insn])]
        if not pairable:
            return None
        ready_list = _ready_insns()
        def key(i):
            return (_count_pairings_in_ready(insns[i], ready_list), -crit[i])
        return min(pairable, key=key)

    # Initialize ready queue
    for i in range(n):
        if in_degree[i] == 0:
            ready.append(i)
            ready_set.add(i)

    free_idx: Optional[int] = None

    while ready or free is not None:
        if not ready:
            # No more ready instructions — flush free as solo
            result.append(free)
            free = None
            free_idx = None
            continue

        if free is not None:
            partner_idx = _pick_tier1_partner(free)
            if partner_idx is not None:
                # Tier-1: free=A, partner=B
                ready.remove(partner_idx)
                ready_set.discard(partner_idx)
                result.append(free)
                result.append(_emit_idx(partner_idx))
                free = None
                free_idx = None
            else:
                partner_idx = _pick_tier2_partner(free, free_idx)
                if partner_idx is not None:
                    # Tier-2: partner=A, free=B
                    ready.remove(partner_idx)
                    ready_set.discard(partner_idx)
                    result.append(_emit_idx(partner_idx))
                    result.append(free)
                    free = None
                    free_idx = None
                else:
                    # No partner — emit free solo, hold next as free
                    result.append(free)
                    free_idx = _pick_best_from_ready()
                    free = _emit_idx(free_idx)
        else:
            free_idx = _pick_best_from_ready()
            free = _emit_idx(free_idx)

    return result


# ---------------------------------------------------------------------------
# Mode 3: Branch-and-bound
# ---------------------------------------------------------------------------

def _pair_count(ordering: list) -> int:
    """Count pairs in the greedy-advance model for a given ordering."""
    # Snapshot solo_reasons to avoid polluting instructions during BnB exploration
    snapshots = {id(insn): set(insn.solo_reasons) for insn in ordering}
    packets = greedy_pair(ordering)
    count = sum(1 for p in packets if p[0] == 'pair')
    # Restore solo_reasons from snapshot
    for insn in ordering:
        insn.solo_reasons = snapshots[id(insn)]
    return count


def _bound(remaining: int, prev_free: bool) -> int:
    if prev_free:
        return 1 + (remaining - 1) // 2
    else:
        return remaining // 2


def _bnb_single_window(insns: list, graph: DepGraph) -> list:
    """BnB over a single window."""
    n = graph.n
    if n == 0:
        return []
    if n == 1:
        return list(insns)

    # Build predecessor sets for topological ordering
    preds: list = [set() for _ in range(n)]
    for i in range(n):
        for j in graph.edges[i]:
            preds[j].add(i)

    # Get list-schedule result as initial solution
    list_result = _list_schedule(insns, graph)
    best_count = _pair_count(list_result)
    best_order = list(list_result)

    nodes_explored = [0]
    nodes_since_improvement = [0]

    def get_ready(emitted_set: frozenset) -> list:
        return [i for i in range(n) if i not in emitted_set and
                all(p in emitted_set for p in preds[i])]

    def search(emitted: frozenset, order: list, pairs: int,
               free_insn: Optional[Instruction], free_idx: Optional[int]):
        nonlocal best_count, best_order
        nodes_explored[0] += 1
        nodes_since_improvement[0] += 1

        if (nodes_explored[0] > NODE_BUDGET or
                nodes_since_improvement[0] > STAGNATION):
            return

        remaining = n - len(order)
        if remaining == 0:
            count = _pair_count(order)
            if count > best_count:
                best_count = count
                best_order = list(order)
                nodes_since_improvement[0] = 0
            return

        # Prune
        upper = pairs + _bound(remaining, free_insn is not None)
        if upper <= best_count:
            return

        ready = get_ready(emitted)
        if not ready:
            return

        # Order candidates: tier1 (free=A, cand=B), tier2 (cand=A, free=B), tier3 (rest)
        if free_insn is not None:
            t1_bs = {id(insns[i]) for i in ready if insns[i].b_slot_ok}
            t1_matched = {id(b) for b, _ in find_b_partners(free_insn, [insns[i] for i in ready if id(insns[i]) in t1_bs])}
            tier1 = [i for i in ready if id(insns[i]) in t1_matched]
            tier2 = [i for i in ready if i not in tier1
                     and insns[i].a_slot_ok
                     and i not in graph.edges[free_idx]
                     and find_b_partners(insns[i], [free_insn])]
            tier3 = [i for i in ready if i not in tier1 and i not in tier2]
            candidates = tier1 + tier2 + tier3
        else:
            candidates = ready

        for idx in candidates:
            if (nodes_explored[0] > NODE_BUDGET or
                    nodes_since_improvement[0] > STAGNATION):
                return

            insn = insns[idx]
            new_emitted = emitted | {idx}
            new_order = order + [insn]

            if free_insn is not None and (
                (insn.b_slot_ok and find_b_partners(free_insn, [insn]))   # tier-1
                or (insn.a_slot_ok                                         # tier-2
                    and idx not in graph.edges[free_idx]
                    and find_b_partners(insn, [free_insn]))
            ):
                new_pairs = pairs + 1
                new_free = None
                new_free_idx = None
            else:
                new_pairs = pairs
                new_free = insn
                new_free_idx = idx

            search(new_emitted, new_order, new_pairs, new_free, new_free_idx)

    search(frozenset(), [], 0, None, None)
    return best_order


def _bnb_schedule(insns: list, graph: DepGraph) -> list:
    """BnB scheduling, splitting into overlapping windows if block is large.

    WINDOW_OVERLAP instructions from the tail of each committed window are
    carried into the head of the next window so BnB can see cross-boundary
    pairing conflicts.  Only the non-overlap portion of each window's result
    is committed; the overlap instructions are re-scheduled in context.
    """
    if len(insns) <= WINDOW_SIZE:
        return _bnb_single_window(insns, graph)

    overlap = min(WINDOW_OVERLAP, WINDOW_SIZE // 2)
    result = []
    start = 0
    carried: list = []   # instructions re-entering from the previous window tail

    while start < len(insns):
        end = min(start + WINDOW_SIZE, len(insns))
        window = carried + insns[start:end]

        # Build subgraph covering the window (indices into the *original* insns list
        # for non-carried instructions; carried instructions anchor the leading edge).
        orig_offset = start - len(carried)
        window_edges = [set() for _ in range(len(window))]
        orig_index = {id(insn): i for i, insn in enumerate(insns)}
        for wi, insn in enumerate(window):
            oi = orig_index.get(id(insn))
            if oi is None:
                continue
            for dep_oi in graph.edges[oi]:
                dep_insn = insns[dep_oi]
                dep_wi = next((k for k, w in enumerate(window) if id(w) == id(dep_insn)), None)
                if dep_wi is not None:
                    window_edges[wi].add(dep_wi)

        sub_graph = DepGraph(instructions=window, edges=window_edges)
        ordered = _bnb_single_window(window, sub_graph)

        # Commit everything except the last `overlap` instructions (which become
        # the carried prefix for the next window), unless this is the final window.
        if end >= len(insns) or overlap == 0:
            result.extend(ordered)
            break
        else:
            commit = len(ordered) - overlap
            result.extend(ordered[:commit])
            carried = ordered[commit:]

        start = end

    return result

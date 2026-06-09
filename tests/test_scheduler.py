"""
Tests for analysis/scheduler.py — instruction scheduling modes.
"""

import pytest
from isa.instruction import Instruction
from analysis.cfg import BasicBlock
from analysis.depgraph import build_dep_graph
from scheduler.reorder import schedule, ScheduleMode
from scheduler.pairing import greedy_pair
from analysis.liveness import compute_global_liveness, compute_local_liveness


def make_insn(mnemonic, rd=None, rs1=None, rs2=None, imm=None, branch_target=None):
    return Instruction(
        mnemonic=mnemonic, operands=[], raw=mnemonic,
        rd=rd, rs1=rs1, rs2=rs2, imm=imm,
        branch_target=branch_target,
    )


def make_add(rd, rs1, rs2):
    return make_insn("add", rd=rd, rs1=rs1, rs2=rs2)


def make_addi(rd, rs1, imm):
    return make_insn("addi", rd=rd, rs1=rs1, imm=imm)


def make_lw(rd, rs1, imm=0):
    return make_insn("lw", rd=rd, rs1=rs1, imm=imm)


def make_sw(rs1, rs2, imm=0):
    return make_insn("sw", rs1=rs1, rs2=rs2, imm=imm)


def make_ret():
    return make_insn("jalr", rd=0, rs1=1, imm=0)


def make_block(insns, label="b"):
    return BasicBlock(label=label, instructions=insns)


def count_pairs(ordered):
    packets = greedy_pair(ordered)
    return sum(1 for p in packets if p[0] == 'pair')


# ---------------------------------------------------------------------------
# FORWARD mode
# ---------------------------------------------------------------------------

class TestForwardMode:

    def test_forward_preserves_order(self):
        """FORWARD mode must preserve source order."""
        insns = [make_add(10, 11, 12), make_add(13, 14, 15), make_ret()]
        block = make_block(insns)
        result = schedule(block, None, ScheduleMode.FORWARD)
        assert result == insns

    def test_forward_empty_block(self):
        """FORWARD mode with empty block returns empty list."""
        block = make_block([])
        result = schedule(block, None, ScheduleMode.FORWARD)
        assert result == []

    def test_forward_single_instruction(self):
        """FORWARD mode with single instruction."""
        insns = [make_ret()]
        block = make_block(insns)
        result = schedule(block, None, ScheduleMode.FORWARD)
        assert result == insns


# ---------------------------------------------------------------------------
# LIST mode
# ---------------------------------------------------------------------------

class TestListMode:

    def test_list_mode_produces_valid_ordering(self):
        """LIST mode result must be a topological sort of the dep graph."""
        # a0 = a1 + a2; a3 = a0 + a4 (RAW dependency: a3 depends on a0)
        insn0 = make_add(10, 11, 12)   # a0 = a1 + a2
        insn1 = make_add(13, 10, 14)   # a3 = a0 + a4 (depends on insn0)
        block = make_block([insn0, insn1])
        graph = build_dep_graph(block)
        result = schedule(block, graph, ScheduleMode.LIST)
        assert len(result) == 2
        # insn0 must come before insn1 (RAW)
        assert result.index(insn0) < result.index(insn1)

    def test_list_mode_maximises_pairs(self):
        """LIST mode should pair independent instructions together."""
        # Four independent instructions: should produce 2 pairs
        a = make_add(10, 11, 12)
        b = make_add(13, 14, 15)
        c = make_add(16, 17, 18)
        d = make_add(19, 20, 21)
        block = make_block([a, b, c, d])
        graph = build_dep_graph(block)
        result = schedule(block, graph, ScheduleMode.LIST)
        pairs = count_pairs(result)
        assert pairs == 2

    def test_list_mode_respects_raw_dependency(self):
        """LIST mode must respect RAW dependencies."""
        # chain: a = b+c, d = a+e, f = d+g
        a = make_add(10, 11, 12)   # a0=a1+a2
        b = make_add(13, 10, 14)   # a3=a0+a4 (depends on a)
        c = make_add(15, 13, 16)   # a5=a3+a6 (depends on b)
        block = make_block([a, b, c])
        graph = build_dep_graph(block)
        result = schedule(block, graph, ScheduleMode.LIST)
        assert result.index(a) < result.index(b)
        assert result.index(b) < result.index(c)

    def test_list_mode_all_instructions_present(self):
        """LIST mode result must contain all instructions exactly once."""
        insns = [make_add(i, i+1, i+2) for i in range(0, 20, 3)]
        block = make_block(insns)
        graph = build_dep_graph(block)
        result = schedule(block, graph, ScheduleMode.LIST)
        assert len(result) == len(insns)
        assert set(id(i) for i in result) == set(id(i) for i in insns)

    def test_list_mode_empty_block(self):
        """LIST mode with empty block returns empty list."""
        block = make_block([])
        graph = build_dep_graph(block)
        result = schedule(block, graph, ScheduleMode.LIST)
        assert result == []


# ---------------------------------------------------------------------------
# BNB mode
# ---------------------------------------------------------------------------

class TestBNBMode:

    def test_bnb_finds_optimal_or_better_than_forward(self):
        """BNB should achieve at least as many pairs as FORWARD."""
        insns = [
            make_add(10, 11, 12),
            make_add(13, 14, 15),
            make_add(16, 17, 18),
            make_add(19, 20, 21),
        ]
        block = make_block(insns)
        graph = build_dep_graph(block)

        fwd_result = schedule(make_block(list(insns)), None, ScheduleMode.FORWARD)
        bnb_result = schedule(block, graph, ScheduleMode.BNB)

        assert count_pairs(bnb_result) >= count_pairs(fwd_result)

    def test_bnb_valid_topological_ordering(self):
        """BNB result must be a valid topological sort."""
        a = make_add(10, 11, 12)
        b = make_add(13, 10, 14)   # depends on a
        c = make_add(15, 16, 17)   # independent
        block = make_block([a, b, c])
        graph = build_dep_graph(block)
        result = schedule(block, graph, ScheduleMode.BNB)
        # a must come before b
        assert result.index(a) < result.index(b)

    def test_bnb_all_instructions_present(self):
        """BNB result must contain all instructions."""
        insns = [make_add(i, i+1, i+2) for i in range(0, 15, 3)]
        block = make_block(insns)
        graph = build_dep_graph(block)
        result = schedule(block, graph, ScheduleMode.BNB)
        assert set(id(i) for i in result) == set(id(i) for i in insns)

    def test_bnb_small_block_optimal(self):
        """For a small independent block, BNB should achieve maximum pairing."""
        # 4 fully independent instructions → max 2 pairs
        insns = [make_add(i, i+1, i+2) for i in range(0, 12, 3)]
        block = make_block(insns)
        graph = build_dep_graph(block)
        result = schedule(block, graph, ScheduleMode.BNB)
        assert count_pairs(result) == 2


# ---------------------------------------------------------------------------
# Dep graph correctness
# ---------------------------------------------------------------------------

class TestDepGraph:

    def test_raw_edge(self):
        """RAW: b reads a reg written by a."""
        a = make_add(10, 11, 12)  # writes a0
        b = make_add(13, 10, 14)  # reads a0
        block = make_block([a, b])
        graph = build_dep_graph(block)
        assert 1 in graph.edges[0]  # a → b

    def test_war_edge(self):
        """WAR: b writes a reg read by a."""
        a = make_add(10, 11, 12)  # reads a1 (rs1=11)
        b = make_add(11, 13, 14)  # writes a1
        block = make_block([a, b])
        graph = build_dep_graph(block)
        assert 1 in graph.edges[0]  # a → b

    def test_waw_edge(self):
        """WAW: both write same reg."""
        a = make_add(10, 11, 12)
        b = make_add(10, 13, 14)
        block = make_block([a, b])
        graph = build_dep_graph(block)
        assert 1 in graph.edges[0]

    def test_independent_no_edge(self):
        """Two completely independent instructions: no dep edge."""
        a = make_add(10, 11, 12)  # a0 = a1 + a2
        b = make_add(13, 14, 15)  # a3 = a4 + a5
        block = make_block([a, b])
        graph = build_dep_graph(block)
        assert 1 not in graph.edges[0]

    def test_memory_ordering_edge(self):
        """Two stores: second must come after first."""
        a = make_sw(rs1=2, rs2=10, imm=0)
        b = make_sw(rs1=2, rs2=11, imm=4)
        block = make_block([a, b])
        graph = build_dep_graph(block)
        assert 1 in graph.edges[0]


# ---------------------------------------------------------------------------
# Integration: liveness recomputation after reorder
# ---------------------------------------------------------------------------

class TestLivenessRecomputation:

    def test_liveness_recomputed_after_reorder(self):
        """After reordering, per-instruction liveness should be correct."""
        a = make_add(10, 11, 12)
        b = make_add(13, 14, 15)
        ret = make_ret()
        block = make_block([a, b, ret])
        global_result = compute_global_liveness([block])
        compute_local_liveness(block, global_result)

        # Reorder: swap a and b (they're independent)
        block.instructions = [b, a, ret]
        compute_local_liveness(block, global_result)

        # b.live_in should now contain a1(11),a2(12) if a follows b
        # Actually b uses a4=14, a5=15; live_in of b should contain 14, 15
        assert 14 in b.live_in
        assert 15 in b.live_in
        # a.live_in should contain 11, 12
        assert 11 in a.live_in
        assert 12 in a.live_in

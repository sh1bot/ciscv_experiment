"""
Tests for analysis/parser.py — focused on compressed (c.*) mnemonic expansion.

A disassembly emitted with explicit RVC mnemonics must decode to the same
register/immediate fields as the aliased (pseudo-op) disassembly of the same
program, so downstream pairing/RVC stats are alias-invariant.
"""

from analysis.parser import _decode_instruction


def dec(mnemonic, *operands):
    return _decode_instruction(mnemonic, list(operands), mnemonic, None)


class TestCompressedExpansion:
    def test_c_li(self):
        i = dec("c.li", "a4", "5")
        assert i.mnemonic == "addi" and i.rd == 14 and i.rs1 == 0 and i.imm == 5

    def test_c_mv(self):
        i = dec("c.mv", "s0", "a0")
        assert i.mnemonic == "addi" and i.rd == 8 and i.rs1 == 10 and i.imm == 0

    def test_c_add_doubles_dest_as_source(self):
        # c.add s9, s5  ->  add s9, s9, s5
        i = dec("c.add", "s9", "s5")
        assert i.mnemonic == "add" and i.rd == 25 and i.rs1 == 25 and i.rs2 == 21

    def test_c_addi_doubles_dest_as_source(self):
        # c.addi t0, 4  ->  addi t0, t0, 4
        i = dec("c.addi", "t0", "4")
        assert i.mnemonic == "addi" and i.rd == 5 and i.rs1 == 5 and i.imm == 4

    def test_c_sub(self):
        i = dec("c.sub", "a1", "a3")
        assert i.mnemonic == "sub" and i.rd == 11 and i.rs1 == 11 and i.rs2 == 13

    def test_c_slli(self):
        i = dec("c.slli", "a2", "0x8")
        assert i.mnemonic == "slli" and i.rd == 12 and i.rs1 == 12 and i.imm == 8

    def test_c_addi4spn(self):
        # c.addi4spn s0, sp, 16  ->  addi s0, sp, 16
        i = dec("c.addi4spn", "s0", "sp", "16")
        assert i.mnemonic == "addi" and i.rd == 8 and i.rs1 == 2 and i.imm == 16

    def test_c_addi16sp(self):
        # c.addi16sp sp, -128  ->  addi sp, sp, -128
        i = dec("c.addi16sp", "sp", "-128")
        assert i.mnemonic == "addi" and i.rd == 2 and i.rs1 == 2 and i.imm == -128

    def test_c_lwsp(self):
        # c.lwsp ra, 12(sp)  ->  lw ra, 12(sp)
        i = dec("c.lwsp", "ra", "12(sp)")
        assert i.mnemonic == "lw" and i.rd == 1 and i.rs1 == 2 and i.imm == 12

    def test_c_swsp(self):
        # c.swsp ra, 12(sp)  ->  sw ra, 12(sp)
        i = dec("c.swsp", "ra", "12(sp)")
        assert i.mnemonic == "sw" and i.rs2 == 1 and i.rs1 == 2 and i.imm == 12

    def test_c_lw(self):
        i = dec("c.lw", "s1", "0(s1)")
        assert i.mnemonic == "lw" and i.rd == 9 and i.rs1 == 9 and i.imm == 0

    def test_c_jr(self):
        # c.jr ra  ->  jalr x0, ra, 0
        i = dec("c.jr", "ra")
        assert i.mnemonic == "jalr" and i.rd == 0 and i.rs1 == 1 and i.imm == 0

    def test_c_jalr_uses_ra(self):
        # c.jalr a1  ->  jalr ra, a1, 0  (NOT rd=x0)
        i = dec("c.jalr", "a1")
        assert i.mnemonic == "jalr" and i.rd == 1 and i.rs1 == 11 and i.imm == 0

    def test_c_beqz(self):
        i = dec("c.beqz", "a2", ".L1")
        assert i.mnemonic == "beqz" and i.rs1 == 12 and i.rs2 == 0
        assert i.branch_target == ".L1"

    def test_c_j(self):
        i = dec("c.j", "target")
        assert i.mnemonic == "j" and not i.is_unknown

    def test_c_lui(self):
        i = dec("c.lui", "s6", "0x10")
        assert i.mnemonic == "lui" and i.rd == 22 and i.imm == 0x10

    def test_not_unknown(self):
        # None of the compressed forms should be flagged unknown.
        for mn, ops in [("c.li", ["a4", "5"]), ("c.add", ["s9", "s5"]),
                        ("c.swsp", ["ra", "12(sp)"]), ("c.jr", ["ra"]),
                        ("c.beqz", ["a2", ".L"]), ("c.addi4spn", ["s0", "sp", "16"])]:
            assert not dec(mn, *ops).is_unknown, mn

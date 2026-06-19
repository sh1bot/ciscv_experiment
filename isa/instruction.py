"""
isa/instruction.py — Instruction base class and computed properties.
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class Instruction:
    mnemonic:      str
    operands:      list[str]
    raw:           str
    label:         Optional[str] = None

    # Non-instruction lines preceding this instruction
    prefix_lines:  list[str] = field(default_factory=list)
    # Non-instruction lines following this instruction (trailing directives/blanks)
    suffix_lines:  list[str] = field(default_factory=list)

    # Populated by decoder:
    rd:   Optional[int] = None   # destination register index (0–31 int, 32–63 float)
    rs1:  Optional[int] = None
    rs2:  Optional[int] = None
    rs3:  Optional[int] = None   # FMA etc.
    imm:  Optional[int] = None
    branch_target: Optional[str] = None
    is_unknown:    bool = False
    _has_mem_operand: bool = False  # set by parser for unknown insns with (base-reg) operand

    # Populated by liveness pass (indices 0–63):
    live_in:   frozenset = field(default_factory=frozenset)
    live_out:  frozenset = field(default_factory=frozenset)

    # Populated by slot-eligibility pass (see scheduler/pairing.py):
    a_slot_ok: bool = True
    b_slot_ok: bool = True

    # Set by liveness pass: True when this instruction's base register (rs1) was
    # produced by an auipc with no intervening redefinition — i.e. this is the
    # load half of an auipc+load symbol/GOT address materialisation, whose
    # offset is a %pcrel_lo relocation rather than a real displacement.
    base_from_auipc: bool = False

    # Populated by pairing pass:
    solo_reasons: set = field(default_factory=set)

    # -----------------------------------------------------------------------
    # Register use/def sets — all register files, excluding only x0 (index 0).
    # x0 is excluded because writes are no-ops and reads always produce zero.
    # -----------------------------------------------------------------------

    @property
    def uses_regs(self) -> frozenset:
        regs = set()
        for r in (self.rs1, self.rs2, self.rs3):
            if r is not None and r != 0:
                regs.add(r)
        return frozenset(regs)

    @property
    def defs_regs(self) -> frozenset:
        if self.rd is not None and self.rd != 0:
            return frozenset({self.rd})
        return frozenset()

    # -----------------------------------------------------------------------
    # Instruction classification
    # -----------------------------------------------------------------------

    @property
    def is_branch(self) -> bool:
        return self.mnemonic in {
            "beq", "bne", "blt", "bge", "bltu", "bgeu",
            "beqz", "bnez", "bltz", "bgez", "blez", "bgtz",
        }

    @property
    def is_jump(self) -> bool:
        """Unconditional jump (JAL, JALR) — not including calls/returns per slot rules."""
        return self.mnemonic in {"jal", "jalr"}

    @property
    def is_call(self) -> bool:
        """call pseudo-instruction, or JAL/JALR writing ra (x1) or t0 (x5)."""
        if self.mnemonic == "call":
            return True
        if self.mnemonic in ("jal", "jalr") and self.rd in (1, 5):
            return True
        return False

    @property
    def is_tail(self) -> bool:
        """tail pseudo-instruction."""
        return self.mnemonic == "tail"

    @property
    def is_return(self) -> bool:
        """ret pseudo or jalr x0, ra, 0."""
        if self.mnemonic == "ret":
            return True
        if self.mnemonic == "jalr" and self.rd == 0 and self.rs1 == 1 and self.imm == 0:
            return True
        return False

    @property
    def is_csr(self) -> bool:
        return self.mnemonic in {
            "csrrw", "csrrs", "csrrc",
            "csrrwi", "csrrsi", "csrrci",
            "csrr", "csrw", "csrs", "csrc",
            "csrwi", "csrsi", "csrci",
            "rdcycle", "rdtime", "rdinstret",
            "rdcycleh", "rdtimeh", "rdinstreth",
        }

    @property
    def is_fence(self) -> bool:
        return self.mnemonic in {"fence", "fence.i"}

    @property
    def is_atomic(self) -> bool:
        return self.mnemonic.startswith("lr.") or \
               self.mnemonic.startswith("sc.") or \
               self.mnemonic.startswith("amo")

    @property
    def has_side_effects(self) -> bool:
        return (self.is_call or self.is_return or self.writes_memory or
                self.is_csr or self.is_fence or self.is_atomic)

    @property
    def writes_memory(self) -> bool:
        return self.mnemonic in {
            "sb", "sh", "sw", "sd",
            "fsw", "fsd",
            "c.sw", "c.sd", "c.swsp", "c.sdsp",
        }

    @property
    def reads_memory(self) -> bool:
        return self.mnemonic in {
            "lb", "lbu", "lh", "lhu", "lw", "lwu", "ld",
            "flw", "fld",
            "c.lw", "c.ld", "c.lwsp", "c.ldsp",
        }

    @property
    def has_mem_operand(self) -> bool:
        """True if this instruction accesses memory — known loads/stores, or
        unknown instructions where the parser detected a (base-reg) operand."""
        return self.reads_memory or self.writes_memory or self._has_mem_operand

    @property
    def reads_stack(self) -> bool:
        """Load instruction whose base register is sp (x2)."""
        return self.reads_memory and self.rs1 == 2

    @property
    def writes_stack(self) -> bool:
        """Store instruction whose base register is sp (x2)."""
        return self.writes_memory and self.rs1 == 2

    @property
    def access_width(self) -> Optional[int]:
        m = self.mnemonic
        if m in ("lb", "lbu", "sb"): return 1
        if m in ("lh", "lhu", "sh"): return 2
        if m in ("lw", "lwu", "sw", "flw", "fsw", "c.lw", "c.sw", "c.lwsp", "c.swsp"): return 4
        if m in ("ld", "sd", "fld", "fsd", "c.ld", "c.sd", "c.ldsp", "c.sdsp"): return 8
        return None

    @property
    def access_shift(self) -> Optional[int]:
        w = self.access_width
        if w is None:
            return None
        return w.bit_length() - 1

    @property
    def is_rsd(self) -> bool:
        """True when rd is also a source operand."""
        return self.rd is not None and (self.rd == self.rs1 or self.rd == self.rs2)

    @property
    def is_li(self) -> bool:
        """addi rd, x0, imm — load immediate (rs1 == x0)."""
        return self.mnemonic == "addi" and self.rs1 == 0

    @property
    def is_mv(self) -> bool:
        """addi rd, rs1, 0 — register copy (imm == 0)."""
        return self.mnemonic == "addi" and self.imm in (0, None)

    @property
    def is_addi4spn(self) -> bool:
        """addi rd, sp, imm — sp-relative address (rs1==sp, nonzero multiple of 4).
        Not range-constrained; use uimm_fits for encoding checks."""
        return (self.mnemonic == "addi" and self.rs1 == 2
                and self.imm is not None and self.imm != 0 and self.imm % 4 == 0)

    @property
    def is_local(self) -> bool:
        """32- or 64-bit load/store with sp (x2) as base address.
        Not range-constrained; use uimm_fits for encoding checks."""
        return self.rs1 == 2 and self.mnemonic in {"lw", "lwu", "ld", "sw", "sd"}

    @property
    def is_commutative(self) -> bool:
        return self.mnemonic in {
            "add", "addw", "mul", "mulh", "mulhu", "mulhsu",
            "and", "or", "xor",
            "min", "minu", "max", "maxu",
            "fadd.s", "fadd.d", "fmul.s", "fmul.d",
        }

    @property
    def reads_rd(self) -> bool:
        """True if the instruction reads rd before writing it (excludes AMOs)."""
        if self.is_atomic:
            return False
        return self.is_rsd

    @property
    def writes_rd(self) -> bool:
        return self.rd is not None

    # -----------------------------------------------------------------------
    # RVC eligibility helpers
    # -----------------------------------------------------------------------

    @property
    def rs1_in_rvc_range(self) -> bool:
        return self.rs1 is not None and self.rs1 in range(8, 16)

    @property
    def rs2_in_rvc_range(self) -> bool:
        return self.rs2 is not None and self.rs2 in range(8, 16)

    @property
    def rd_in_rvc_range(self) -> bool:
        return self.rd is not None and self.rd in range(8, 16)

    @property
    def rvc_eligible(self) -> bool:
        """True if any RVC encoding can represent this instruction."""
        m = self.mnemonic
        rd, rs1, rs2 = self.rd, self.rs1, self.rs2
        imm = self.imm

        # c.addi4spn rd', nzuimm[9:2] — 8 significant bits scaled by 4
        if m == "addi" and self.rd_in_rvc_range and rs1 == 2 and self.uimm_fits(8, 2, nonzero=True):
            return True
        # c.lw rd', imm(rs1') — uimm[6:2], 5 significant bits scaled by 4
        if m == "lw" and self.rd_in_rvc_range and self.rs1_in_rvc_range and self.uimm_fits(5, 2):
            return True
        # c.sw rs2', imm(rs1') — uimm[6:2], 5 significant bits scaled by 4
        if m == "sw" and self.rs2_in_rvc_range and self.rs1_in_rvc_range and self.uimm_fits(5, 2):
            return True
        # c.ld rd', imm(rs1') — RV64, uimm[7:3], 5 significant bits scaled by 8
        if m == "ld" and self.rd_in_rvc_range and self.rs1_in_rvc_range and self.uimm_fits(5, 3):
            return True
        # c.sd rs2', imm(rs1') — RV64, uimm[7:3], 5 significant bits scaled by 8
        if m == "sd" and self.rs2_in_rvc_range and self.rs1_in_rvc_range and self.uimm_fits(5, 3):
            return True
        # c.addi rd, nzimm
        if m == "addi" and rd == rs1 and rd is not None and rd != 0 and self.imm_fits(6, nonzero=True):
            return True
        # c.addiw rd, imm — RV64 (imm=0 encodes sext.w which is valid)
        if m == "addiw" and rd == rs1 and rd is not None and rd != 0 and self.imm_fits(6):
            return True
        # c.li rd, imm
        if m == "addi" and rs1 == 0 and rd is not None and rd != 0 and self.imm_fits(6):
            return True
        # c.addi16sp — addi x2, x2, nzimm[9:4], 6 significant bits scaled by 16
        if m == "addi" and rd == 2 and rs1 == 2 and self.imm_fits(6, 4, nonzero=True):
            return True
        # c.lui rd, nzimm
        if m == "lui" and rd is not None and rd != 0 and rd != 2 and self.imm_fits(6, nonzero=True):
            return True
        # c.srli rd', shamt
        if m == "srli" and self.rd_in_rvc_range and rd == rs1 and self.uimm_fits(6, nonzero=True):
            return True
        # c.srai rd', shamt
        if m == "srai" and self.rd_in_rvc_range and rd == rs1 and self.uimm_fits(6, nonzero=True):
            return True
        # c.andi rd', imm
        if m == "andi" and self.rd_in_rvc_range and rd == rs1 and self.imm_fits(6):
            return True
        # c.sub rd', rs2'
        if m == "sub" and self.rd_in_rvc_range and self.rs2_in_rvc_range and rd == rs1:
            return True
        # c.xor rd', rs2'
        if m == "xor" and self.rd_in_rvc_range and self.rs2_in_rvc_range and rd == rs1:
            return True
        # c.or rd', rs2'
        if m == "or" and self.rd_in_rvc_range and self.rs2_in_rvc_range and rd == rs1:
            return True
        # c.and rd', rs2'
        if m == "and" and self.rd_in_rvc_range and self.rs2_in_rvc_range and rd == rs1:
            return True
        # c.subw rd', rs2' — RV64
        if m == "subw" and self.rd_in_rvc_range and self.rs2_in_rvc_range and rd == rs1:
            return True
        # c.addw rd', rs2' — RV64
        if m == "addw" and self.rd_in_rvc_range and self.rs2_in_rvc_range and rd == rs1:
            return True
        # c.j offset — jal x0, offset
        if m == "jal" and rd == 0:
            return True
        # c.jal offset — RV32 only: jal x1, offset
        if m == "jal" and rd == 1:
            return True
        # c.beqz rs1', offset
        if m == "beq" and self.rs1_in_rvc_range and rs2 == 0:
            return True
        # c.bnez rs1', offset
        if m == "bne" and self.rs1_in_rvc_range and rs2 == 0:
            return True
        # c.slli rd, shamt
        if m == "slli" and rd is not None and rd != 0 and rd == rs1 and self.uimm_fits(6, nonzero=True):
            return True
        # c.lwsp rd, imm — lw rd, imm(x2), uimm[7:2], 6 significant bits scaled by 4
        if m == "lw" and rd is not None and rd != 0 and rs1 == 2 and self.uimm_fits(6, 2):
            return True
        # c.ldsp rd, imm — RV64: ld rd, imm(x2), uimm[8:3], 6 significant bits scaled by 8
        if m == "ld" and rd is not None and rd != 0 and rs1 == 2 and self.uimm_fits(6, 3):
            return True
        # c.jr rs1 — jalr x0, rs1, 0
        if m == "jalr" and rd == 0 and rs1 is not None and rs1 != 0 and self.imm == 0:
            return True
        # c.mv rd, rs2 — add rd, x0, rs2
        if m == "add" and rs1 == 0 and rd is not None and rd != 0 and rs2 is not None and rs2 != 0:
            return True
        # c.nop — addi x0, x0, 0
        if m == "addi" and rd == 0 and rs1 == 0 and imm == 0:
            return True
        # c.ebreak
        if m == "ebreak":
            return True
        # c.jalr rs1 — jalr x1, rs1, 0
        if m == "jalr" and rd == 1 and rs1 is not None and rs1 != 0 and self.imm == 0:
            return True
        # c.add rd, rs2 — add rd, rd, rs2
        if m == "add" and rd is not None and rd != 0 and rd == rs1 and rs2 is not None and rs2 != 0:
            return True
        # c.swsp rs2, imm — sw rs2, imm(x2), uimm[7:2], 6 significant bits scaled by 4
        if m == "sw" and rs1 == 2 and self.uimm_fits(6, 2):
            return True
        # c.sdsp rs2, imm — RV64: sd rs2, imm(x2), uimm[8:3], 6 significant bits scaled by 8
        if m == "sd" and rs1 == 2 and self.uimm_fits(6, 3):
            return True

        return False

    # -----------------------------------------------------------------------
    # Immediate helpers
    # -----------------------------------------------------------------------

    def imm_multiple(self, shift: int) -> bool:
        """True if self.imm is a non-None multiple of (1 << shift)."""
        if self.imm is None:
            return False
        if shift == 0:
            return True
        return (self.imm & ((1 << shift) - 1)) == 0

    def imm_fits(self, n: int, shift: int = 0, nonzero: bool | str = False) -> bool:
        """Signed range + alignment check.

        nonzero=False   — [-(2**(n-1))<<shift, (2**(n-1)-1)<<shift]
        nonzero=True    — same range, zero excluded (nzimm)
        nonzero='remap' — [-(2**(n-1))<<shift, -(1<<shift)] ∪ [(1<<shift), 2**(n-1)<<shift]
                          Zero maps to 2**(n-1)<<shift, balancing the range.
        """
        if self.imm is None or not self.imm_multiple(shift):
            return False
        v = self.imm >> shift
        half = 1 << (n - 1)
        if nonzero == 'remap':
            return v != 0 and -half <= v <= half
        if not (-half <= v <= half - 1):
            return False
        if nonzero and v == 0:
            return False
        return True

    def uimm_fits(self, n: int, shift: int = 0, nonzero: bool | str = False) -> bool:
        """Unsigned range + alignment check.

        nonzero=False   — [0, (2**n - 1) << shift]
        nonzero=True    — [1<<shift, (2**n-1)<<shift]  (nzuimm)
        nonzero='remap' — [1<<shift, 2**n<<shift]      (zero bit-pattern encodes max+1)
        """
        if self.imm is None or not self.imm_multiple(shift):
            return False
        v = self.imm >> shift
        cap = 1 << n
        if nonzero == 'remap':
            return 0 < v <= cap
        if not (0 <= v < cap):
            return False
        if nonzero and v == 0:
            return False
        return True

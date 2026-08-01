"""
isa/xlen.py — which RISC-V base is this corpus?

The scheduler has never needed to know, because every frame named concrete
mnemonics. It needs to now: an XLEN-width memory op is `lw`/`sw` on RV32 and
`ld`/`sd` on RV64, and a frame that spends ONE opcode on "the natural word"
(as `c.lwsp`/`c.ldsp` do) can only be checked against a known base.

The corpus states it. `objdump` puts the ELF class in the first line and
`util/objdump_to_asm.py` preserves it:

    # musl-rv32:     file format elf32-littleriscv

That is authoritative, so it is tried first. The fallback — RV64-only
mnemonics — is for hand-written input with no header; it is sound in one
direction only (their presence proves RV64, their absence does not prove
RV32), so a headerless file with none of them is reported as RV32 with the
`certain` flag clear, and callers that care can refuse it.
"""
import re

_HEADER = re.compile(r"file format elf(32|64)-littleriscv")
# Mnemonics that exist only on RV64.
_RV64_ONLY = re.compile(r"^\t(ld|sd|lwu|addiw|slliw|srliw|sraiw|addw|subw|"
                        r"sllw|srlw|sraw|mulw|divw|divuw|remw|remuw)\b", re.M)

DEFAULT = 32


def detect_xlen(text):
    """(xlen, certain). `certain` is False only when nothing in the text
    settles it, in which case xlen is DEFAULT."""
    m = _HEADER.search(text)
    if m:
        return int(m.group(1)), True
    if _RV64_ONLY.search(text):
        return 64, True
    return DEFAULT, False


def xlen_bytes(xlen):
    return xlen // 8


def is_xlen_width(insn, xlen):
    """True if this memory op moves exactly one natural word — `lw`/`sw` on
    RV32, `ld`/`sd` on RV64. This is the op an sp-relative frame spends its
    single load/store opcode on."""
    return (insn.has_mem_operand
            and insn.access_width == xlen_bytes(xlen))

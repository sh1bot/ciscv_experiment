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


# The XLEN-switchable op vocabulary, READ FROM encoding.yaml.
#
# A frame that spends ONE opcode on "the natural word" cannot name `lw` or
# `ld`, because which it means depends on the base. `lx`/`sx` are that name,
# and `opsets.xlen_switchable` in the yaml says what each becomes for each
# base. The mapping is encoding data, not scheduler policy, so it is declared
# there and only read here — a hardcoded copy would be one more of the
# re-stated facts that TODO A8 exists to eliminate.
#
# This is the first op name whose meaning is not one-to-one with a mnemonic.
# `rules_conform.PSEUDO_BASE` maps pseudo-ops one-to-one (`li` is always
# `addi`); these map differently per base, so any table like that one must
# consult resolve_xlen_op rather than hold a fixed entry.
import os
from functools import lru_cache


@lru_cache(maxsize=1)
def xlen_ops():
    """{op_name: {xlen: mnemonic}} from encoding.yaml's opsets section."""
    import yaml
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "encoding.yaml")
    spec = yaml.safe_load(open(path))
    return dict((spec.get("opsets") or {}).get("xlen_switchable") or {})


def resolve_xlen_op(name, xlen):
    """The concrete mnemonic an XLEN-switchable op name means for this base,
    or the name unchanged if it is not one."""
    return xlen_ops().get(name, {}).get(xlen, name)


def is_xlen_op(name):
    return name in xlen_ops()

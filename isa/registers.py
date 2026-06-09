"""
isa/registers.py — Register indices, ABI names, and calling-convention sets.

Flat namespace: integer registers 0–31, float registers 32–63,
vector registers 64–95.  Vector registers are recognised so the
heuristic unknown-instruction decoder can skip them when extracting
scalar register dependencies.
"""

# Single flat register namespace
# Integer: zero/ra/sp/... x0–x31 → 0–31
# Float:   ft0–ft11/fs0–fs11/fa0–fa7 f0–f31 → 32–63
REG_ALIASES: dict[str, int] = {
    "zero": 0, "ra": 1, "sp": 2, "gp": 3, "tp": 4,
    "t0": 5, "t1": 6, "t2": 7,
    "s0": 8, "fp": 8, "s1": 9,
    "a0": 10, "a1": 11, "a2": 12, "a3": 13,
    "a4": 14, "a5": 15, "a6": 16, "a7": 17,
    "s2": 18, "s3": 19, "s4": 20, "s5": 21,
    "s6": 22, "s7": 23, "s8": 24, "s9": 25,
    "s10": 26, "s11": 27,
    "t3": 28, "t4": 29, "t5": 30, "t6": 31,
    # Float ABI names → 32–63
    "ft0": 32, "ft1": 33, "ft2": 34, "ft3": 35, "ft4": 36, "ft5": 37,
    "ft6": 38, "ft7": 39,
    "fs0": 40, "fs1": 41,
    "fa0": 42, "fa1": 43, "fa2": 44, "fa3": 45,
    "fa4": 46, "fa5": 47, "fa6": 48, "fa7": 49,
    "fs2": 50, "fs3": 51, "fs4": 52, "fs5": 53,
    "fs6": 54, "fs7": 55, "fs8": 56, "fs9": 57,
    "fs10": 58, "fs11": 59,
    "ft8": 60, "ft9": 61, "ft10": 62, "ft11": 63,
}
# Add x0–x31 aliases
for _i in range(32):
    REG_ALIASES[f"x{_i}"] = _i
# Add f0–f31 aliases (offset by 32)
for _i in range(32):
    REG_ALIASES[f"f{_i}"] = _i + 32
# Add v0–v31 aliases (offset by 64) — vector register file
VECTOR_REG_BASE = 64
for _i in range(32):
    REG_ALIASES[f"v{_i}"] = _i + VECTOR_REG_BASE

# Calling-convention register sets (unified int+float)
# Integer caller-saved: ra, t0–t6, a0–a7
# Float caller-saved:   ft0–ft11, fa0–fa7
CALLER_SAVED: frozenset[int] = frozenset({
    1,   # ra
    5, 6, 7,   # t0, t1, t2
    10, 11, 12, 13, 14, 15, 16, 17,  # a0–a7
    28, 29, 30, 31,  # t3–t6
    # float caller-saved: ft0–ft7 (32–39), fa0–fa7 (42–49), ft8–ft11 (60–63)
    32, 33, 34, 35, 36, 37, 38, 39,   # ft0–ft7
    42, 43, 44, 45, 46, 47, 48, 49,   # fa0–fa7
    60, 61, 62, 63,                    # ft8–ft11
})

# Integer callee-saved: sp, s0–s11
# Float callee-saved:   fs0–fs11
CALLEE_SAVED: frozenset[int] = frozenset({
    2,   # sp
    8, 9,  # s0, s1
    18, 19, 20, 21, 22, 23, 24, 25, 26, 27,  # s2–s11
    # float callee-saved: fs0–fs1 (40–41), fs2–fs11 (50–59)
    40, 41,          # fs0, fs1
    50, 51, 52, 53, 54, 55, 56, 57, 58, 59,  # fs2–fs11
})

# ARG_REGS: a0–a7 (10–17) and fa0–fa7 (42–49)
ARG_REGS: frozenset[int] = frozenset({
    10, 11, 12, 13, 14, 15, 16, 17,   # a0–a7
    42, 43, 44, 45, 46, 47, 48, 49,   # fa0–fa7
})

# RET_REGS: a0, a1 (10–11) and fa0, fa1 (42–43)
RET_REGS: frozenset[int] = frozenset({10, 11, 42, 43})

_INT_REG_NAMES = [
    "zero", "ra", "sp", "gp", "tp", "t0", "t1", "t2",
    "s0", "s1", "a0", "a1", "a2", "a3", "a4", "a5",
    "a6", "a7", "s2", "s3", "s4", "s5", "s6", "s7",
    "s8", "s9", "s10", "s11", "t3", "t4", "t5", "t6",
]

_FLOAT_REG_NAMES = [
    "ft0", "ft1", "ft2", "ft3", "ft4", "ft5", "ft6", "ft7",
    "fs0", "fs1", "fa0", "fa1", "fa2", "fa3", "fa4", "fa5",
    "fa6", "fa7", "fs2", "fs3", "fs4", "fs5", "fs6", "fs7",
    "fs8", "fs9", "fs10", "fs11", "ft8", "ft9", "ft10", "ft11",
]


def reg_name(index: int) -> str:
    """Return the canonical ABI name for a register index (0–95)."""
    if index < 32:
        return _INT_REG_NAMES[index]
    if index < 64:
        return _FLOAT_REG_NAMES[index - 32]
    return f"v{index - 64}"


def is_vector_reg(index: int) -> bool:
    return index >= VECTOR_REG_BASE

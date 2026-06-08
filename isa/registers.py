"""
isa/registers.py — Register indices, ABI names, and calling-convention sets.
"""

# Integer register ABI names → index 0–31
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
}
# Add x0–x31 aliases
for _i in range(32):
    REG_ALIASES[f"x{_i}"] = _i

# Float register ABI names → index 0–31
FREG_ALIASES: dict[str, int] = {
    "ft0": 0, "ft1": 1, "ft2": 2, "ft3": 3, "ft4": 4, "ft5": 5,
    "ft6": 6, "ft7": 7,
    "fs0": 8, "fs1": 9,
    "fa0": 10, "fa1": 11, "fa2": 12, "fa3": 13,
    "fa4": 14, "fa5": 15, "fa6": 16, "fa7": 17,
    "fs2": 18, "fs3": 19, "fs4": 20, "fs5": 21,
    "fs6": 22, "fs7": 23, "fs8": 24, "fs9": 25,
    "fs10": 26, "fs11": 27,
    "ft8": 28, "ft9": 29, "ft10": 30, "ft11": 31,
}
# Add f0–f31 aliases
for _i in range(32):
    FREG_ALIASES[f"f{_i}"] = _i

# Calling-convention register sets (integer)
CALLER_SAVED: frozenset[int] = frozenset({
    1,   # ra
    5, 6, 7,   # t0, t1, t2
    10, 11, 12, 13, 14, 15, 16, 17,  # a0–a7
    28, 29, 30, 31,  # t3–t6
})

CALLEE_SAVED: frozenset[int] = frozenset({
    2,   # sp
    8, 9,  # s0, s1
    18, 19, 20, 21, 22, 23, 24, 25, 26, 27,  # s2–s11
})

ARG_REGS: frozenset[int] = frozenset({10, 11, 12, 13, 14, 15, 16, 17})  # a0–a7
RET_REGS: frozenset[int] = frozenset({10, 11})  # a0, a1

# Float calling-convention register sets
FCALLER_SAVED: frozenset[int] = frozenset({
    0, 1, 2, 3, 4, 5, 6, 7,   # ft0–ft7
    10, 11, 12, 13, 14, 15, 16, 17,  # fa0–fa7
    28, 29, 30, 31,  # ft8–ft11
})

FCALLEE_SAVED: frozenset[int] = frozenset({
    8, 9,  # fs0, fs1
    18, 19, 20, 21, 22, 23, 24, 25, 26, 27,  # fs2–fs11
})

FARG_REGS: frozenset[int] = frozenset({10, 11, 12, 13, 14, 15, 16, 17})  # fa0–fa7
FRET_REGS: frozenset[int] = frozenset({10, 11})  # fa0, fa1

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
    """Return the canonical ABI name for an integer register index."""
    return _INT_REG_NAMES[index]


def freg_name(index: int) -> str:
    """Return the canonical ABI name for a float register index."""
    return _FLOAT_REG_NAMES[index]

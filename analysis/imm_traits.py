"""analysis/imm_traits.py — per-op immediate traits inferred from the opcode
(its subform): signedness, and whether a zero immediate is encodable.

Single source of truth for "immediate semantics from the opcode class",
replacing the scattered IMM_SIGNED set and the informal "arith can't be zero"
rule.

  * signedness stays per-opcode: arithmetic / compare / li are signed; shift
    amounts and memory offsets are unsigned.
  * zero: an arithmetic op with a source register is a no-op at imm 0
    (`addi rd,rs,0` == `mv`, `slli …,0` == `mv`), so it never encodes zero and
    its field may RECLAIM the zero codepoint to reach one more magnitude.
    `li` (which sets a constant, including 0) and memory offsets keep zero.

(subform names come from analysis.encoding_budget.subform: addi splits into
li / mv / addi4spn / addi_rsd / addi_other; other ops are their mnemonic.)
"""

SIGNED = frozenset({"addi", "addiw", "andi", "addi_rsd", "addi_other",
                    "li", "slti", "sltiu"})
_SHIFT = frozenset({"slli", "srli", "srai", "slliw", "srliw", "sraiw"})
# subforms whose imm==0 is degenerate (canonicalises to mv/nop), so zero is not
# encoded — an arithmetic op stepping a register, or a shift.
NO_ZERO = frozenset({"addi_rsd", "addi_other", "addiw", "andi", "addi4spn"}) | _SHIFT


def signed(subform):
    return subform in SIGNED


def zero_ok(subform):
    """False when the op cannot carry a zero immediate (an arithmetic no-op),
    which lets its field reclaim the zero codepoint for one more magnitude."""
    return subform not in NO_ZERO


_signed_default = signed          # the parameter below shadows the function


def required_bits(v, subform, signed=None):
    """Significant bits to encode immediate value `v` (already scaled) for this
    op, honouring signedness and the reclaimed zero codepoint. v must be != 0.

    `signed` overrides the per-op default when encoding.yaml declares one on the
    op entry -- addi4spn's field is structurally unsigned (storage below sp is
    not safe to use), which no per-opcode default can know."""
    u = abs(v)
    nz = not zero_ok(subform)
    is_signed = _signed_default(subform) if signed is None else signed
    if is_signed:
        # signed field of n bits holds 2^n values; excluding zero shifts the
        # positive edge from 2^(n-1)-1 up to 2^(n-1).
        return (u - 1).bit_length() + 1 if nz else _signed_bits(v)
    return max(1, (u - 1).bit_length()) if nz else max(1, u.bit_length())


def _signed_bits(v):
    n = 1
    while not (-(1 << (n - 1)) <= v <= (1 << (n - 1)) - 1):
        n += 1
    return n

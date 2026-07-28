"""analysis/imm_expr.py — parse the linear immediate expressions written in the
frame templates.

A template line carries at most one immediate as a linear expression over a
single variable and the access width k, e.g.  k*imm,  k*imm+k,  -16*imm,
16*imm-k,  -k*imma,  4*immb,  imma.  We parse it into  value = m*var + b  where
m and b each carry an integer part plus a multiple of k: coeff = (int, k_part)
meaning  int + k_part*k.  This is the single source of truth for how an
immediate is scaled and how the two immediates of a pair relate.
"""
import re

# [sign] [coeff *] var [ +|- term ],  coeff/term ∈ int | k.
_IMM_RE = re.compile(
    r"([+-])?\s*(?:(\d+|k)\s*\*\s*)?(imma|immb|imm)\b\s*(?:([+-])\s*(\d+|k))?")


def _coeff(sign, tok, default_int):
    """A coefficient token → (int_part, k_part): value = int_part + k_part*k."""
    s = -1 if sign == "-" else 1
    if tok is None:
        return (s * default_int, 0)
    if tok == "k":
        return (0, s)
    return (s * int(tok), 0)


def parse_expr(text):
    """First immediate in `text` as (var, m, b) with value = m*var + b, or None."""
    mo = _IMM_RE.search(text)
    if not mo:
        return None
    sign, coeff, var, op, term = mo.groups()
    m = _coeff(sign, coeff, 1)                 # default coeff is 1 (bare var)
    b = _coeff(op, term, 0) if op else (0, 0)  # default constant is 0
    return var, m, b


def ev(c, k):
    """Evaluate a coefficient (int, k_part) at a concrete width k."""
    return c[0] + c[1] * k


def scale_of(m):
    """The immediate's scale from its coefficient m: 'k' if width-scaled, else
    the positive integer multiplier (1 for a bare variable)."""
    return "k" if m[1] else abs(m[0])


def coeff_str(c):
    i, kc = c
    if kc == 0:
        return str(i)
    kpart = "k" if kc == 1 else ("-k" if kc == -1 else f"{kc}k")
    return kpart if i == 0 else f"{kpart}{i:+d}"


def expr_str(m, b, var):
    s = f"{coeff_str(m)}*{var}" if m != (1, 0) else var
    return s if b == (0, 0) else f"{s} {'+' if (b[0] or b[1]) >= 0 else '-'} " \
                                f"{coeff_str((abs(b[0]), abs(b[1])))}"

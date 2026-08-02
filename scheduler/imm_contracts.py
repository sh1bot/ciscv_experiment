"""
scheduler/imm_contracts.py — immediate widths, derived from encoding.yaml.

`rules.py` used to carry ten named width constants and four inline ranges, each
a hand-copy of a fact the yaml already states. Four of them had drifted:
`chain-li-branch` accepted 8 bits against a declared 6, `arith-mem-pair` 7
against 5, its B offset had a field that did not exist, and `pre-inc-pair`
checked no width at all. Every one was invisible until something else went
looking.

This module derives the same facts from `encoding.yaml` at import, so the
number in the rule and the number in the frame cannot disagree — there is only
one number. Derived rather than generated on purpose: a generated file can go
stale between edit and regeneration, and staleness is the failure mode we are
trying to remove.

    from scheduler.imm_contracts import width_of
    bits = width_of("chain-li-branch", "a", "li")     # -> 6, or None

`width_of` returns the DECLARED op width, which is what an op may actually
carry: the drawn field plus whatever opcode repetition the op's `imm: {bits}`
buys. An op with no declared contract falls back to the row's drawn field,
which is the honest reading — a bare op cannot extend anything (see
`lint_frame`, which rejects bare ops on a widened field).

NOT derived here: which slot a rule reads, order sensitivity, scaling by access
width, or the sentinel forms. Those are scheduler semantics and stay in
`rules.py`; this module owns widths only.
"""
import os
from functools import lru_cache

import yaml

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_YAML = os.path.join(_ROOT, "encoding.yaml")


@lru_cache(maxsize=1)
def _contracts():
    """{rule_name: {slot: {mnemonic: bits}}} for every frame in the yaml."""
    import sys
    sys.path.insert(0, os.path.join(_ROOT, "util"))
    from encoding_render import op_name, op_imm, imm_field_bits

    spec = yaml.safe_load(open(_YAML))
    grid = spec["grid"]
    out = {}
    for node in spec["doc"]:
        frame = node.get("frame")
        if not frame or not frame.get("ops"):
            continue
        names = (frame.get("rules_py_names")
                 or [x.strip() for x in frame["name"].split(",")])
        per_slot = {}
        for slot in ("a", "b"):
            try:
                base = imm_field_bits(frame, grid, slot)
            except Exception:
                base = 0
            widths = {}
            for cluster in frame["ops"]:
                for entry in cluster.get(slot, []):
                    c = op_imm(entry)
                    bits = c.get("bits") if c else None
                    widths[op_name(entry)] = bits or (base or None)
            per_slot[slot] = widths
        for rn in names:
            out[rn] = per_slot
    return out


def width_of(rule, slot, mnemonic):
    """Declared immediate width in bits, or None if the frame gives this op no
    immediate. `mnemonic` is the yaml op name (`li`, not `addi`)."""
    return _contracts().get(rule, {}).get(slot, {}).get(mnemonic)


def widths_for(rule, slot):
    """{mnemonic: bits} for one slot of one rule."""
    return dict(_contracts().get(rule, {}).get(slot, {}))


def signed_range(bits, signed=True):
    """The inclusive range a field of `bits` holds."""
    if bits is None:
        return None
    if signed:
        return -(1 << (bits - 1)), (1 << (bits - 1)) - 1
    return 0, (1 << bits) - 1

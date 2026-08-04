"""
scheduler/imm_contracts.py — immediate widths, derived from encoding.yaml.

`rules.py` used to carry ten named width constants and four inline ranges, each
a hand-copy of a fact the yaml already states. Four of them had drifted:
`li-branch-chain` accepted 8 bits against a declared 6, `arith-mem-pair` 7
against 5, its B offset had a field that did not exist, and `pre-inc-pair`
checked no width at all. Every one was invisible until something else went
looking.

This module derives the same facts from `encoding.yaml` at import, so the
number in the rule and the number in the frame cannot disagree — there is only
one number. Derived rather than generated on purpose: a generated file can go
stale between edit and regeneration, and staleness is the failure mode we are
trying to remove.

    from scheduler.imm_contracts import width_of
    bits = width_of("li-branch-chain", "a", "li")     # -> 6, or None

`width_of` returns the DECLARED op width, which is what an op may actually
carry: the drawn field plus whatever opcode repetition the op's `imm: {bits}`
buys. An op with no declared contract falls back to the row's drawn field,
which is the honest reading — a bare op cannot extend anything (see
`lint_frame`, which rejects bare ops on a widened field).

NOT derived here: which slot a rule reads, order sensitivity, or the sentinel
forms.  (`scale_of` does report a declared scale, which a width census needs to
avoid reporting a scaled field as starved.) Those are scheduler semantics and stay in
`rules.py`; this module owns widths only.
"""
import os
from functools import lru_cache

import yaml

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_YAML = os.path.join(_ROOT, "encoding.yaml")


@lru_cache(maxsize=1)
def _contracts():
    """{rule_name: {slot: {mnemonic: (bits, scale)}}} for every frame."""
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
                    scale = (c.get("scale") or 1) if c else 1
                    widths[op_name(entry)] = (bits or (base or None), scale)
            per_slot[slot] = widths
        for rn in names:
            out[rn] = per_slot
    return out


@lru_cache(maxsize=1)
def _rd_column():
    """{rule_name: (slots,)} — which slots' DESTINATION register a frame draws
    in the `rd` column, and so must keep clear of the x0/x2 sentinel (A1.11).

    The sentinel is what lets `prologue`/`epilogue`/`arith-jump` be selected
    by a bit pattern in that column instead of by an opcode of their own, so
    every frame that puts a real register there owes the reservation.  Frames
    whose rd column carries an immediate or the literal sentinel owe nothing:
    they appear here with no slots."""
    spec = yaml.safe_load(open(_YAML))
    grid = spec["grid"]
    rdcol = grid["columns"].index("rd")
    out = {}
    for node in spec["doc"]:
        frame = node.get("frame") if isinstance(node, dict) else None
        if not frame:
            continue
        names = (frame.get("rules_py_names")
                 or [x.strip() for x in frame["name"].split(",")])
        slots = set()
        for row in frame.get("rows") or []:
            cells = row["c"] if isinstance(row, dict) else row
            pos = 0
            for cell in cells:
                body, _, n = cell.rpartition("*")
                span = int(n) if n.isdigit() else 1
                if span == 1:
                    body = cell
                if pos <= rdcol < pos + span:
                    stem = body.split("[")[0]
                    # rda/rsda -> slot a, rdb/rsdb -> slot b.  Only DESTINATION
                    # operands matter: a source in this column is read, not
                    # written, and cannot collide with the sentinel's meaning.
                    if stem.startswith(("rd", "rsd")) and stem[-1] in "ab":
                        slots.add(stem[-1])
                    break
                pos += span
        for rn in names:
            out[rn] = tuple(sorted(slots))
    return out


def rd_column_slots(rule):
    """Slots whose destination register this frame encodes in the rd column."""
    return _rd_column().get(rule, ())


def width_of(rule, slot, mnemonic):
    """Declared immediate width in bits, or None if the frame gives this op no
    immediate. `mnemonic` is the yaml op name (`li`, not `addi`)."""
    e = _contracts().get(rule, {}).get(slot, {}).get(mnemonic)
    return e[0] if e else None


def scale_of(rule, slot, mnemonic):
    """The multiplier the field carries (4 for addi4spn, the access width for a
    scaled memory offset), or 1. A census that scores a scaled field unscaled
    reports a starved frame that is not starved."""
    e = _contracts().get(rule, {}).get(slot, {}).get(mnemonic)
    return e[1] if e else 1


def widths_for(rule, slot):
    """{mnemonic: bits} for one slot of one rule."""
    return {m: e[0] for m, e in _contracts().get(rule, {}).get(slot, {}).items()}


def signed_range(bits, signed=True):
    """The inclusive range a field of `bits` holds."""
    if bits is None:
        return None
    if signed:
        return -(1 << (bits - 1)), (1 << (bits - 1)) - 1
    return 0, (1 << bits) - 1

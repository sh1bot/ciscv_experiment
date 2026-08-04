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

Widths are the bulk of it, but the principle is the module's real subject: a
fact the yaml states is read from the yaml. `rd_column_slots` and
`link_regs_for` follow the same rule for the two register facts a frame's rows
and op-select declare.

NOT derived here: which slot a rule reads, order sensitivity, scaling by access
width, or the sentinel forms. Those are scheduler semantics and stay in
`rules.py`.
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


# An `encode` value that names another operand rather than a fixed register:
# `addi_rsd` is `{rd: rs1}`, meaning "rd is whatever rs1 is", which constrains
# the two to be equal and pins neither.  Those carry no register number.
_OPERAND_ALIASES = frozenset({"rd", "rs1", "rs2"})


def _reg_number(name):
    """`x6` -> 6, or None for an operand alias.  encoding.yaml spells fixed
    registers in xN form throughout; anything else is a typo, not a default."""
    if isinstance(name, int):
        return name
    s = str(name)
    if s in _OPERAND_ALIASES:
        return None
    if s.startswith("x") and s[1:].isdigit():
        return int(s[1:])
    raise ValueError(f"encoding.yaml: unrecognised register spelling {name!r}")


@lru_cache(maxsize=1)
def _encoded_rd():
    """{rule: {slot: (rd, ...)}} — destination registers a slot's ops HARD-CODE.

    A frame that draws no rd field can still choose between spellings by
    op-select: `load-call-chain` picks its link register that way, offering
    `jalr_link_ra` (a call) and `jalr_link_t1` (a linking jump, the PLT stub's
    spelling) and no others.  The permitted set is stated once, as each
    pseudo-op's `encode.rd`, and read here so a rule cannot admit a register
    the encoding has no codepoint for.  Slots whose ops name no destination
    appear with an empty tuple, which is the ordinary case."""
    import sys
    sys.path.insert(0, os.path.join(_ROOT, "util"))
    from encoding_render import op_name

    spec = yaml.safe_load(open(_YAML))
    pseudo = spec.get("pseudo_ops") or {}
    out = {}
    for node in spec["doc"]:
        frame = node.get("frame") if isinstance(node, dict) else None
        if not frame or not frame.get("ops"):
            continue
        names = (frame.get("rules_py_names")
                 or [x.strip() for x in frame["name"].split(",")])
        per_slot = {}
        for slot in ("a", "b"):
            regs = set()
            for cluster in frame["ops"]:
                for entry in cluster.get(slot, []):
                    enc = (pseudo.get(op_name(entry)) or {}).get("encode") or {}
                    if enc.get("rd") is not None:
                        n = _reg_number(enc["rd"])
                        if n is not None:
                            regs.add(n)
            per_slot[slot] = tuple(sorted(regs))
        for rn in names:
            out[rn] = per_slot
    return out


@lru_cache(maxsize=1)
def _pcrel_lo_frames():
    """Rules whose frame declares `accepts_pcrel_lo` (ACCOUNTING.md sec 8).

    An auipc-fed load's offset is a relocation, not a displacement the program
    chose, so rules refuse it by default.  A frame whose field spans the whole
    pcrel-lo range can admit it instead: the value is whatever the link step
    computes for the packed layout, and any such value fits.  The frame says
    so once, with its reasoning; this reads it."""
    spec = yaml.safe_load(open(_YAML))
    out = set()
    for node in spec["doc"]:
        frame = node.get("frame") if isinstance(node, dict) else None
        if not frame or not frame.get("accepts_pcrel_lo"):
            continue
        out.update(frame.get("rules_py_names")
                   or [x.strip() for x in frame["name"].split(",")])
    return frozenset(out)


def accepts_pcrel_lo(rule):
    """True when this frame admits a load whose base came from an `auipc`."""
    return rule in _pcrel_lo_frames()


def link_regs_for(rule, slot):
    """The rd values this frame's `slot` ops hard-code, as register numbers.

    Empty when the slot's ops name no destination.  `rules.py` uses it to
    admit exactly the transfer spellings the frame has codepoints for."""
    return _encoded_rd().get(rule, {}).get(slot, ())


def rd_column_slots(rule):
    """Slots whose destination register this frame encodes in the rd column."""
    return _rd_column().get(rule, ())


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

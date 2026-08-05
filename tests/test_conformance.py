"""
Gate the yaml against the code on every commit.

Everything here was previously a tool you had to remember to run. Each of this
session's encoding glitches survived because nothing failed when the yaml and
`rules.py` disagreed — the checks existed and were not wired to anything.
"""
import os
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _run(script, *args):
    return subprocess.run([sys.executable, os.path.join(ROOT, script), *args],
                          capture_output=True, text=True)


def test_rules_conform_to_encoding_yaml():
    """No frame may disagree with encoding.yaml on op sets, immediate widths or
    memory base-register classes."""
    r = _run("util/rules_conform.py")
    assert r.returncode == 0, r.stdout + r.stderr


def test_codepoint_accounting_has_no_complaints():
    """No frame may take immediate range it has not paid for: no immediate
    field parked in the g/h opcode bits, no unrecognised field name.  A field
    is its register columns; wider ranges are bought by opcode duplication and
    nothing else."""
    r = _run("util/encoding_assign.py", "--text")
    out = r.stdout + r.stderr
    assert "Codepoint-accounting complaints" not in out, out[:4000]


def test_codepoint_budget_fits():
    """Reserved blocks must fit the 1024-codepoint namespace."""
    r = _run("util/encoding_assign.py", "--text")
    out = r.stdout + r.stderr
    assert "OVERFLOW" not in out.upper() or "FIT" in out, out[:2000]
    assert "exceeds their block" not in out, out[:2000]


def test_op_tables_fit_and_decode():
    """Every op-select bit must resolve: each frame's opcode tables, rounded up
    to power-of-two index fields, fit the block its budget reserves, and every
    assigned codepoint decodes back to exactly one frame and one op pair
    (util/encoding_assign.py --check-tables)."""
    r = _run("util/encoding_assign.py", "--check-tables")
    assert r.returncode == 0, r.stdout + r.stderr


def test_proto_yaml_emits():
    """The default output is the ciscv-proto.yml data file: valid yaml, one
    entry per frame, each carrying its layout and its opcode tables."""
    import yaml

    r = _run("util/encoding_assign.py")
    assert r.returncode == 0, r.stderr
    data = yaml.safe_load(r.stdout)
    assert data["selector"]["bits"] == 10
    frames = data["frames"]
    assert len(frames) > 20
    for f in frames:
        assert f["layout"].strip(), f["name"]
        # the layout's op-select bits and the tables' index must agree in width
        o_bits = f["select"].count("o")
        assert f["block"] == 1 << o_bits, f["name"]
        used = sum(c["n"] for c in f["opcodes"])
        assert used <= f["block"], f["name"]
        for c in f["opcodes"]:
            assert len(c["select"]) == o_bits, (f["name"], c["select"])
            variable = sum(c["select"].count(ch) for ch in "abip")
            assert c["n"] == 1 << variable, f["name"]
            if "pairs" in c:            # a diagonal: one entry per combination
                assert sum(p["n"] for p in c["pairs"]) <= c["n"], f["name"]
            else:
                for slot in ("a", "b"):
                    assert sum(e["n"] for e in c[slot]) <= 1 << c["select"].count(slot)


def test_immediate_contracts_derive():
    """The yaml-derived width table must load and cover every rule."""
    sys.path.insert(0, ROOT)
    from scheduler.imm_contracts import _contracts, width_of
    from scheduler.rules import RULES
    table = _contracts()
    missing = [r.name for r in RULES if r.name not in table]
    assert not missing, f"rules with no frame contract: {missing}"
    # Spot values this session got wrong in both directions at some point.
    assert width_of("li-branch-chain", "a", "li") == 8
    assert width_of("dual-setup-pair", "b", "li") == 6
    assert width_of("mem-base-pair", "a", "lw") == 6


def test_yaml_schema_valid():
    """encoding.yaml must be structurally valid: grid arithmetic, row spans,
    resolvable field names, unique frame names, power-of-two budgets,
    well-formed immediate contracts (util/encoding_schema.py, TODO A2)."""
    r = _run("util/encoding_schema.py")
    assert r.returncode == 0, r.stdout + r.stderr


def test_encoding_md_regenerated():
    """encoding.md is generated from the yaml and must not drift: a re-render
    must match the checked-in file byte-for-byte (TODO A2).  On failure:
    python3 util/encoding_render.py -o encoding.md"""
    r = _run("util/encoding_render.py", "--check")
    assert r.returncode == 0, (r.stdout[-2000:] if r.stdout else r.stderr)


def test_width_naming_frames_match_their_declared_widths():
    """A frame whose NAME states its immediate widths must actually have them.

    `load0-load10-chain` and `load5-load5-chain` put a measured tuning decision
    -- the 5+5 split, chosen because it topped the corpus total in
    `util/chain_width_sweep.py` -- into the identifier.  That is good
    documentation and a live hazard: re-sweeping on a different corpus could
    prefer 4+6, and nothing else in the tree would notice the name had started
    lying.  This makes the name part of the contract the yaml is gated on.

    Naming a frame `load<A>-load<B>-chain` is therefore an opt-in: do it and
    the widths are pinned to the name, or pick a name without numbers.
    """
    import re
    import yaml as _yaml
    sys.path.insert(0, os.path.join(ROOT, "util"))
    from encoding_render import imm_field_bits

    spec = _yaml.safe_load(open(os.path.join(ROOT, "encoding.yaml")))
    grid = spec["grid"]
    checked = 0
    for node in spec["doc"]:
        frame = node.get("frame")
        if not frame or not frame.get("ops"):
            continue
        m = re.fullmatch(r"load(\d+)-load(\d+)-chain", frame["name"])
        if not m:
            continue
        checked += 1
        want_a, want_b = int(m.group(1)), int(m.group(2))
        # A width of 0 means the slot draws no immediate field at all.
        got_a = imm_field_bits(frame, grid, "a")
        got_b = imm_field_bits(frame, grid, "b")
        assert (got_a, got_b) == (want_a, want_b), (
            f"{frame['name']} draws imma={got_a} immb={got_b}, but its name "
            f"claims {want_a} and {want_b}. Rename the frame or fix the rows.")
    assert checked >= 2, f"expected the two chain frames, found {checked}"


def test_every_register_operand_gets_a_full_five_bit_field():
    """No frame draws a register in a field narrower than five bits.

    Every register operand encodes as a full 5-bit field, x0..x31, so `rules.py`
    carries no register-class check at all and none of its frames can overclaim
    by accepting a register its row cannot hold.  That is a property of the
    LAYOUT, not of the rules, and nothing else asserts it: draw a register into
    a narrower field and every pairing rule would silently start accepting
    pairs the encoding cannot express.  Hence this gate.
    """
    import yaml as _yaml
    spec = _yaml.safe_load(open(os.path.join(ROOT, "encoding.yaml")))
    fields = spec["grid"]["fields"]
    width = {name: abs(f["bits"][0] - f["bits"][1]) + 1
             for name, f in fields.items()}
    bad = []
    for node in spec["doc"]:
        frame = node.get("frame")
        if not frame:
            continue
        for row in frame.get("rows") or []:
            if not isinstance(row, dict):
                continue
            for col, val in row.items():
                v = str(val)
                if v.startswith("imm") or v == "unused" or "[" in v:
                    continue          # immediate, sentinel, or an explicit split
                if width.get(col, 5) < 5:
                    bad.append(f"{frame['name']}: {v} in {col} "
                               f"({width[col]} bits)")
    assert not bad, ("register operands in narrow fields — the register-class "
                     "clamp is no longer dead code:\n  " + "\n  ".join(bad))

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
    assert width_of("indep-pair", "b", "li") == 6
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

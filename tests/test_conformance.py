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
    r = _run("util/encoding_assign.py")
    out = r.stdout + r.stderr
    assert "Codepoint-accounting complaints" not in out, out[:4000]


def test_codepoint_budget_fits():
    """Reserved blocks must fit the 1024-codepoint namespace."""
    r = _run("util/encoding_assign.py")
    out = r.stdout + r.stderr
    assert "OVERFLOW" not in out.upper() or "FIT" in out, out[:2000]
    assert "exceeds their block" not in out, out[:2000]


def test_immediate_contracts_derive():
    """The yaml-derived width table must load and cover every rule."""
    sys.path.insert(0, ROOT)
    from scheduler.imm_contracts import _contracts, width_of
    from scheduler.rules import RULES
    table = _contracts()
    missing = [r.name for r in RULES if r.name not in table]
    assert not missing, f"rules with no frame contract: {missing}"
    # Spot values this session got wrong in both directions at some point.
    assert width_of("chain-li-branch", "a", "li") == 8
    assert width_of("dual-indep-pair", "b", "li") == 6
    assert width_of("mem-pair", "a", "lw") == 6

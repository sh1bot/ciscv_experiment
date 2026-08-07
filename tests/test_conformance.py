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
        # ('p' in a frame's `select`; a cluster's re-letters the same bits by
        # role, so 'p' means the pairs table there and is counted separately)
        o_bits = f["select"].count("p")
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


def test_layout_art_stays_on_the_bit_grid():
    """Every box line of every frame's layout is the same width, and every box
    boundary sits on a bit boundary.

    A layout box is one FIELD, not one grid column: adjoining columns are
    fused (`imma[9:5]` + `imma[4:0]` draws as `imma[9:0]`) and a column split
    between two operands is divided (`imb│rdb`). Both only ever move a
    boundary, never the bits either side of it, so the drawing keeps its
    width and its character positions -- which is what a tool reading the art
    by column depends on. In display coordinates a bit occupies every odd
    position, so a separator at an odd one means a box has overflowed or a
    boundary has landed mid-bit.
    """
    import yaml

    r = _run("util/encoding_assign.py")
    assert r.returncode == 0, r.stderr
    for f in yaml.safe_load(r.stdout)["frames"]:
        art = [ln[:ln.rindex("│") + 1] for ln in f["layout"].splitlines()
               if ln.startswith("│")]
        assert art, f["name"]
        widths = {len(ln) for ln in art}
        assert len(widths) == 1, (f["name"], sorted(widths))
        for ln in art:
            odd = [i for i, c in enumerate(ln) if c == "│" and i % 2]
            assert not odd, (f["name"], odd, ln)
            assert ln[0] == "│" and ln[-1] == "│", (f["name"], ln)


def test_layout_explains_every_shortened_name():
    """A box too narrow for its operand's name draws a shortened one, and
    every shortening a frame uses is spelled out in its `where` footnote --
    otherwise the drawing names a field the reader cannot resolve."""
    sys.path.insert(0, os.path.join(ROOT, "util"))
    import encoding_assign as ea
    from encoding_render import display_widths

    frames, info = ea.load()
    widths = display_widths(info["grid"])
    seen = 0
    for f in frames:
        notes = dict(n.split(" = ", 1) for n in ea.frame_notes(f, widths))
        footnote = [ln for ln in ea.frame_body_lines(f, widths)
                    if ln.strip().startswith("where ")]
        assert bool(notes) == bool(footnote), f["name"]
        w = ea.word_chars(f)
        for row in f["spec"]["rows"]:
            for text, width, _stems, full in ea.cells(ea._tokens(row, w), widths):
                assert len(text) <= width, (f["name"], text, width)
                if full is not None:
                    assert notes.get(text) == full, (f["name"], text, full)
                    seen += 1
    assert seen, "no frame draws a shortened name -- test no longer covers it"


def test_immediate_contracts_derive():
    """The yaml-derived width table must load and cover every rule."""
    sys.path.insert(0, ROOT)
    from scheduler.imm_contracts import _contracts, narrow_field_of, width_of
    from scheduler.rules import RULES
    table = _contracts()
    missing = [r.name for r in RULES if r.name not in table]
    assert not missing, f"rules with no frame contract: {missing}"
    # Spot values this session got wrong in both directions at some point.
    assert width_of("li-branch-chain", "a", "li") == 8
    assert width_of("dual-setup-pair", "a", "li") == 8
    # B-side li is BARE: the wide band is A-only, so b's falls to its field.
    assert width_of("dual-setup-pair", "b", "li") == 5
    assert width_of("mem-base-pair", "a", "lw") == 6
    # The any-rd band beside the a0-a7 split rows: the widest-row fallback
    # read 7 here and silently widened the band the day the split rows landed.
    assert narrow_field_of("dual-setup-pair", "a") == 5
    assert narrow_field_of("dual-setup-pair", "b") == 5


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


# Registers a frame deliberately draws in fewer than five bits, with the class
# the rule must restrict them to.  A narrow register field is legitimate, but it
# is a CONSTRAINT the rule owes: arg-call-pair splits its rs1 column into two
# bits of imma[6:5] and three of rda, so rda is a0-a7 and every op sharing that
# row must say so.  One did not (`addi_rsd`) -- latent, since its corpus pairs
# all land in the class anyway.  Anything not listed here gets the full five.
NARROW_REGISTER_FIELDS = {
    ("arg-call-pair", "rda"): 3,        # a0-a7, enforced by _ARG_REGS
    # dual-setup-pair's wide-li band: the split rows buy two more immediate
    # bits by giving the destination three, so `_dual_indep` demands a0-a7
    # for exactly those and leaves its full-5-bit rows unrestricted.
    ("dual-setup-pair", "rda"): 3,
    ("dual-setup-pair", "rdb"): 3,
}


def test_register_fields_are_five_bits_or_declared_narrow():
    """Every register operand gets a full 5-bit field, or is declared narrow.

    Registers are normally x0..x31 and no rule needs a class check.  Where a row
    SPLITS a column -- packing an immediate fragment beside the register -- the
    register gets fewer bits and the rule owes a matching restriction, which no
    other check enforces.  An earlier version of this test skipped split cells
    outright and so missed the only narrow field in the tree.
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
                col_bits = width.get(col, 5)
                # A split cell is a list of {bits, value} parts, most
                # significant first; each part gets exactly its own `bits`.
                parts = (val if isinstance(val, list)
                         else [{"bits": col_bits, "value": val}])
                for part in parts:
                    v = str(part.get("value", part))
                    got = part.get("bits", col_bits)
                    if v.startswith("imm") or v == "unused":
                        continue
                    if got >= 5:
                        continue
                    declared = NARROW_REGISTER_FIELDS.get((frame["name"], v))
                    if declared == got:
                        continue
                    bad.append(f"{frame['name']}: {v} in {col} gets {got} bits"
                               + ("" if declared is None else
                                  f" (declared {declared})"))
    assert not bad, ("register operands in fields narrower than five bits, not "
                     "declared in NARROW_REGISTER_FIELDS — the owning rule must "
                     "restrict them:\n  " + "\n  ".join(bad))

def test_accepts_pcrel_lo_fields_span_the_residue():
    """`accepts_pcrel_lo` is valid only while the field really spans it.

    An auipc-fed offset is the %pcrel_lo half of an address materialisation:
    the corpus magnitude belongs to the OLD layout, and only the target's
    alignment survives relinking.  A frame may therefore accept such pairs
    unmeasured exactly when its offset field can hold ANY lo the new link
    step produces — declared bits + log2(scale) >= 12, the sign being free
    because the toolchain biases the auipc.  This pins the declaration to
    that arithmetic: narrow a declared frame's field and the declaration
    (with rules.py's skip-the-range-check behaviour) becomes a lie this
    test catches.
    """
    import math
    import yaml as _yaml
    sys.path.insert(0, os.path.join(ROOT, "util"))
    from encoding_render import op_name, op_imm

    access = {"lb": 1, "lbu": 1, "lh": 2, "lhu": 2, "lw": 4, "lwu": 4,
              "ld": 8, "sb": 1, "sh": 2, "sw": 4, "sd": 8}
    spec = _yaml.safe_load(open(os.path.join(ROOT, "encoding.yaml")))
    checked = 0
    for node in spec["doc"]:
        frame = node.get("frame")
        if not frame or not frame.get("accepts_pcrel_lo"):
            continue
        checked += 1
        for cluster in frame.get("ops") or []:
            for entry in cluster.get("a", []):
                c = op_imm(entry)
                if not c or not c.get("bits"):
                    continue    # register-form op: no offset to hold a lo
                k = c.get("scale") or access.get(op_name(entry), 1)
                reach = c["bits"] + int(math.log2(k))
                assert reach >= 12, (
                    f"{frame['name']} declares accepts_pcrel_lo but "
                    f"a:{op_name(entry)} reaches only {reach} bits "
                    f"({c['bits']}b x{k}) — it cannot span the 12-bit "
                    f"pcrel-lo residue")
    assert checked >= 2, f"expected load-call-chain and pre-inc-pair, found {checked}"


# The operand-position discipline (encoding.yaml grid note): these stems have
# a STANDARD column, and every row keeps them there unless the displacement is
# declared here.  A-slot sources and both slots' destinations are pinned; B's
# sources are not (funct5 is the overflow column by convention).  Each entry
# is a deliberate trade — a wide immediate or the partner slot's operand
# claiming the standard column — and the test is two-sided, so a stale entry
# fails just like a missing one.
STANDARD_COLUMNS = {"rs1a": "rs1", "rsda": "rs1", "rbase": "rs1",
                    "rs2a": "rs2", "rdb": "rd", "rsdb": "rd"}
OPERAND_POSITION_EXCEPTIONS = {
    # (frame, stem, column-it-actually-occupies): why
    ("addi-store-chain", "rbase", "rd"):
        "the 10-bit imma owns funct5+rs2 and rs1a keeps rs1; B's base "
        "(offset structurally zero) parks in the remaining column",
    ("addi-store-off-chain", "rbase", "funct5"):
        "rs1a keeps rs1 and B's store immediate owns rd (S-type); the "
        "base takes the overflow column",
    ("pre-inc-pair", "rsda", "rs2"):
        "shXadd walk rows: rsda IS Zba's rs2 (the added pointer) — its "
        "standard port; B's base is A's forwarded result and reads no port",
}


def test_operand_positions_standard_or_declared():
    """Rows keep operands at their standard RISC-V positions, or say why not.

    The grid note states the discipline; this pins it.  A row (or template
    re-ordering, which re-binds operand semantics to columns) that moves a
    pinned stem out of its standard column must add a justified entry above —
    the pre-inc-pair shXadd operand flip went in and out of the tree before
    anything watched this.
    """
    import yaml as _yaml
    sys.path.insert(0, os.path.join(ROOT, "util"))
    from encoding_render import row_parts

    spec = _yaml.safe_load(open(os.path.join(ROOT, "encoding.yaml")))
    grid = spec["grid"]
    observed = set()
    for node in spec["doc"]:
        frame = node.get("frame")
        if not frame:
            continue
        for row in frame.get("rows") or []:
            for field, stem, _bits, _raw in row_parts(row, grid):
                std = STANDARD_COLUMNS.get(stem)
                if std is not None and field != std:
                    observed.add((frame["name"], stem, field))
    declared = set(OPERAND_POSITION_EXCEPTIONS)
    undeclared = observed - declared
    stale = declared - observed
    assert not undeclared, (
        "operands displaced from their standard column without a declared "
        "exception:\n  " + "\n  ".join(map(str, sorted(undeclared))))
    assert not stale, (
        "declared operand-position exceptions no longer present (clean up "
        "the table):\n  " + "\n  ".join(map(str, sorted(stale))))


def test_row_contracts_hold_and_the_lint_has_teeth():
    """No op may declare width its own rows cannot draw (A8 row contract).

    `imm_field_bits` prices every op against a frame's WIDEST row; that is
    only honest for ops that can actually sit on it.  `row_contract_complaints`
    computes, per declared-width op, the widest row able to hold that op's
    operands (learned from the frame's template lines, placeholders included)
    and complains when pricing assumes more.  Gated two ways: the current yaml
    must be clean, and the lint must still catch the documented hazard — a
    6-bit load offset in setup-jump-pair, whose load row is full at 20 bits
    (imma 5) while its li row draws imma 10, so the widest-row pricing would
    call the sixth bit free (TODO A8).
    """
    import copy
    import yaml as _yaml
    sys.path.insert(0, os.path.join(ROOT, "util"))
    from encoding_render import lint_frame, row_contract_complaints

    spec = _yaml.safe_load(open(os.path.join(ROOT, "encoding.yaml")))
    grid = spec["grid"]
    frames = [n["frame"] for n in spec["doc"] if "frame" in n]
    bad = [c for f in frames for c in lint_frame(f, grid)]
    assert not bad, "row-contract violations:\n  " + "\n  ".join(bad)

    sj = copy.deepcopy(next(f for f in frames if f["name"] == "setup-jump-pair"))
    sj["ops"][0]["a"] = ["mv", "lbu",
                        {"op": "lw", "imm": {"bits": 6, "signed": False}}, "ld"]
    caught = row_contract_complaints(sj, grid)
    assert caught and "a:lw" in caught[0], (
        "the lint no longer catches TODO A8's documented hazard — a widened "
        "load offset priced against setup-jump-pair's li row")


def test_frame_containment_runs():
    """The static overlap report must survive every yaml edit.

    It reads clusters, rows and field widths together, so it breaks on schema
    changes that the renderer alone would not notice.
    """
    r = _run("util/frame_containment.py")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "SOLE ENCODER" in r.stdout

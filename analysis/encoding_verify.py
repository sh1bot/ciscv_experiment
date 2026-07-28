"""
analysis/encoding_verify.py — packing verifier for the encoding.yaml layout.

Cross-checks the declared bit layout (encoding.yaml) against real code:

  * the immediate FIELD WIDTH each frame provides is derived from the yaml row
    cells -- e.g. `imma[4:0|9:5]` means A's immediate field is 10 bits, `immb[4:0]`
    means B's is 5 bits, split by SP-relative vs base variant (the `tag`);
  * for every corpus pair a rule matches, the concrete immediate it carries is
    encoded (signed / unsigned / width-scaled) and checked against that field.

Output: per frame, the pair-count, how many carried a checkable immediate, the
pack-rate (fraction whose immediate fits the declared field), and the worst
overflow. Branch/jump displacements are unresolved labels in the corpus, so
they are skipped (the same optimism the rules apply).

Usage:  python3 -m analysis.encoding_verify tests/godot.s tests/testcase0.s
"""
import os, re, sys
from collections import Counter, defaultdict

import yaml

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

from analysis.parser import parse_file
from analysis.liveness import compute_global_liveness, compute_local_liveness
from scheduler.pairing import stamp_slot_eligibility
from scheduler.rules import RULES, NotPair
from analysis.encoding_budget import subform
from analysis.imm_expr import parse_expr
from analysis import imm_traits
from util.encoding_render import op_contracts

# A width no declared field can hold: marks a value the frame cannot encode at
# all (e.g. a negative into an unsigned field), so it is counted as an overflow
# rather than silently dropped.
UNENCODABLE = 99


def required_bits(insn, scale, pair_mem_width=None, contract=None):
    """Significant bits of insn's immediate once scaled the way this frame's
    field scales it (scale = 1 | int | 'k'). For 'k' on a non-memory insn (e.g.
    a post-inc addi stride k*imm), the paired memory op's width is used.
    Signedness and zero-encodability are inferred per-opcode (imm_traits): an
    arithmetic op cannot carry a zero immediate, so its field reclaims the zero
    codepoint for one more magnitude. Returns None if there is no immediate,
    0 if zero. An unaligned immediate returns its unscaled width (overflows)."""
    v = insn.imm
    if v is None:
        return None
    if v == 0:
        return 0
    sf = subform(insn)
    # An op-level contract (encoding.yaml `ops`) overrides the template-derived
    # scale and signedness. It exists for ops that never appear in a template
    # line -- addi4spn is named only in `ops`, so it has no template coefficient
    # to carry its structural x4, and no way to say its field is unsigned.
    signed = None
    if contract:
        if contract.get("scale") is not None:
            scale = contract["scale"]
        signed = contract.get("signed")
    # scale is the multiplier: 1, an int (e.g. 16), or 'k' (the data width).
    div = (insn.access_width or pair_mem_width or 1) if scale == "k" else int(scale)
    if signed is False and v < 0:
        return UNENCODABLE               # negative value, unsigned field
    if v % div != 0:                             # unaligned -> unencodable
        return imm_traits.required_bits(v, sf)
    return imm_traits.required_bits(v // div, sf, signed=signed)

_TOKEN = re.compile(r"^(imm[ab]?)\[(.+)\]$")


def token_bits(tok):
    """Field width in bits declared by an immediate token, e.g.
    'imma[4:0|9:5]' -> 10, 'immb[4:0]' -> 5. Returns (name, bits) or None."""
    tok = tok.split("*")[0]                       # drop any *N span suffix
    m = _TOKEN.match(tok)
    if not m:
        return None
    name, slices = m.group(1), m.group(2)
    hi = 0
    for part in slices.split("|"):
        a = part.split(":")
        hi = max(hi, int(a[0]))                   # high index of each slice
    return name, hi + 1


def rows_of(frame):
    for row in frame["rows"]:
        if isinstance(row, dict):
            yield row["c"], row.get("tag")
        else:
            yield row, None


def frame_capacities(frame):
    """Widest immediate field the frame declares, per SP variant:
    returns {'sp': bits, 'base': bits}. Immediate fields are pooled (we do not
    insist an A-slot immediate use an 'imma' token vs 'immb') -- the physical
    question is just whether some declared field is wide enough."""
    cap = {"sp": 0, "base": 0}
    for cells, tag in rows_of(frame):
        key = "sp" if tag == "SP-relative" else "base"
        for cell in cells:
            tb = token_bits(cell)
            if tb:
                cap[key] = max(cap[key], tb[1])
    return cap


def cap_for(cap, is_sp):
    if is_sp and cap["sp"]:
        return cap["sp"]
    return cap["base"] or cap["sp"] or None


def slot_exprs(frame):
    """The immediate expression per slot, from the first imm-bearing template
    line: {'a': (var, m, b) or None, 'b': ...}. The template coefficient is the
    scale (single source of truth); a constant term marks a compound, relation-
    bearing offset. Branch displacements appear here but their corpus immediate
    is an unresolved label (insn.imm None), so they are skipped downstream."""
    out = {"a": None, "b": None}
    for pair in frame.get("templates", []):
        for side, idx in (("a", 0), ("b", 1)):
            if out[side] is None:
                e = parse_expr(pair[idx])
                if e:
                    out[side] = e
    return out


def is_sp_mem(insn):
    return insn.has_mem_operand and insn.rs1 == 2


def eligible(rule, a, b):
    if rule.a_mnemonic_set and a.mnemonic not in rule.a_mnemonic_set: return False
    if rule.b_mnemonic_set and b.mnemonic not in rule.b_mnemonic_set: return False
    if not all(getattr(a, p) for p in rule.a_prerequisites): return False
    if not all(getattr(b, p) for p in rule.b_prerequisites): return False
    return True


def rule_ok(rule, a, b):
    try:
        rule.check(a, b); return True
    except NotPair:
        return False
    except Exception:
        return False


def main():
    paths = sys.argv[1:] or ["tests/godot.s", "tests/testcase0.s"]
    paths = [p if os.path.isabs(p) else os.path.join(ROOT, p) for p in paths]

    spec = yaml.safe_load(open(os.path.join(ROOT, "encoding.yaml")))
    # map each rule name -> frame display name; and per-frame caps + scale
    rule2frame, caps, scales, contracts = {}, {}, {}, {}
    for node in spec["doc"]:
        if "frame" not in node:
            continue
        f = node["frame"]
        caps[f["name"]] = frame_capacities(f)
        scales[f["name"]] = slot_exprs(f)
        contracts[f["name"]] = op_contracts(f)
        # A frame may list the scheduler rules it covers (when its display name
        # differs from the rule names); otherwise the name IS the rule list.
        for rn in f.get("rules_py_names") or [x.strip() for x in f["name"].split(",")]:
            rule2frame[rn] = f["name"]

    known = {r.name for r in RULES}
    for rn in rule2frame:
        if rn not in known:
            print(f"WARN: yaml frame rule '{rn}' not in RULES", file=sys.stderr)
    for r in RULES:
        if r.name not in rule2frame:
            print(f"WARN: rule '{r.name}' has no yaml frame", file=sys.stderr)

    claimed = Counter()
    checked = Counter()
    packed  = Counter()
    unframed = Counter()
    worst   = defaultdict(lambda: (0, 0, None))   # frame -> (need, cap, insn desc)
    xcheck  = {}                                   # frame -> template/width mismatch

    for path in paths:
        _b, fns = parse_file(open(path).read())
        for fn in fns:
            for bl in fn.blocks:
                stamp_slot_eligibility(bl.instructions)
        for fn in fns:
            gl = compute_global_liveness(fn.blocks)
            for bl in fn.blocks:
                if not bl.instructions: continue
                compute_local_liveness(bl, gl)
                ins = bl.instructions
                for i in range(len(ins) - 1):
                    a, b = ins[i], ins[i + 1]
                    if not (a.a_slot_ok and b.b_slot_ok): continue
                    for rule in RULES:
                        if not eligible(rule, a, b) or not rule_ok(rule, a, b):
                            continue
                        frame = rule2frame.get(rule.name)
                        if frame is None:
                            unframed[rule.name] += 1
                            break              # no declared layout to verify against
                        claimed[frame] += 1
                        cap = caps[frame]
                        exprs = scales[frame]
                        con = contracts[frame]
                        mem_w = next((x.access_width for x in (a, b)
                                      if x.has_mem_operand and x.access_width), None)
                        ok, saw = True, False
                        for insn, side in ((a, "a"), (b, "b")):
                            e = exprs.get(side)
                            # Scale comes from the template coefficient (single
                            # source of truth). A memory op is width-scaled (k);
                            # its own width cross-checks a simple numeric template
                            # scale. A non-memory op takes the template coefficient
                            # (k for a base-mod, 16 for sp, 1 for a plain ALU imm).
                            if insn.has_mem_operand:
                                scale = "k"
                                if (e and e[2] == (0, 0) and e[1][1] == 0
                                        and insn.access_width
                                        and abs(e[1][0]) not in (1, insn.access_width)):
                                    xcheck[frame] = (f"{subform(insn)}: template scale "
                                                     f"{abs(e[1][0])} != width {insn.access_width}")
                            elif e is not None:
                                scale = "k" if e[1][1] else abs(e[1][0])
                            else:
                                scale = 1
                            need = required_bits(insn, scale, mem_w,
                                                 con.get(subform(insn)))
                            if need is None or need == 0:
                                continue           # no / zero immediate to pack
                            saw = True
                            c = cap_for(cap, is_sp_mem(insn))
                            if c is None or need > c:
                                ok = False
                                if need - (c or 0) > worst[frame][0] - worst[frame][1]:
                                    worst[frame] = (need, c or 0,
                                                    f"{subform(insn)} imm={insn.imm}")
                        if saw:
                            checked[frame] += 1
                            if ok:
                                packed[frame] += 1
                        break
    # report
    order = []
    for node in spec["doc"]:
        if "frame" in node and node["frame"]["name"] not in order:
            order.append(node["frame"]["name"])

    hdr = f"{'frame':38} {'claimed':>7} {'imm-chk':>7} {'pack%':>6}  worst overflow"
    print(hdr); print("-" * len(hdr))
    tot_c = tot_chk = tot_pk = 0
    for frame in order:
        cl = claimed.get(frame, 0)
        if not cl: continue
        ck = checked.get(frame, 0)
        pk = packed.get(frame, 0)
        tot_c += cl; tot_chk += ck; tot_pk += pk
        rate = f"{100*pk/ck:5.1f}%" if ck else "   -  "
        w = worst.get(frame)
        wtxt = f"{w[2]} needs {w[0]}b vs {w[1]}b field" if (w and w[2]) else ""
        print(f"{frame:38} {cl:7} {ck:7} {rate:>6}  {wtxt}")
    print("-" * len(hdr))
    orate = f"{100*tot_pk/tot_chk:.1f}%" if tot_chk else "-"
    print(f"{'TOTAL':38} {tot_c:7} {tot_chk:7} {orate:>6}")
    print(f"\nOf {tot_c} matched pairs, {tot_chk} carried a checkable immediate; "
          f"{tot_pk} of those ({orate}) fit their frame's declared field.")
    print("Branch/jump displacements are unresolved in the corpus and are not checked.")
    print("Immediate scale is read from the template coefficient (k*imm etc.);\n"
          "a memory op's own width cross-checks a simple numeric template scale.")
    if xcheck:
        print("\nTemplate scale vs instruction width mismatches (encoding bug?):")
        for fr, msg in xcheck.items():
            print(f"    {fr:34} {msg}")
    if unframed:
        print("\nRules with NO frame in encoding.yaml (unverifiable — a spec gap):")
        for rn, n in unframed.most_common():
            print(f"    {rn:24} {n} matched pairs")


if __name__ == "__main__":
    main()

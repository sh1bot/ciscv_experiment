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
from analysis.encoding_budget import subform, signed_bits, unsigned_bits, IMM_SIGNED


def required_bits(insn, const_scale, kflag, pair_mem_width):
    """Significant bits of insn's immediate once scaled the way this frame's
    field scales it. Returns None if there is no immediate, 0 if it is zero.
    An unaligned (non-divisible) immediate returns its unscaled width, which
    will overflow any reasonable field -- correctly flagging it as unencodable."""
    v = insn.imm
    if v is None:
        return None
    if v == 0:
        return 0
    if insn.has_mem_operand:
        div = insn.access_width or 1              # memory offsets scale by width
    elif const_scale:
        div = const_scale                         # e.g. addi sp, 16*imm
    elif kflag and pair_mem_width:
        div = pair_mem_width                      # dual-mem addi stride = k*imm
    else:
        div = 1
    signed = subform(insn) in IMM_SIGNED
    if v % div != 0:
        return signed_bits(v) if signed else unsigned_bits(abs(v))
    v //= div
    return signed_bits(v) if signed else unsigned_bits(abs(v))

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


_SCALE = re.compile(r"(\d+|k)\s*\*\s*imm")


def frame_scale(frame):
    """(const_scale:int|None, k_scale:bool) parsed from the asm templates:
    '16*imm' -> const 16; 'k*imm' -> width-scaled."""
    const, kflag = None, False
    text = "\n".join(ln for pair in frame["templates"] for ln in pair)
    for m in _SCALE.finditer(text):
        if m.group(1) == "k":
            kflag = True
        else:
            const = int(m.group(1))
    return const, kflag


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
    rule2frame, caps, scales = {}, {}, {}
    for node in spec["doc"]:
        if "frame" not in node:
            continue
        f = node["frame"]
        caps[f["name"]] = frame_capacities(f)
        scales[f["name"]] = frame_scale(f)
        for rn in [x.strip() for x in f["name"].split(",")]:
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
                        const_scale, kflag = scales[frame]
                        mem_w = next((x.access_width for x in (a, b)
                                      if x.has_mem_operand and x.access_width), None)
                        ok, saw = True, False
                        for insn in (a, b):
                            need = required_bits(insn, const_scale, kflag, mem_w)
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
    print("Fields are checked AS DRAWN in encoding.yaml; g/h immediate extension\n"
          "counts only where a row actually widens the slice (e.g. imma[5:0]).")
    if unframed:
        print("\nRules with NO frame in encoding.yaml (unverifiable — a spec gap):")
        for rn, n in unframed.most_common():
            print(f"    {rn:24} {n} matched pairs")


if __name__ == "__main__":
    main()

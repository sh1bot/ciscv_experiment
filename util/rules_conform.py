"""
util/rules_conform.py — check scheduler/rules.py against encoding.yaml.

A first step toward generating rules.py from the yaml. This does NOT generate
anything: it reports, per frame, where the two disagree on the facts the yaml
owns. Deliberately narrow — it checks only what can be compared mechanically
today, and says plainly what it is not checking.

READ THE COVERAGE LINE, NOT JUST THE VERDICT. "0 frames disagree" does NOT
mean rules.py implements encoding.yaml. It means nothing was found among the
narrow set of facts this tool can mechanically compare, on the subset of them
its probes can actually reach.

CHECKED
  * mnemonic sets      — the ops a frame's `ops` clusters allow per slot, vs the
                         rule's a_mnemonic_set / b_mnemonic_set;
  * immediate widths   — a declared `imm: {bits}` op contract vs the range the
                         rule's own check actually accepts. Probed with
                         synthetic pairs built from the frame's OWN op clusters
                         (not a fixed `add`), across a set of register shapes:
                         chained and independent, distinct and shared `rd`,
                         RSD, and sp-based and register-based memory operands.
                         A width is verified if any shape reaches it; a rule no
                         shape reaches is reported as unverifiable, never as
                         correct.
  * base-register class — for every memory operand in a frame's templates,
                         whether the base is drawn as a general register field
                         (`rs1a`/`rs1b`/`rbase`), as the chain temp, or as sp.
                         A frame with a general base whose rule accepts ONLY
                         sp is the @a_sp_mem bug; a frame with an sp-only
                         template whose rule accepts any base is the converse.
  * frame coverage     — rules with no frame, frames with no rule.

NOT CHECKED (scheduler-owned, see scheduler/RULES.md and yaml_migration.md)
  deadness and chaining, operand-form constraints beyond the base register,
  register-class windows, order sensitivity, commutative operand fitting,
  relocation policy.

THE BUG THIS USED TO MISS, kept as a warning: encoding.yaml's
load-chain-alu-pair draws rows 1-2 with an explicit `rs1a` base field and rows
3-4 as the SP-relative variant, while rules.py applied @a_sp_mem
unconditionally and refused every non-sp base. It cost ~1250 pairs and the tool
reported the frame clean, because a register class is not an immediate width.
That check now exists — but its existence is not evidence that the NEXT
structural constraint is covered.

Usage:  python3 util/rules_conform.py [--verbose]
Exit status is 1 if any disagreement is found, so it can gate a commit.
"""
import os, re, sys

import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from scheduler.rules import RULES, NotPair
from isa.xlen import resolve_xlen_op, is_xlen_op
from util.encoding_render import op_name, op_contracts

# Pseudo-op names the yaml may use that are register-operand choices on a real
# mnemonic rather than opcodes of their own.
PSEUDO_BASE = {"li": "addi", "mv": "addi", "addi4spn": "addi",
               "addi_rsd": "addi", "addi_other": "addi",
               "inc": "addi", "dec": "addi",
               "j": "jal", "j_near": "jal", "ret": "jalr",
               "beqz": "beq", "bnez": "bne", "bltz": "blt", "bgez": "bge",
               "blez": "bge", "bgtz": "blt",
               "bltu_r": "bltu", "bge_r": "bge", "bgeu_r": "bgeu"}

# Register numbers the probes use. Nothing here may be x0 (rules read x0 as a
# sentinel) or x2 (sp, which is what the base-class probe is trying to vary).
RD_A, RD_B, RS_1, RS_2, RS_3 = 10, 11, 12, 13, 14
SP = 2

# Immediate sweeps are O(configs x range), so a frame with a dozen ops and ten
# shapes would sweep for minutes. Every config that reaches a rule exercises
# the same width check, so a handful is enough; the cap is here to keep the
# tool fast enough to gate a commit, not because later configs differ.
CONFIG_CAP = 12


def _real_mnemonic(name):
    """The mnemonic a yaml op name denotes.  Pseudo-ops are one-to-one (`li` is
    always `addi`); XLEN-dependent ops are not, so they are resolved against
    the base rules.py is currently set to."""
    if is_xlen_op(name):
        import scheduler.rules as _r
        return resolve_xlen_op(name, _r.XLEN)
    return PSEUDO_BASE.get(name, name)


def frame_slot_ops(frame, slot):
    """Real mnemonics a frame allows in one slot, pseudo-ops mapped to the
    mnemonic that carries them.

    An XLEN-switchable op contributes EVERY mnemonic it can mean, because
    `RULES` is built at import -- before the base is known -- so a rule's
    mnemonic set is necessarily the union over bases.  Which one is the natural
    word for the base actually being scheduled is enforced by the rule's check,
    not by its set, and this comparison does not reach into checks."""
    from isa.xlen import xlen_ops
    out = set()
    for cluster in frame.get("ops") or []:
        for entry in cluster.get(slot, []):
            n = op_name(entry)
            if is_xlen_op(n):
                out.update(xlen_ops()[n].values())
            else:
                out.add(_real_mnemonic(n))
    return out


def op_pairs(frame, limit=3, pin=None):
    """[(a_op, b_op), ...] drawn from the frame's own clusters, as yaml op
    names (pseudo-ops kept — `_mk` applies their operand form).

    Probing with the frame's real op vocabulary is what lets a rule be reached
    at all: a fixed `add` in the off-slot is rejected by most frames before any
    immediate is looked at.

    `pin` is a (slot, op) that must appear in that slot. Without it the per-
    cluster `limit` would silently drop the very op being probed — an op-list
    of a dozen entries only ever offered its first three."""
    out = []
    for cluster in frame.get("ops") or []:
        ops = {s: [op_name(e) for e in cluster.get(s, [])] for s in ("a", "b")}
        if pin:
            slot, want = pin
            if want not in ops[slot] and _real_mnemonic(want) not in [
                    _real_mnemonic(o) for o in ops[slot]]:
                continue
            ops[slot] = [want]
        for a in (ops["a"] or [None])[:limit]:
            for b in (ops["b"] or [None])[:limit]:
                out.append((a, b))
    return out or [(None, None)]


def frame_rule_names(f):
    """The rules.py rules a frame maps to. Some frames carry an explicit
    `rules_py_names` list; the rest split a comma-joined `name` (see TODO A6),
    and both forms can name more than one rule."""
    return f.get("rules_py_names") or [x.strip() for x in f["name"].split(",")]


def load_frames():
    spec = yaml.safe_load(open(os.path.join(ROOT, "encoding.yaml")))
    frames = {}
    for node in spec["doc"]:
        if "frame" not in node:
            continue
        f = node["frame"]
        for rn in frame_rule_names(f):
            frames[rn] = f
    return frames


# ---------------------------------------------------------------------------
# Base-register classes, read off the frame's templates
# ---------------------------------------------------------------------------

_MEM = re.compile(r"\(\s*([A-Za-z_][A-Za-z0-9_]*)\s*\)")


def template_bases(frame, slot):
    """{'sp', 'reg', 'tmp'} — how this slot's memory operand names its base
    across all of the frame's templates. Empty if the slot never touches
    memory. 'reg' means the base is a drawn register field, which is the case
    the sp-only bug gets wrong."""
    idx = 0 if slot == "a" else 1
    kinds = set()
    for tpl in frame.get("templates") or []:
        if not isinstance(tpl, list) or len(tpl) <= idx:
            continue
        for base in _MEM.findall(tpl[idx]):
            if base == "sp":
                kinds.add("sp")
            elif base == "tmp":
                kinds.add("tmp")
            else:
                kinds.add("reg")
    return kinds


# ---------------------------------------------------------------------------
# Probing
# ---------------------------------------------------------------------------

def _mk(op, rd, rs1, rs2, imm):
    """Build a probe instruction for a yaml op name. Pseudo-ops are register
    choices on a real mnemonic, so they carry an operand form as well as a
    name: `li` is an addi from x0, `j` is a jal to x0, `beqz` compares against
    x0. Getting these wrong is why a jump or branch slot was never reached."""
    from isa.instruction import Instruction
    mn = _real_mnemonic(op)
    if op == "li":
        rs1 = 0
    elif op == "mv":
        rs1, imm = rs1, 0
    elif op == "addi4spn":
        rs1 = SP
    elif op == "addi_rsd":
        rs1 = rd
    elif op in ("inc", "dec"):
        rs1, imm = rd, (1 if op == "inc" else -1)
    elif op == "j":
        rd = 0
    elif op == "ret":
        rd, rs1, imm = 0, 1, 0
    elif op in ("beqz", "bnez"):
        rs2 = 0
    i = Instruction(mnemonic=mn, operands=[], raw=mn)
    i.rd, i.rs1, i.rs2, i.imm = rd, rs1, rs2, imm
    i.live_out = frozenset()
    return i


# Each shape assigns (a.rd, a.rs1, a.rs2) and (b.rd, b.rs1, b.rs2). A chain
# means b reads a's destination — through rs1 (a base or first source) or
# through rs2 (a store's data operand, which is a different frame shape and
# was unreachable while only rs1 was tried). RSD means the slot writes back to
# its own source. The `-z` variants put x0 in b.rs2, the form a branch against
# zero takes.
SHAPES = {
    "chain":        ((RD_A, RS_1, RS_2), (RD_B, RD_A, RS_3)),
    "chain-z":      ((RD_A, RS_1, RS_2), (RD_B, RD_A, 0)),
    "chain-data":   ((RD_A, RS_1, RS_2), (RD_B, RS_3, RD_A)),
    "chain-rsd-b":  ((RD_A, RS_1, RS_2), (RD_A, RD_A, RS_3)),
    "indep":        ((RD_A, RS_1, RS_2), (RD_B, RS_3, RS_1)),
    "rsd-a":        ((RD_A, RD_A, RS_2), (RD_B, RS_3, RS_1)),
    "rsd-a-chain":  ((RD_A, RD_A, RS_2), (RD_B, RD_A, RS_3)),
    "rsd-a-chain-z": ((RD_A, RD_A, RS_2), (RD_B, RD_A, 0)),
    "rsd-a-data":   ((RD_A, RD_A, RS_2), (RD_B, RS_3, RD_A)),
    "rsd-both":     ((RD_A, RD_A, RS_2), (RD_B, RD_B, RS_3)),
}


def _pair(a_mn, b_mn, shape, a_imm, b_imm, a_base=None, b_base=None):
    (ard, ars1, ars2), (brd, brs1, brs2) = SHAPES[shape]
    if a_base is not None:
        ars1 = a_base
    if b_base is not None:
        brs1 = b_base
    return _mk(a_mn, ard, ars1, ars2, a_imm), _mk(b_mn, brd, brs1, brs2, b_imm)


def accepts(rule, *args, **kw):
    a, b = _pair(*args, **kw)
    try:
        rule.check(a, b)
        return True
    except NotPair:
        return False
    except Exception:
        return False


def reachable_configs(rule, frame, slot, pin=None):
    """[(a_op, b_op, shape, base_kw), ...] that this rule actually accepts with
    a zero immediate — the configurations any further probe of `slot` can use.

    Memory slots are tried with both an sp base and a general-register base, so
    a rule that only takes one of them is still reached."""
    out = []
    for a_mn, b_mn in op_pairs(frame, pin=pin):
        if a_mn is None or b_mn is None:
            continue
        for shape in SHAPES:
            for kw in ({}, {"a_base": SP}, {"b_base": SP}, {"a_base": SP, "b_base": SP}):
                if accepts(rule, a_mn, b_mn, shape, 0, 0, **kw):
                    out.append((a_mn, b_mn, shape, kw))
    return out


def accepted_range(rule, frame, mnemonic, slot, lo=-4096, hi=4096):
    """The union of immediate values `rule` accepts for `mnemonic` in `slot`,
    over every configuration that reaches the rule at all. None if no
    configuration does — a probe result, not a proof."""
    configs = reachable_configs(rule, frame, slot, pin=(slot, mnemonic))
    if not configs:
        return None
    seen = set()
    for a_mn, b_mn, shape, kw in configs[:CONFIG_CAP]:
        for v in range(lo, hi + 1):
            if v in seen:
                continue
            imms = (v, 0) if slot == "a" else (0, v)
            if accepts(rule, a_mn, b_mn, shape, *imms, **kw):
                seen.add(v)
    return (min(seen), max(seen)) if seen else None


def base_class_note(siblings, frame, slot):
    """Compare the frame's declared base-register class for `slot` against what
    the rules accept. `siblings` is every rule the frame maps to — a frame such
    as `load-sp-branch, load-base-branch` splits the two base classes across
    two rules, so the comparison is against their union. Returns a complaint
    string, or None."""
    kinds = template_bases(frame, slot)
    if not kinds or kinds == {"tmp"}:
        return None                      # no memory operand, or an implicit temp
    key = "a_base" if slot == "a" else "b_base"
    sp_ok = reg_ok = False
    for rule in siblings:
        for a_mn, b_mn in op_pairs(frame):
            if a_mn is None or b_mn is None:
                continue
            for shape in SHAPES:
                if accepts(rule, a_mn, b_mn, shape, 0, 0, **{key: SP}):
                    sp_ok = True
                if accepts(rule, a_mn, b_mn, shape, 0, 0):
                    reg_ok = True        # SHAPES never puts sp in a base slot
                if sp_ok and reg_ok:
                    break
    if not sp_ok and not reg_ok:
        return None                      # rule unreachable; counted elsewhere
    if "reg" in kinds and not reg_ok:
        return (f"{slot}: yaml draws a general base field ({'/'.join(sorted(kinds))}), "
                f"rules.py accepts sp only"
                + (" (across every rule this frame maps to)"
                   if len(siblings) > 1 else ""))
    if kinds == {"sp"} and reg_ok:
        return (f"{slot}: yaml draws this slot sp-relative only, "
                f"rules.py accepts any base register")
    return None


def bits_to_range(bits, signed):
    if signed:
        return -(1 << (bits - 1)), (1 << (bits - 1)) - 1
    return 0, (1 << bits) - 1


def main():
    verbose = "--verbose" in sys.argv
    frames = load_frames()
    rules = {r.name: r for r in RULES}
    problems = 0

    for rn, rule in rules.items():
        if rn not in frames:
            print(f"✗ {rn}: no frame in encoding.yaml")
            problems += 1
    for rn in frames:
        if rn not in rules:
            print(f"✗ yaml frame names rule '{rn}', absent from RULES")
            problems += 1

    reached = unreachable = 0
    bases_checked = 0
    unverified: list = []
    base_done: set = set()
    for rn, rule in rules.items():
        frame = frames.get(rn)
        if frame is None:
            continue
        notes = []
        siblings = [rules[n] for n in frame_rule_names(frame) if n in rules]
        # A frame naming several rules spreads its clusters across them, so a
        # per-slot set comparison is not meaningful for any one of them.
        multi = len(siblings) > 1
        # When rules.py treats the slots symmetrically it accepts either order,
        # while the yaml lists only the canonical one; compare against the union.
        symmetric = (rule.a_mnemonic_set is not None
                     and rule.a_mnemonic_set == rule.b_mnemonic_set)
        for slot, attr in (("a", "a_mnemonic_set"), ("b", "b_mnemonic_set")):
            if multi:
                continue
            want = frame_slot_ops(frame, slot)
            if symmetric:
                want = frame_slot_ops(frame, "a") | frame_slot_ops(frame, "b")
            # `measures_also` declares mnemonics the rule may MATCH beyond
            # what the frame encodes (measurement optimism, e.g. addiw billed
            # as full-width inc) — declared in the yaml, honoured here.
            want |= set((frame.get("measures_also") or {}).get(slot, []))
            got = getattr(rule, attr)
            if not want or got is None:
                continue
            got = {_real_mnemonic(m) for m in got}
            if want != got:
                if want - got:
                    notes.append(f"{slot}: yaml has {sorted(want - got)}, rules.py lacks them")
                if got - want:
                    notes.append(f"{slot}: rules.py has {sorted(got - want)}, yaml lacks them")

        for slot in ("a", "b"):
            if not template_bases(frame, slot) - {"tmp"}:
                continue
            if (id(frame), slot) in base_done:
                continue                 # one report per frame, not per sibling
            base_done.add((id(frame), slot))
            bases_checked += 1
            note = base_class_note(siblings, frame, slot)
            if note:
                notes.append(note)

        contracts = op_contracts(frame)
        for mn, c in contracts.items():
            bits = c.get("bits")
            if not bits or c.get("scale"):     # scaled fields need the width; skip
                continue
            base = _real_mnemonic(mn)
            for slot in ("a", "b"):
                if base not in frame_slot_ops(frame, slot):
                    continue
                want = bits_to_range(bits, c.get("signed", True))
                got = accepted_range(rule, frame, mn, slot)
                if got is None:
                    unreachable += 1
                    unverified.append(f"{rn} {slot}:{mn}")
                    if verbose:
                        notes.append(f"{slot}: {mn} never accepted by any probe "
                                     f"(other constraints may gate it)")
                    continue
                reached += 1
                if got != want:
                    notes.append(f"{slot}: {mn} yaml {bits}b = {want}, "
                                 f"rules.py accepts {got}")
        if multi and verbose:
            print(f"· {rn}: frame '{frame['name']}' covers "
                  f"{frame['rules_py_names']}; op sets not compared per-rule")
        if notes:
            problems += 1
            print(f"✗ {rn}")
            for n in notes:
                print(f"    {n}")
        elif verbose:
            print(f"✓ {rn}")

    total = reached + unreachable
    print(f"\n{problems} frame(s) disagree with encoding.yaml.")
    if total:
        print(f"COVERAGE: {reached} of {total} declared immediate contracts were "
              f"actually verified ({100 * reached // total}%); {unreachable} could "
              f"not be\nreached — no probe shape is accepted by those rules, so "
              f"their widths are\nUNCHECKED, not confirmed. "
              f"{bases_checked} memory slot(s) had their base-register class checked.")
        if unverified and not verbose:
            print("  unverified: " + ", ".join(sorted(unverified)[:6])
                  + (f", +{len(unverified) - 6} more (--verbose)"
                     if len(unverified) > 6 else ""))
    print("Structural facts — deadness, chaining, operand form, order — are still "
          "not\ncompared. A clean verdict here is not evidence that rules.py "
          "implements the yaml.")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())

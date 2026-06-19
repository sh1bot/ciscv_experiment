"""
analysis/pair_stats.py — corpus pairing statistics and next-candidate analysis.

Runs the LIST-mode scheduler over an assembly file and reports:
  * overall pair rate, no-partner rate, RVC-eligible rate
  * per-mnemonic breakdown
  * which rule (if any) paired each pair — rule utilisation
  * for adjacent solo/solo neighbours in the scheduled stream, the *closest*
    rejection reason, grouped by rule and category — the near-miss frontier.
"""

import sys
import os
from collections import Counter, defaultdict

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from analysis.parser import parse_file
from analysis.liveness import compute_global_liveness, compute_local_liveness
from analysis.depgraph import build_dep_graph
from scheduler.reorder import schedule, ScheduleMode
from scheduler.pairing import greedy_pair, stamp_slot_eligibility, can_pair, RULES


def schedule_file(path, mode=ScheduleMode.LIST):
    with open(path) as f:
        source = f.read()
    _blocks, functions = parse_file(source)

    # (kind, ...) packet stream plus the post-schedule ordered block lists.
    all_packets = []
    ordered_blocks = []
    for fn in functions:
        for block in fn.blocks:
            stamp_slot_eligibility(block.instructions)
        global_result = compute_global_liveness(fn.blocks)
        for block in fn.blocks:
            if not block.instructions:
                continue
            compute_local_liveness(block, global_result)
            graph = build_dep_graph(block) if mode != ScheduleMode.FORWARD else None
            ordered = schedule(block, graph, mode)
            if mode != ScheduleMode.FORWARD:
                block.instructions = ordered
                compute_local_liveness(block, global_result)
            ordered_blocks.append(ordered)
            all_packets.extend(greedy_pair(ordered))
    return all_packets, ordered_blocks


def which_rule(a, b):
    """Return the name of the first rule that accepts (a,b), else None."""
    from scheduler.pairing import _rule_applies  # may not exist; fall back
    for rule in RULES:
        # Replicate can_pair's per-rule acceptance test.
        res = _try_rule(rule, a, b)
        if res is None:
            return rule.name
    return None


def _try_rule(rule, a, b):
    # mnemonic gating
    if rule.a_mnemonic_set is not None and a.mnemonic not in rule.a_mnemonic_set:
        return "a-mnemonic"
    if rule.b_mnemonic_set is not None and b.mnemonic not in rule.b_mnemonic_set:
        return "b-mnemonic"
    for prop in rule.a_prerequisites:
        if not getattr(a, prop):
            return "a-prereq"
    for prop in rule.b_prerequisites:
        if not getattr(b, prop):
            return "b-prereq"
    return rule.check(a, b)


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else os.path.join(
        os.path.dirname(__file__), "..", "tests", "godot.s")

    packets, ordered_blocks = schedule_file(path)

    # ---- overall + per-mnemonic ----
    total = paired = rvc = 0
    per_mn = defaultdict(lambda: [0, 0, 0])  # mnemonic -> [total, paired, rvc]
    rule_use = Counter()

    for pkt in packets:
        kind = pkt[0]
        if kind == "pair":
            _, a, b, *rest = pkt
            rname = rest[0] if rest else which_rule(a, b)
            rule_use[rname] += 1
            for insn in (a, b):
                total += 1
                paired += 1
                per_mn[insn.mnemonic][0] += 1
                per_mn[insn.mnemonic][1] += 1
                if insn.rvc_eligible:
                    rvc += 1
                    per_mn[insn.mnemonic][2] += 1
        elif kind == "solo":
            insn = pkt[1]
            total += 1
            per_mn[insn.mnemonic][0] += 1
            if insn.rvc_eligible:
                rvc += 1
                per_mn[insn.mnemonic][2] += 1

    print(f"{'='*72}")
    print(f"OVERALL  ({os.path.basename(path)})")
    print(f"{'='*72}")
    print(f"  instructions : {total}")
    print(f"  paired       : {paired}  ({100*paired/total:.1f}%)")
    print(f"  solo         : {total-paired}  ({100*(total-paired)/total:.1f}%)")
    print(f"  RVC-eligible : {rvc}  ({100*rvc/total:.1f}%)")

    print(f"\n  Rule utilisation (pairs):")
    for rname, cnt in rule_use.most_common():
        print(f"    {rname:<24} {cnt:>7}  ({100*cnt/paired:.1f}% of paired insns' pairs)")

    print(f"\n{'='*72}")
    print(f"PER-MNEMONIC  (top 30 by count)")
    print(f"{'='*72}")
    print(f"  {'mnemonic':<14}{'total':>8}{'paired':>8}{'pair%':>7}{'rvc%':>7}")
    for mn, (t, p, r) in sorted(per_mn.items(), key=lambda kv: -kv[1][0])[:30]:
        print(f"  {mn:<14}{t:>8}{p:>8}{100*p/t:>6.1f}%{100*r/t:>6.1f}%")

    # ---- near-miss frontier: adjacent solo/solo neighbours ----
    reason_by_rule = Counter()
    reason_detail = Counter()
    adj_solo_pairs = 0
    for ordered in ordered_blocks:
        # recompute which were emitted solo by re-running greedy on this block
        pk = greedy_pair(ordered)
        solo_set = {id(p[1]) for p in pk if p[0] == "solo"}
        for i in range(len(ordered) - 1):
            a, b = ordered[i], ordered[i+1]
            if id(a) in solo_set and id(b) in solo_set:
                adj_solo_pairs += 1
                # best (least-bad) rule outcome: find rule whose check ran
                best = _closest_reason(a, b)
                if best:
                    rule, cat = best
                    reason_by_rule[rule] += 1
                    reason_detail[f"{rule}: {cat}"] += 1

    print(f"\n{'='*72}")
    print(f"NEAR-MISS FRONTIER  (adjacent solo+solo neighbours: {adj_solo_pairs})")
    print(f"{'='*72}")
    print("  Closest rule that gated each adjacent solo/solo neighbour")
    print("  (the rule whose mnemonic-set matched but whose check/prereq failed):")
    print(f"\n  By rule:")
    for rule, cnt in reason_by_rule.most_common():
        print(f"    {rule:<24} {cnt:>7}")
    print(f"\n  By detailed reason (top 25):")
    for reason, cnt in reason_detail.most_common(25):
        print(f"    {cnt:>7}  {reason}")


def _closest_reason(a, b):
    """Find the rule that 'almost' applied to (a,b): mnemonic-sets matched
    (in some order) but the prereq/check failed. Returns (rule_name, category).
    Prefers rules that got furthest (check ran rather than mnemonic mismatch)."""
    candidates = []
    for rule in RULES:
        for x, y in ((a, b), (b, a)):
            res = _try_rule(rule, x, y)
            if res is None:
                return None  # actually pairs — shouldn't happen for solo/solo
            # rank: how far did we get? mnemonic miss = 0, prereq = 1, check = 2
            if res in ("a-mnemonic", "b-mnemonic"):
                rank = 0
            elif res in ("a-prereq", "b-prereq"):
                rank = 1
            else:
                rank = 2
            candidates.append((rank, rule.name, _categorise(res)))
    if not candidates:
        return None
    candidates.sort(key=lambda t: -t[0])
    rank, rname, cat = candidates[0]
    if rank == 0:
        return None  # nothing even matched mnemonics; not a near miss
    return (rname, cat)


def _categorise(reason):
    """Collapse a specific rejection string into a coarse category."""
    if reason in ("a-mnemonic", "b-mnemonic"):
        return "mnemonic"
    if reason in ("a-prereq", "b-prereq"):
        return "prerequisite"
    r = reason.lower()
    if "not in x0" in r or ("x" in r and "not in" in r and "reg" in r):
        return "register out of range"
    if "offset" in r or "imm" in r or "range" in r or "scaled" in r:
        return "immediate/offset out of range"
    if "consume" in r or "does not" in r or "feed" in r or "chain" in r:
        return "no producer/consumer chain"
    if "base" in r:
        return "base register mismatch"
    if "escape" in r or "live" in r or "dead" in r:
        return "value escapes (live after)"
    if "source" in r or "operand" in r or "differ" in r:
        return "operands differ"
    if "dest" in r or "rd" in r:
        return "destination conflict"
    if "rsd" in r or "not rsd" in r:
        return "not RSD form"
    return reason.split(":", 1)[-1].strip()[:40]


if __name__ == "__main__":
    main()

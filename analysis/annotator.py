"""
analysis/annotator.py — Format annotated assembly output.
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional

from isa.instruction import Instruction
from isa.registers import reg_name


@dataclass
class PairingStats:
    pairs:     int = 0
    solos:     int = 0
    rvc_total: int = 0   # instructions that are RVC-eligible
    rule_hits: dict = field(default_factory=dict)  # rule_name -> int

    @property
    def instructions(self) -> int:
        return self.pairs * 2 + self.solos

    @property
    def packets(self) -> int:
        return self.pairs + self.solos

    def pair_rate(self) -> float:
        return self.pairs / self.packets if self.packets else 0.0

    def rvc_rate(self) -> float:
        return self.rvc_total / self.instructions if self.instructions else 0.0

    def __iadd__(self, other: "PairingStats") -> "PairingStats":
        self.pairs     += other.pairs
        self.solos     += other.solos
        self.rvc_total += other.rvc_total
        for rule, count in other.rule_hits.items():
            self.rule_hits[rule] = self.rule_hits.get(rule, 0) + count
        return self


def _stats_comment_lines(stats: PairingStats, label: str, rule_breakdown: bool = False) -> list[str]:
    lines = [f"# --- {label} ---"]
    n = stats.instructions
    p = stats.pairs
    s = stats.solos
    pk = stats.packets
    lines.append(f"#   instructions: {n}  packets: {pk}  pairs: {p}  solos: {s}")
    lines.append(f"#   pair rate:    {stats.pair_rate()*100:.1f}%  ({p*2}/{n} instructions paired)")
    lines.append(f"#   RVC-eligible: {stats.rvc_rate()*100:.1f}%  ({stats.rvc_total}/{n} instructions)")
    if rule_breakdown and stats.rule_hits:
        lines.append("#   rule hits:")
        for rule, count in sorted(stats.rule_hits.items()):
            lines.append(f"#     {rule}: {count}")
    return lines


def annotate_output(fn_packets: list[tuple], annotate_liveness: bool = False) -> str:
    """Format annotated assembly output.

    fn_packets: list of (fn_name, packets) where packets is a list of
      ('solo', insn) or ('pair', insn_a, insn_b, rule_name)
    Returns the formatted text with per-function and file-level stats appended.
    """
    lines = []
    total_stats = PairingStats()
    packet_num = 1

    for fn_name, packets in fn_packets:
        fn_stats = PairingStats()

        for item in packets:
            if item[0] == 'pair':
                _, a, b, rule_name = item
                fn_stats.pairs += 1
                fn_stats.rule_hits[rule_name] = fn_stats.rule_hits.get(rule_name, 0) + 1
                if a.rvc_eligible:
                    fn_stats.rvc_total += 1
                if b.rvc_eligible:
                    fn_stats.rvc_total += 1

                for pl in a.prefix_lines:
                    lines.append(pl)
                comment_a = f"# {{packet {packet_num}a}}"
                if annotate_liveness:
                    comment_a += f"  live_in={_fmt_live(a.live_in)}"
                lines.append(f"{a.raw.rstrip()}  {comment_a}")

                for pl in b.prefix_lines:
                    lines.append(pl)
                comment_b = f"# {{packet {packet_num}b}}"
                if annotate_liveness:
                    comment_b += f"  live_in={_fmt_live(b.live_in)}"
                lines.append(f"{b.raw.rstrip()}  {comment_b}")
                packet_num += 1

            else:  # solo
                _, insn = item
                fn_stats.solos += 1
                if insn.rvc_eligible:
                    fn_stats.rvc_total += 1

                for pl in insn.prefix_lines:
                    lines.append(pl)
                rvc = _rvc_marker(insn)
                prefix = f"{rvc}  " if rvc else ""
                if insn.solo_reasons:
                    reasons_str = ", ".join(sorted(insn.solo_reasons))
                    comment = f"# {prefix}{{solo: {reasons_str}}}"
                else:
                    comment = f"# {prefix}{{solo}}"
                if annotate_liveness:
                    comment += f"  live_in={_fmt_live(insn.live_in)}"
                lines.append(f"{insn.raw.rstrip()}  {comment}")

        lines.append("")
        lines.extend(_stats_comment_lines(fn_stats, f"function: {fn_name}"))
        total_stats += fn_stats

    lines.append("")
    lines.extend(_stats_comment_lines(total_stats, "file totals", rule_breakdown=True))

    return "\n".join(lines)


def _rvc_marker(insn: Instruction) -> str:
    if insn.is_unknown:
        return "[?]"
    if insn.rvc_eligible:
        return "[C]"
    return ""


def _fmt_live(live: frozenset) -> str:
    if not live:
        return "{}"
    names = sorted(reg_name(r) for r in live)
    return "{" + ",".join(names) + "}"

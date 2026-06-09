"""
analysis/annotator.py — Format annotated assembly output.
"""

from __future__ import annotations
from typing import Optional

from isa.instruction import Instruction
from isa.registers import reg_name


def annotate_output(packets: list, annotate_liveness: bool = False) -> str:
    """Format annotated assembly output from a list of packet items.

    packets: list of ('solo', insn) or ('pair', insn_a, insn_b)
    """
    lines = []
    packet_num = 1

    for item in packets:
        if item[0] == 'pair':
            _, a, b = item
            # Emit prefix_lines
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
            for pl in insn.prefix_lines:
                lines.append(pl)
            rvc = _rvc_marker(insn)
            if insn.solo_reasons:
                reasons_str = ", ".join(sorted(insn.solo_reasons))
                comment = f"# {rvc}  {{solo: {reasons_str}}}"
            else:
                comment = f"# {rvc}  {{solo}}"
            if annotate_liveness:
                comment += f"  live_in={_fmt_live(insn.live_in)}"
            lines.append(f"{insn.raw.rstrip()}  {comment}")

    return "\n".join(lines)


def _rvc_marker(insn: Instruction) -> str:
    if insn.is_unknown:
        return "[?]"
    if insn.rvc_eligible:
        return "[C]"
    return "[~C]"


def _fmt_live(live: frozenset) -> str:
    if not live:
        return "{}"
    names = sorted(reg_name(r) for r in live)
    return "{" + ",".join(names) + "}"

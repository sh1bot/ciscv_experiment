"""
__main__.py — CLI entry point for the RISC-V instruction scheduler.

Usage: python -m rv_scheduler [options] input.s
  (or: python __main__.py [options] input.s from the repo root)
"""

from __future__ import annotations
import argparse
import sys

from analysis.parser import parse_file
from analysis.liveness import compute_global_liveness, compute_local_liveness
from analysis.depgraph import build_dep_graph
from scheduler.reorder import schedule, ScheduleMode
from scheduler.pairing import greedy_pair, stamp_slot_eligibility
from analysis.annotator import annotate_output


def main():
    parser = argparse.ArgumentParser(
        description="RISC-V instruction scheduler — pair instructions into packets"
    )
    parser.add_argument("input", help="Input assembly file")
    parser.add_argument("--output", "-o", default=None, help="Output file (default: stdout)")
    parser.add_argument("--fast", action="store_true",
                        help="Forward-scan only, no reordering")
    parser.add_argument("--thorough", action="store_true",
                        help="BnB reordering within windows")
    parser.add_argument("--same-base-reorder", action="store_true",
                        help="Relax memory ordering between independent same-base loads/stores")
    parser.add_argument("--annotate-liveness", action="store_true",
                        help="Include live-in register sets in comments")
    parser.add_argument("-v", "--verbose", action="store_true",
                        help="Verbose output (to stderr)")

    args = parser.parse_args()

    # Determine scheduling mode
    if args.thorough:
        mode = ScheduleMode.BNB
    elif args.fast:
        mode = ScheduleMode.FORWARD
    else:
        mode = ScheduleMode.LIST

    # Read input
    with open(args.input) as f:
        source = f.read()

    # Parse
    blocks, functions = parse_file(source)

    # Stamp slot eligibility once per instruction (intrinsic properties only)
    for fn in functions:
        for block in fn.blocks:
            stamp_slot_eligibility(block.instructions)

    # Process each function independently
    all_packets = []
    all_trailing_lines = []

    for fn in functions:
        # Global liveness pass
        global_result = compute_global_liveness(fn.blocks)

        for block in fn.blocks:
            if not block.instructions:
                continue

            # Local liveness
            compute_local_liveness(block, global_result)

            # Build dep graph (not needed for FORWARD)
            graph = None
            if mode != ScheduleMode.FORWARD:
                graph = build_dep_graph(block, same_base_reorder=args.same_base_reorder)

            # Schedule
            ordered = schedule(block, graph, mode)

            # Recompute local liveness on new ordering
            if mode != ScheduleMode.FORWARD:
                block.instructions = ordered
                compute_local_liveness(block, global_result)

            # Greedy pairing
            packets = greedy_pair(ordered)
            all_packets.extend(packets)

    # Format output
    output_text = annotate_output(all_packets, annotate_liveness=args.annotate_liveness)

    if args.output:
        with open(args.output, 'w') as f:
            f.write(output_text + "\n")
    else:
        print(output_text)


if __name__ == "__main__":
    main()

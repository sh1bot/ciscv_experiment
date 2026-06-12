"""
__main__.py — CLI entry point for the RISC-V instruction scheduler.

Usage: python -m rv_scheduler [options] input.s
  (or: python __main__.py [options] input.s from the repo root)
"""

from __future__ import annotations
import argparse
import re
import sys
from concurrent.futures import ProcessPoolExecutor, as_completed

from analysis.parser import parse_file
from analysis.annotator import annotate_output
from scheduler.reorder import ScheduleMode


def _split_source(source: str) -> list[str]:
    """Split assembly source into per-function chunks at .globl/.type @function boundaries.

    Each chunk starts at the line that opens a new function (the .globl or
    .type directive, or any preceding blank/comment lines that belong to it)
    and ends just before the next such boundary.  The file header (anything
    before the first function marker) is returned as the first chunk.
    """
    lines = source.splitlines(keepends=True)
    # A line is a function boundary if it contains .globl/.global/.weak or
    # .type <name>, @function.
    boundary = re.compile(
        r'^\s*(\.(globl|global|weak)\s+\S+|\.type\s+\S+\s*,\s*@function)'
    )
    # Find indices of boundary lines
    cuts = [i for i, ln in enumerate(lines) if boundary.match(ln)]
    if not cuts:
        return [source]

    chunks = []
    prev = 0
    for cut in cuts[1:]:
        chunks.append("".join(lines[prev:cut]))
        prev = cut
    chunks.append("".join(lines[prev:]))
    return chunks


def _process_chunk(chunk: str, same_base_reorder: bool, mode: ScheduleMode) -> list[tuple]:
    """Parse and schedule one source chunk; return its fn_packets list."""
    from analysis.liveness import compute_global_liveness, compute_local_liveness
    from analysis.depgraph import build_dep_graph
    from scheduler.reorder import schedule
    from scheduler.pairing import greedy_pair, stamp_slot_eligibility

    _blocks, functions = parse_file(chunk)

    fn_packets = []
    for fn in functions:
        fn_name = fn.name if fn.name else "(unknown)"
        for block in fn.blocks:
            stamp_slot_eligibility(block.instructions)

        global_result = compute_global_liveness(fn.blocks)
        fn_block_packets = []
        for block in fn.blocks:
            if not block.instructions:
                continue
            compute_local_liveness(block, global_result)
            graph = None
            if mode != ScheduleMode.FORWARD:
                graph = build_dep_graph(block, same_base_reorder=same_base_reorder)
            ordered = schedule(block, graph, mode)
            if mode != ScheduleMode.FORWARD:
                block.instructions = ordered
                compute_local_liveness(block, global_result)
            fn_block_packets.extend(greedy_pair(ordered))

        fn_packets.append((fn_name, fn_block_packets))

    return fn_packets


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

    if args.thorough:
        mode = ScheduleMode.BNB
    elif args.fast:
        mode = ScheduleMode.FORWARD
    else:
        mode = ScheduleMode.LIST

    with open(args.input) as f:
        source = f.read()

    chunks = _split_source(source)

    # Sort chunks largest-first so the heaviest work starts immediately.
    indexed = sorted(enumerate(chunks), key=lambda t: -len(t[1]))

    results: dict[int, list] = {}
    with ProcessPoolExecutor() as executor:
        futures = {
            executor.submit(_process_chunk, chunk, args.same_base_reorder, mode): orig_idx
            for orig_idx, chunk in indexed
        }
        for future in as_completed(futures):
            orig_idx = futures[future]
            results[orig_idx] = future.result()

    fn_packets = []
    for i in range(len(chunks)):
        fn_packets.extend(results[i])

    output_text = annotate_output(fn_packets, annotate_liveness=args.annotate_liveness)

    if args.output:
        with open(args.output, 'w') as f:
            f.write(output_text + "\n")
    else:
        print(output_text)


if __name__ == "__main__":
    main()

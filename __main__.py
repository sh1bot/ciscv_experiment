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
    """Split assembly source into per-function chunks.

    Each chunk starts at the earliest line belonging to a new function:
    the .globl/.weak declaration (if any) preceding a .type @function marker,
    or the .type @function line itself.  File header content (before the first
    function marker) is returned as the first chunk.
    """
    lines = source.splitlines(keepends=True)
    _globl = re.compile(r'^\s*\.(globl|global|weak)\s+\S+')
    _type_fn = re.compile(r'^\s*\.type\s+\S+\s*,\s*@function')

    # Primary split points: .type @function lines.
    type_indices = [i for i, ln in enumerate(lines) if _type_fn.match(ln)]
    if not type_indices:
        return [source]

    # For each .type line, search backward to absorb any preceding .globl/.weak
    # lines (skipping over blank/comment-only lines) into the same chunk.
    cut_set: set = set()
    for ti in type_indices:
        cut = ti
        j = ti - 1
        while j >= 0:
            stripped = re.sub(r'#.*$', '', lines[j]).strip()
            if not stripped:
                j -= 1
                continue
            if _globl.match(lines[j]):
                cut = j
                j -= 1
            else:
                break
        cut_set.add(cut)

    cuts = sorted(cut_set)
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
            if block.prefix_lines:
                fn_block_packets.append(('block_prefix', block.prefix_lines))
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
    parser.add_argument("--overlap", type=int, default=None, metavar="N",
                        help="BnB window overlap (instructions carried between windows, default 0)")
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

    import scheduler.reorder as _reorder
    if args.overlap is not None:
        _reorder.WINDOW_OVERLAP = args.overlap

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

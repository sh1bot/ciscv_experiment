# TODO / Open questions

Deferred items from the GOALS/PLAN/CODE consistency review.  These are parked
pending a decision on the intended behaviour — they are *not* yet settled, so no
code or doc change has been made for them.  Numbering follows the review's
consolidated reconciliation table.

## 3 — `stamp_solo_reasons` is never wired into the pipeline
`scheduler/pairing.py:stamp_solo_reasons()` (and the per-rule `diagnose_a`/
`diagnose_b` callbacks it drives) computes partner-independent solo reasons such
as "immediate out of range" / "not RSD form".  It is only called from tests;
`__main__.py:_process_chunk` calls `stamp_slot_eligibility` but not
`stamp_solo_reasons`.  As a result, a solo instruction with no eligible
neighbour can be emitted with a bare `{solo}` and none of the structural
"why it can never pair" reasons GOALS §1 promises.
**Decision needed:** wire `stamp_solo_reasons` into `_process_chunk`, or
accept opportunistic (partner-dependent) reasons and soften GOALS §1 wording.

## 5 (held) — PLAN documentation of scheduler internals
`--overlap` and `base_from_auipc` are now documented.  Still undocumented in
PLAN, held until their intended behaviour is settled:
- `diagnose_a` / `diagnose_b` fields on `PairingRule` (and `stamp_solo_reasons`).
- `_backward_pair()` branch-rescue second pass (`scheduler/pairing.py`).
- `STALL_FOR_PAIR` list-scheduler heuristic and the `--no-stall-for-pair` flag
  (`scheduler/reorder.py`, `__main__.py`).

## 6 — PLAN attributes CFG/function logic to the wrong module
PLAN §6 / §28 describe `analysis/cfg.py` as building CFG edges and identifying
functions, but `cfg.py` contains only the `BasicBlock`/`Function` dataclasses;
the edge-building and `identify_functions` logic live in `analysis/parser.py`
(`parse_file`).  **Decision needed:** update PLAN §6 to attribute the logic to
`parser.py` (as §4 already does for the decoders).

## 7 — `--verbose` defined but never read
`__main__.py` defines `-v/--verbose` and PLAN §13 documents detailed behaviour
("show all candidate pairs ... to stderr"), but `args.verbose` is never read.
**Decision needed:** implement it or remove the flag and its PLAN description.

## 12 — GOALS dataflow wording is narrower than the code
GOALS §2 phrases the reordering constraint as RAW-only ("reads a register that
has not yet been written").  The dependency graph enforces RAW + WAR + WAW
(plus memory and barrier edges) — `analysis/depgraph.py`.  **Decision needed:**
broaden the GOALS §2 wording to cover all true register/memory dependencies.

## 14 — code cleanups
- Dead `mem_pair` match-kind branch in `_dual_shared_ok` (`scheduler/rules.py`):
  no entry in `_DUAL_TUPLES` maps to `"mem_pair"`, so the branch is unreachable.
- `is_jump` docstring (`isa/instruction.py`) says it excludes calls/returns, but
  the predicate returns True for any `jal`/`jalr` including those.  Reconcile the
  docstring with the predicate (or the predicate with the docstring).

## 15 — goals with no supporting tool/test/scope
- **Cross-rule-set comparison** (GOALS §0.4 / §1): no tooling — rules are edited
  in-file, there is no `--rules` selector or stats-diff across runs.
- **Pseudo-instruction invariance** (GOALS §5): asserted but not validated by a
  test (pseudo vs expanded → identical packets).
- **Extension-coverage scope:** GOALS states no target for which ISA extensions
  are decoded vs. handled by the unknown-opcode heuristic.

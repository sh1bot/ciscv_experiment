# TODO / Open questions

Items parked pending a decision on the intended behaviour.  Numbering is
stable — resolved items are deleted, leaving gaps — and follows the original
consistency review's reconciliation table.  Measurement records live under
`results/corpus/`; conventions in `ACCOUNTING.md`.

---

# A. The `encoding.yaml`-as-source-of-truth migration

The planning documents name `encoding.yaml` as the source of truth (see
`yaml_migration.md`).  These are the items that migration leaves open.

## A1 — open design decisions

4. **Is the chain temp architecturally x31, or any dead register?**  The yaml
   says "x31 becomes undefined"; `yaml_migration.md` says `tmp` is the
   compiler's own register, required dead.  These differ for code using x31.
5. **Wide `li`.**  Over 61033 `li` in five corpora: 68.8% fit 5 bits, 74.1%
   fit 6, 90.1% fit 8, 97.4% fit 10.  Current widths: 6 bits in
   `dual-indep-pair`, 7 in `chain-li-branch`, 10 in `li-czero-pair` and
   `mvload-jump-pair`.  The remaining question is whether the 8-10-bit tail
   deserves a dedicated frame or a lui-split, or is an accepted loss.
6. **`scheduler/RULES.md`'s future** — regenerated from the yaml, reduced to
   scheduler semantics only, or retired?  Its numeric limits have drifted from
   both the yaml and the code (see A4).
7. **Pairing-rate metric** — pairs *accepted*, or accepted *and encodable*?
   Nearly closed: `encoding_verify` puts 99.9% of checkable immediates inside
   their declared field.  The residual is `post-inc-pair` at 95.5%, where
   `rules.py` and the verifier disagree about the offset scale `k` on cases
   like `imm=96`; settle which side is right rather than patching either.
8. **Frame priority.**  `encoding_budget.py` and `encoding_verify.py` both
   `break` at the first accepting rule, so `RULES` list order determines every
   number they print.  Make it an explicit yaml property, or state in the docs
   that attribution order is significant and defined in `rules.py`.
9. **Pseudo-op canonicalization placement** (`li`/`mv`/`addi4spn`, P1–P5 in
   `yaml_migration.md`, explicitly TBD).  Defined today in three places:
   predicates in `isa/instruction.py`, `encoding_budget.subform()`, and the
   yaml's op vocabulary.  The yaml's op names are meaningless without them.
10. **Hardware-decoder opcode alignment** — `encoding_assign.py` orders frames
    by A-slot RISC-V format so leading identifier bits track real `opcode[6:2]`.
    Stated objective, or incidental nice-to-have?  (The yaml's "Enumeration
    policy" note now records it as intent.)
11. **`rd = x0/x2` sentinel — enforce it?**  Declared `status: active` in the
    yaml, but `rules.py` never checks it.  If enforced, `arith-jump`/
    `prologue`/`epilogue` could ride inside any rd-bearing frame's opcode word
    in the slice its `rd` cannot reach — roughly 68 codepoints at zero opcode
    cost.  Caveat: the host's `rd` column must hold a register in every row,
    so frames whose `rd` carries `immb[4:0]` cannot host.

## A2 — missing tooling

- **Schema validation for `encoding.yaml`**: grid `bits` sum to 32, rows span
  exactly 7 cells net of spans, row field names resolve to a grid column or
  declared operand, op clusters agree with templates, frame names unique.
  (Evidence it is needed: a row naming an unknown immediate field once cost a
  frame its displacement field, and a six-column row was caught only by a
  rendering exception.  Both are hard errors now, but only for those two
  cases.)
- **Regeneration gate**: `encoding.md` is generated and can silently drift
  from the yaml; require a zero-diff re-render in `tests/test_conformance.py`.
- **Cross-revision comparison**: a stats-diff across `encoding.yaml` revisions.

## A4 — `scheduler/RULES.md` drift

Depends on decision 6.  Two known-wrong classes: numeric limits that restate
(and now contradict) the yaml's, and descriptions that predate width-scaled
offsets.  Under single-source-of-truth the yaml keeps the numbers and RULES.md
should reference them rather than restate them.  Also absent from RULES.md and
PLAN.md entirely: the implicit chain-temp model (decision 4) and the `rd`
sentinel reservation (decision 11).

## A5 — design constraints that live only in tooling or scheduler code

Recorded here so they are not lost; each needs a home in the design documents.

- **Variable-length prefix code for frame identification** — canonical Huffman,
  DEFLATE-style, decoded MSB→LSB over `opcode5:funct3:g:h`, with a Kraft-greedy
  promotion policy.  Exists only in `encoding_assign.py`'s docstring.
- **Global slot discipline** — A executes before B; control transfers may only
  occupy the B slot; unknown instructions never pair; calls are excluded from
  jump frames.  This is packet execution semantics, not a heuristic.
- **Relocation and optimism policy** — `%pcrel_lo`/auipc-fed loads never pair;
  branch and jump displacements are deliberately *not* range-checked.  Same
  optimism as the RVC-eligibility ceiling.
- **Methodology constants** — the 90/95/99% coverage targets, p95 as the
  immediate-width statistic, the starvation and register-pressure thresholds in
  `encoding_budget.py`.  These define what "fits" means.
- **Encoding aliasing conventions** — `addi imm==0` passes because it encodes
  as `add rd, rs1, x0`; `arith-mem` excludes `imm==0` because it would encode
  as a move from x0.

## A6 — smaller yaml cleanups

- `level:` is undocumented — present on all frames, defined nowhere, and it
  drives markdown heading depth, so level-1 frames render as H1 siblings of
  section headers and break `encoding.md`'s outline.  Define it or decouple it.
- Two frames carry two rule names in one comma-joined string, which consumers
  split on `,` (`deref-chain-load-pair, base-chain-load-pair` and
  `load-sp-branch, load-base-branch`).  `post-inc-pair` shows the better
  pattern with an explicit `rules_py_names` list; the others should follow.
- `encoding_budget.py` iterates `RULES` from `rules.py` rather than the yaml,
  so its output is generated from a different source of truth than
  `encoding.md`.  Re-point it.

## A7 — tests

- `tests/test_pairing.py` hard-codes `rules.py`'s constants as accept/reject
  boundaries; every width that moves to the yaml invalidates the corresponding
  boundary test.  Decide: regenerate tests from the yaml (parameterized), or
  freeze them as a regression baseline?
- Pair-count expectations in `tests/test_scheduler.py` shift with any yaml
  iteration.
- RVC-eligibility tests measure the comparison baseline, not the new encoding.

## A8 — making the yaml actually singular

Every fact stated in the yaml and re-stated elsewhere is a drift waiting to
happen; `tests/test_conformance.py` gates what can be compared mechanically.
Remaining steps, in leverage order:

1. **Consume `scheduler/imm_contracts.py` in `rules.py`.**  Ten named width
   constants plus four inline ranges are hand-copies of yaml facts over 32
   call sites.  Replacing them with `width_of(rule, slot, mnemonic)` leaves one
   number instead of two.  Blocker: a rule's check does not know its own name;
   give `PairingRule` a back-reference and it falls out.
2. **Schema validation** (A2 above).
3. **Extend `rules_conform`'s probe reach.**  The unverified immediate
   contracts are frames whose pair shape the probe cannot construct (e.g.
   `mem-pair`'s two same-mnemonic accesses one width apart).  Per-frame probe
   hints in the yaml would close it.
4. **Regeneration gate** (A2 above).

## A10 — `addi-branch-pair` schedules only unencodable pairs

Found while measuring the chain-li/addi-branch fold: `_addi_branch_pair`
never requires the branch to compare against ZERO, and its B set lists only
the two-register spellings (`beq`..`bgeu`), not the aliases.  The measured
consequence, over musl-rv32 + sqlite-rv64:

  * 438 of 438 scheduled B slots compare the sum against a REGISTER
    (`bge s6, a0`), which the row cannot encode -- it draws no rs2 field.
    Every pair this frame reports is phantom.
  * The population the frame was designed for (`addi rsda; beqz/bnez rsda`,
    per its own template) is never matched, because `beqz`/`bnez`/`bltz`/
    `bgez` are absent from the B set.  That true population is small:
    ~45 adjacent occurrences per corpus.

Options: fix to the designed vs-zero form (~90 honest pairs across two
corpora, fits a block of 8-16); redesign with an rs2b field at the cost of
halving the branch displacement to 5 bits (unmeasurable fit -- displacements
are unresolved); or discard.  See the immediate-size analysis conversation:
the register-compare population is real and valuable but needs a different
row, and folding with chain-li-branch only serves the vs-zero premise.

## A9 — final sp/base split: `load-sp-branch` / `load-base-branch`

The one frame still drawing an undiscriminated SP-relative row.  Its two rules
already separate the traffic; the fix mirrors `mem-pair-sp`: give the sp side
its own frame on the XLEN-switchable ops (`lx` covers 62–89% of its loads;
measured mix `lw 89%/lbu 9%` on rv32, `ld 62%/lw 22%/lbu 16%` on rv64) and
decide whether the narrower op set is worth the 10-bit offset field, which its
traffic does need (5-bit fit is only 66–69%).

---

# B. Pre-existing items

## 3 — `stamp_solo_reasons` is never wired into the pipeline
`scheduler/pairing.py:stamp_solo_reasons()` (and the per-rule `diagnose_a`/
`diagnose_b` callbacks it drives) computes partner-independent solo reasons
such as "immediate out of range".  It is only called from tests;
`__main__.py:_process_chunk` calls `stamp_slot_eligibility` but not
`stamp_solo_reasons`, so a solo instruction with no eligible neighbour can be
emitted with a bare `{solo}` and none of the structural reasons GOALS §1
promises.  **Decision needed:** wire it in, or soften GOALS §1.

## 5 (held) — PLAN documentation of scheduler internals
Still undocumented in PLAN, held until their intended behaviour is settled:
- `diagnose_a` / `diagnose_b` fields on `PairingRule` (and `stamp_solo_reasons`).
- `_backward_pair()` branch-rescue second pass (`scheduler/pairing.py`).
- `STALL_FOR_PAIR` list-scheduler heuristic and the `--no-stall-for-pair` flag.

## 6 — PLAN attributes CFG/function logic to the wrong module
PLAN §6 / §28 describe `analysis/cfg.py` as building CFG edges and identifying
functions, but that logic lives in `analysis/parser.py` (`parse_file`);
`cfg.py` holds only the dataclasses.  **Decision needed:** update PLAN §6.

## 7 — `--verbose` is barely read
`__main__.py` defines `-v/--verbose`; PLAN §13 documents detailed behaviour
("show all candidate pairs ... to stderr") that is not implemented — today it
gates only the XLEN-detection warning.  Implement or reduce the PLAN text.

## 12 — GOALS dataflow wording is narrower than the code
GOALS §2 phrases the reordering constraint as RAW-only; `analysis/depgraph.py`
enforces RAW + WAR + WAW plus memory and barrier edges.  Broaden the wording.

## 14 — code cleanups
- `is_jump` docstring (`isa/instruction.py`) says it excludes calls/returns,
  but the predicate returns True for any `jal`/`jalr`.  Reconcile.

## 15 — goals with no supporting tool/test/scope
- **Pseudo-instruction invariance** (GOALS §5): asserted but not validated by
  a test (pseudo vs expanded → identical packets).
- **Extension-coverage scope:** GOALS states no target for which ISA
  extensions are decoded vs handled by the unknown-opcode heuristic.

## Accounting conventions
See `ACCOUNTING.md` for the measurement conventions behind every corpus number.
Two open questions that block frame sizing:
- **Corpus ISA mismatch.**  The corpus is near-balanced by instruction count
  (47.3% RV32) but the mnemonic skew is extreme: `lw` is 87% RV32-sourced and
  `sw` 89%, because an RV64 build uses `ld`/`sd` for anything pointer-sized.
  The `ld/lw/sd/sw` clusters are sized against a blend matching no single
  target; the XLEN-switchable ops (`lx`/`sx`) are the start of the answer.
- **Chain and dual slot populations differ** — unary ops are 40.9% of
  independent-pair slot occupancy against 12.6% of chain — and now have
  separate op-set anchors sized separately.

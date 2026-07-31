# TODO / Open questions

Deferred items from the GOALS/PLAN/CODE consistency review.  These are parked
pending a decision on the intended behaviour — they are *not* yet settled, so no
code or doc change has been made for them.  Numbering follows the review's
consolidated reconciliation table.

Every number below was re-derived against the current tree and the current
17-file corpus; `results/corpus/REMEASURE.md` holds the evidence and lists what
changed.  Items that the remeasurement retired have been deleted rather than
annotated — a stale number in a live document argues the wrong way.

---

# A. The `encoding.yaml`-as-source-of-truth migration

The planning documents now name `encoding.yaml` as the source of truth (see
`yaml_migration.md` for the governing plan).  These are the items that migration
leaves open.

## A1 — open design decisions, in rough dependency order

1. **`g`/`h` semantics.**  Three mechanisms coexist in the yaml: the Overview
   says g/h extend `funct3` (opcode bits); two per-frame notes say they extend
   `immb` (immediate bits); the codepoint model prices wide immediates at
   `2^ext` and never mentions g/h.  Pick one and rewrite the Overview bullet.
   Blocks A2 below.
2. ~~**The 16×16 ALU op-lists / codepoint overflow.**~~  RETIRED — the frames
   were re-optimised and carved apart; `encoding_assign.py` now reports
   1004/1024 reserved with 20 spare and exits 0.  A3's measured options were
   taken against the superseded op-lists and are recorded there as history.
3. ~~**`mvload-jump-pair` has no yaml frame.**~~  RETIRED — the frame exists
   (`encoding.yaml:816`) and `rules_conform` reports no unframed rule.
4. **Is the chain temp architecturally x31, or any dead register?**  The yaml
   says "x31 becomes undefined"; `yaml_migration.md` says `tmp` is the
   compiler's own register, required dead.  These differ for code using x31.
5. **Wide `li`.**  Now measured on the population rather than the pack rate:
   over 61033 `li` in five corpora, 68.8% fit 5 bits, 90.1% fit 8, 97.4% fit
   10.  `dual-indep-pair` caps A-slot `li` at 5 bits, so it turns away 31.2% of
   them — and since the cap is enforced up front they no longer show as
   unencodable packed pairs (this is the −752 pairs the cap cost on two
   corpora).  Dedicated frame, lui-split, wider field, or accepted loss?
6. **`scheduler/RULES.md`'s future** — regenerated from the yaml, reduced to
   scheduler semantics only, or retired?  Its numeric limits have drifted from
   both the yaml and the code (see A4).
7. **Pairing-rate metric** — pairs *accepted*, or accepted *and encodable*?
   Much less material than it was: the verifier now puts 99.2% of checkable
   immediates inside their declared field (was 79.7%).  What remains is
   concentrated, not spread — `pre-inc-pair` fits only 35.1%, `post-inc-pair`
   93.1%, `arith-mem-pair` 93.6% — so the honest fix is per-frame, and
   `pre-inc-pair` should be re-costed before it is trusted (it is also the
   frame GCC leans on hardest, see `results/corpus/GCC.md`).
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
    Stated objective, or incidental nice-to-have?
11. **`rd = x0/x2` sentinel — enforce it?**  Declared `status: active` in the
    yaml, but `rules.py` never checks it (`exclusive_rd` treats x0 as a
    non-collision, and x2 sits inside the x0–x15 window `rsd-alu` accepts).
    See A3 for what the sentinel is worth if enforced.

## A2 — missing tooling

- **Schema validation for `encoding.yaml`**: grid `bits` sum to 32, rows span
  exactly 7 cells net of spans, row field names resolve to a grid column or
  declared operand, op clusters agree with templates, frame names unique.
- **Regeneration gates**: no obligation to re-render `encoding.md` to a zero
  diff, or to re-run the budget, after a yaml edit.  See VALIDATION Technique 5.
- **Cross-revision comparison**: the comparison axis is now `encoding.yaml`
  revisions; the missing tool is a stats-diff across them (supersedes the
  `--rules` selector framing in item 15 below).

## A3 — measured input to decision 2 (HISTORICAL)

Decision 2 is retired and the op-lists these numbers were taken against no
longer exist (`rsd_alu` is now 10 ops, `load-chain-alu` carries `lw`/`ld`
only).  Kept because the *method* and the shape of the trade-offs still apply,
not because the figures are current.

Reserved blocks total 1036 against a 1024 namespace — 12 over.  Blocks are
rounded up to powers of two, so *trimming a few ops frees nothing*: a 15×16 ALU
frame still reserves a 256 block.  Only halving a block, or removing one,
actually recovers space.  Three measured options (corpus: godot.s + testcase0.s,
op-sets first widened to a generous RV64 I/M/Zba/Zbb/Zbs set so the current
16-op list does not censor the counts, then tiled with `util/biclique_tiling.py`
on held-out Poisson-thinned halves):

| Option | Frees | Measured cost |
|---|---:|---|
| `rsd-alu` 16×16 → 16×8 (cut the **B-side** only) | 128 | held-out yield 97.96% → 95.66% (−2.3pp) |
| `load-chain-alu` 3×16 → 2×16 (drop one of `lw`/`lwu`/`ld`) | 64 | loses that load's share; `lw` is smallest at 10/55 pairs (18%) |
| Host `arith-jump`/`prologue`/`epilogue` in the `rd = x2` sentinel slice | 68 | none — an accounting fix, not a design cut (requires decision 11) |

Notes:
- `rsd-alu` halves cheaply because its B-side is dominated by `li` (81% of
  B-slot uses); its A-side is diverse and should stay 16 wide.  `chain-alu` is
  the opposite — halving it costs 83.6% → 73.6% (−10pp), so it should stay 16×16.
- The widened experiment showed the current op-list is genuinely missing
  earners: `srliw`, `maxu`, `sltu`/`sltiu`, `xori`, `slli.uw`, `sh1add`/`sh2add`
  all place inside the best rectangle.  Reshaping the op-lists is worth doing on
  its own merits, not only to save codepoints.
- All three loads in `load-chain-alu` need >5 offset bits about half the time
  (`ld` 33%, `lw` 50%, `lwu` 48%), so the 6-bit extension cannot be selectively
  dropped from one of them instead.
- The sentinel option is the only free one.  The yaml already declares that `rd`
  may not name x0 or x2 when it holds a register, precisely so those bit
  patterns can select the prologue/epilogue/jump marker formats — but
  `encoding_assign.py` still gives those three frames their own opcode blocks,
  so the reservation is paid for and not spent.  A frame with no `rd` field of
  its own can ride inside any rd-bearing frame's opcode word, in the slice that
  frame's `rd` cannot reach, at zero opcode cost.  Caveat: the host's `rd`
  column must hold a *register* in every row — words where `rd` carries
  `immb[4:0]` (e.g. `store-chain-alu`, `pre-inc`) cannot host a sentinel.

## A4 — `scheduler/RULES.md` drift

Depends on decision 6.  Two known-wrong classes:

- **Wrong about the code**: `RULES.md:339–340` and `:922–923` still describe the
  load-branch offset as "10-bit unsigned byte offset (`uimm10`, *unscaled*)"
  after `rules.py` moved to width-scaled offsets.
- **Wrong about the yaml**: roughly a dozen numeric constraints conflict —
  `rsd-alu` imm −64..64 vs the drawn field; `mem-pair` "8-bit sp / 5-bit base"
  vs the yaml's 6-bit base / 10-bit sp; `load-base-branch` "5-bit unsigned byte
  offset (0–31)" vs width-scaled; `chain-li-branch` 8-bit vs 6+1;
  `addi-branch-pair` 8-bit vs 6-bit; `prologue`/`epilogue` 7-bit ×16 vs 10-bit
  ×16, plus prologue's ra-only store restriction vs the yaml's free `rs1b`.
  Under single-source-of-truth the yaml keeps the numbers and RULES.md should
  reference them rather than restate them; the §4 summary table's "Key limits"
  column should become frame references.

Also absent from RULES.md and PLAN.md entirely: the implicit chain-temp model
(decision 4) and the `rd` sentinel reservation (decision 11).

## A5 — design constraints that live only in tooling or scheduler code

Recorded here so they are not lost; each needs a home in the design documents.

- **Variable-length prefix code for frame identification** — canonical Huffman,
  DEFLATE-style, decoded MSB→LSB over `opcode5:funct3:g:h`, with a Kraft-greedy
  promotion policy.  Exists only in `encoding_assign.py`'s docstring.
- **Global slot discipline** — A executes before B; control transfers may only
  occupy the B slot; unknown instructions never pair; calls are excluded from
  jump frames.  This is packet execution semantics, not a heuristic.
- **Relocation and optimism policy** — `%pcrel_lo`/auipc-fed loads never pair;
  branch and jump displacements are deliberately *not* range-checked.  The yaml
  says `unbounded: true` for branches but never says direct `j`/`jal` targets
  are also unchecked.  Same optimism as the RVC-eligibility ceiling.
- **Methodology constants** — the 90/95/99% coverage targets, p95 as the
  immediate-width statistic, the "5-bit + g + h = 7 bits" starvation threshold,
  the 95%/99.5% register-pressure thresholds.  These define what "fits" means.
- **Encoding aliasing conventions** — `addi imm==0` passes because it encodes as
  `add rd, rs1, x0`; `arith-mem` excludes `imm==0` because it would encode as a
  move from x0.  Related to the settled zero-immediate rule but not identical.
- **Prose is load-bearing for two tools** — `encoding_assign.wants_gh()` regex-
  greps frame `notes` for phrases like `` `g` … extend ``, and
  `encoding_render.lint()` suppresses a missing-operand error when the notes
  mention the immediate by name.  Both results depend on note *wording*, which
  is fragile for a source of truth.

## A6 — smaller yaml cleanups

- `level:` is undocumented — present on all frames, defined nowhere, and it
  drives markdown heading depth, so level-1 frames render as H1 siblings of
  section headers and break `encoding.md`'s outline.  Define it or decouple it.
- Two frames carry two rule names in one comma-joined string, which consumers
  split on `,` (`deref-chain-load-pair, base-chain-load-pair` and
  `load-sp-branch, load-base-branch`).  `post-inc-pair` shows the better
  pattern with an explicit `rules_py_names` list; the others should follow.
- `encoding_budget.py` still iterates `RULES` from `rules.py` rather than the
  yaml, so `encoding_budget.md` is generated from a different source of truth
  than `encoding.md`.  The two no longer disagree in their conclusions — both
  now say the namespace fits, and the frame sets match — so this is a
  structural risk rather than an active contradiction.  Re-point it anyway:
  the agreement is a coincidence of the two being edited together.

## A7 — tests

- `tests/test_pairing.py` hard-codes `rules.py`'s constants as accept/reject
  boundaries.  Every A4 conflict resolved in the yaml's favour invalidates the
  corresponding boundary test.  Decide: regenerate tests from the yaml
  (parameterized), or freeze them as a pre-migration regression baseline?
- Pair-count expectations in `tests/test_scheduler.py` shift with any yaml
  iteration.
- RVC-eligibility tests measure the comparison baseline, not the new encoding —
  the docs should state this axis is out of the yaml's scope.

---

# B. Pre-existing items

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
- `is_jump` docstring (`isa/instruction.py`) says it excludes calls/returns, but
  the predicate returns True for any `jal`/`jalr` including those.  Reconcile the
  docstring with the predicate (or the predicate with the docstring).

## 15 — goals with no supporting tool/test/scope
- **Cross-revision comparison** (GOALS §0.4 / §1): no tooling.  Superseded by
  §A2 — the comparison axis is `encoding.yaml` revisions, not rule sets, so the
  missing piece is a stats-diff across revisions rather than a `--rules`
  selector.
- **Pseudo-instruction invariance** (GOALS §5): asserted but not validated by a
  test (pseudo vs expanded → identical packets).
- **Extension-coverage scope:** GOALS states no target for which ISA extensions
  are decoded vs. handled by the unknown-opcode heuristic.

## Accounting conventions
See `ACCOUNTING.md` for the measurement conventions behind every corpus number
(pairing rate, pack rate, codepoint demand, op-set yield) and its own register of
open questions.  Two that block frame sizing:
- **Corpus ISA mismatch — WORSE than recorded, not stale.**  The corpus is now
  17 files and near-balanced by instruction count (47.3% RV32), but the mnemonic
  skew grew: `lw` is 87% RV32-sourced and `sw` 89%, because an RV64 build uses
  `ld`/`sd` for anything pointer-sized.  Balancing by instruction count cannot
  fix this.  The `ld/lw/sd/sw` clusters in `post-inc-pair` / `mem-pair` /
  `pre-inc-pair` are still sized against a blend matching no single target.
  (ACCOUNTING §1)
- **Chain and dual want different op sets:** remeasured at 40.9% of
  independent-pair slot occupancy against 12.6% of chain (was 65.4%/2.9%).  The
  asymmetry stands at 3.2x; the conclusion drawn from it does not, since the
  carve-out gave the two frames separate anchors.  (ACCOUNTING §5)

## A7 — the g/h immediate-extension mechanism is unfunded across the yaml

`util/encoding_assign.py` now checks two things it previously only computed,
and both find pre-existing problems.  Neither is a new regression; both were
invisible while the checks were absent.

**Eight frames claim a `g`/`h` immediate bit that does not exist.**  A frame can
only carry an immediate in `g` or `h` if its selector word STOPS above that bit
— identifier plus op-select must leave it free, which needs depth <= 9 for `h`
and <= 8 for both.  Every frame is at depth 10/10, so no frame has either bit.
`GH_FREE_DEPTH` / `H_FREE_DEPTH` encoded exactly this and were dead constants;
they are now enforced and report: `chain-li-branch`, `chain-bit-test-branch`,
`load-sp-branch`/`load-base-branch`, `addi-branch-pair`, `mem-pair`,
`mvload-jump-pair`, `arith-mem-pair`.

**Two frames take field width without declaring it.**  `_slot_weight` charges
`2^(bits - base)` only for an op with an explicit `imm: {bits}`; a BARE op
inherits the row's whole span for free.  `lint_frame` reports:

  * `chain-bit-test-branch` slot a — `andi` bare against a 6-bit drawn field;
  * `mem-pair` slots a and b — all eleven load/store ops bare against 6 bits.

`mem-pair` is the expensive one and needs a decision, not a patch.  Declaring
6 bits on all eleven ops makes each singleton cluster weigh 2x2, so demand goes
11 -> 44 and the block 16 -> 64: **+48 codepoints against 12 spare**.  But that
figure is itself suspect, because `mem-pair`'s immediate is SHARED between the
slots (one `imm` field, not `imma`/`immb`), and the model multiplies the two
slot weights as if the extension were bought twice.  A shared field should pay
`2^ext` once.  So the options are: teach `opcode_codepoints` about shared
immediates, narrow the row to 5 bits and measure the pair cost, or fund it.

Until one of those lands, `mem-pair`'s 6-bit offset and `chain-bit-test-branch`'s
6-bit `andi` are ranges we are reporting but have not paid for.

THE RULE, so this stops recurring: **an immediate field is 5 or 10 bits drawn
from register columns; extra range is bought by REPEATING THE OPCODE, declared
as `imm: {bits}` on the op.**  Never both — drawing the extra bit AND declaring
the width pays for the same bit twice, which a revision of `dual-indep-pair`
briefly did.

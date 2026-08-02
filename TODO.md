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

4. **Which register is the chain temp?**  DECIDED in part: it is a FIXED
   architectural register, not "any dead register" — the compiler must know
   which register a packet may corrupt, and an exception taken mid-packet
   needs somewhere to save the intermediate from.  An implementation that
   corrupts nothing is conforming.  Still open: WHICH register.  x31 is the
   default, but RVE (16 registers) has no x31 and needs another — x7 is the
   candidate.  Settle before any ABI claim; `yaml_migration.md`'s
   "compiler's own register, required dead" wording is now superseded.
5. **Wide `li`.**  Over 61033 `li` in five corpora: 68.8% fit 5 bits, 74.1%
   fit 6, 90.1% fit 8, 97.4% fit 10.  Current widths: 6 bits in
   `dual-indep-pair`, 8 in `chain-li-branch`, 10 in `li-czero-pair` and
   `mvload-jump-pair`.  The remaining question is whether the 8-10-bit tail
   deserves a dedicated frame or a lui-split, or is an accepted loss.
8. **Frame priority.**  DECIDED: default to YAML ORDER, but make it
   experimentally flexible — the scheduler should let a rule be promoted or
   demoted so the effect on attribution and totals can be observed.  Today
   `encoding_budget.py` and `encoding_verify.py` both `break` at the first
   accepting rule, so `RULES` list order silently determines every number
   they print (this is why the arith-mem kill measured against a shifting
   attribution snapshot).  To build: derive rule order from the yaml, and add
   a priority override (CLI and/or API) that reorders without editing either
   source.
9. **Pseudo-op canonicalization placement** (`li`/`mv`/`addi4spn`, P1–P5 in
   `yaml_migration.md`, explicitly TBD).  Defined today in three places:
   predicates in `isa/instruction.py`, `encoding_budget.subform()`, and the
   yaml's op vocabulary.  The yaml's op names are meaningless without them.
10. **Hardware-decoder opcode alignment** — DECIDED: an OBJECTIVE, held
    until it cannot be achieved, at which point it degrades to a nicety for
    the frame that broke it — and that concession is stated explicitly at the
    point it is made, never by quiet reordering.  Recorded in the yaml's
    "Enumeration policy" note.
11. **`rd = x0/x2` sentinel** — DECIDED: ENFORCE.  `rules.py` must reject
    `rd` in {x0, x2} wherever a row draws a register there, which makes the
    sentinel real and unlocks hosting: a sentinel-selected frame
    (`arith-jump`/`prologue`/`epilogue`) can ride inside another rd-bearing
    frame's opcode word in the slice that frame's `rd` cannot reach —
    roughly 68 codepoints at zero opcode cost.  A frame may host only if its
    `rd` column holds a register in EVERY row; frames whose `rd` carries
    `immb[4:0]` have no unreachable slice to lend.  To build: the rd check in
    rules.py, host eligibility in `encoding_assign.py`, and a remeasure (the
    check can only cost pairs; the codepoints are the return).

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
- One frame still carries two rule names in one comma-joined string, which
  consumers split on `,` (`deref-chain-load-pair, base-chain-load-pair`).
  `post-inc-pair` shows the better pattern with an explicit `rules_py_names`
  list.  (`load-sp-branch, load-base-branch` was resolved by the A9 split.)
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

## A8 — making the yaml actually singular (largely done; residue below)

Every numeric width in `rules.py` now derives from the yaml at import
(`_w(rule, slot, op)` over `scheduler/imm_contracts.py`), and per-frame
`probe:` hints let `rules_conform` verify 55 of 56 declared contracts (98%),
including the scaled and coupled-immediate shapes it could never reach.
What remains, deliberately:

- **Row-level narrowings are not per-op facts**: mvload-jump's direct-j row
  narrows li to 5 bits and drops the load offset.  (The other instance, the
  SP-relative chain rows, dissolved with the A9 split.)  Stays a documented
  literal until contracts can attach to rows.
- **`chain-bit-test-branch a:andi` is unverifiable by interval compare**:
  its accepted set is powers of two and masks, not a range.  Covered by
  `tests/test_pairing.py` boundary tests instead.
- Mnemonic SETS and mode tables (`_INC_MODES`/`_DEC_MODES` restate
  inc-branch-pair's joint clusters) could derive the same way widths now do.

## A11 — the corpus is shaped by its compiler's cost model (third instance)

Non-unit loop steps are substantially disguised pointer bumps.  Measured over
four corpora: unit-step counters are memory bases only 5.9% of the time, but
power-of-two-step counters are 26.1% (a floor — the scan sees one basic
block), and where a stepped counter IS a base, the step relates to the access
width 93% of the time (76 sites step == width, 110 step = N x width from
unrolling).  Compilers strength-reduce to pointer bumps because plain RISC-V
charges an instruction for indexed addressing; under this encoding
`shXadd+load` is one packet (`index-chain-mem-pair`) and `inc + bXX` is one
packet, so element counting costs the same packets and the reduction buys
nothing.  The non-unit tail is therefore reachable value behind a compiler
tuning, joining the RVC register-clustering tax and the clang/GCC gap as
cost-model artefacts in the corpus.  Test: rebuild with LSR/ivopts damped
(`-mllvm -disable-lsr` / `-fno-ivopts`) and remeasure the step census and
`index-chain-mem-pair`.

Related measured design input, from the same session: a unit-step
increment/decrement-and-branch frame (`inc/dec[w] rsd ; bXX rsd, rs2b, L`,
10-bit packet displacement, rs2b=x0 giving the vs-zero forms free) captures
~330 scheduled pairs on musl-rv32+sqlite-rv64 with ~100% displacement fit,
and FOUR compare modes — beq, bne, blt(sum,r), bge(sum,r) — cover 81% of the
demand in a 16-block at ~16 pairs/cp.  The four unsigned modes add 16% for
another 16 codepoints (~4/cp, below floor); sum-second signed compares are
under 3% and never worth encoding.

**Test result (musl-1.2.5 rv32 `-O2` no-C, `-mllvm -disable-lsr`, against the
matched `musl-norvc-rv32`): the hypothesis holds and the retuning is free.**
Instructions 118445 vs 118755 (LSR off is 0.26% SMALLER), total pairs 26801
vs 26913 (flat), but adjacent `inc/dec+bXX` sites rise 345 -> 397, unit-step
sites 125 -> 185 (+48%), and the unit-step frame's scheduled pairs 99 -> 191
(+93%).  Power-of-two-step share stays flat (~8%): the migrated mass came out
of the wide-step tail, i.e. LSR's strength-reduced pointer forms.  Damping
induction-variable rewriting doubles the unit-step frame's population at no
size cost.

Follow-on census findings (adjacent sites, musl-rv32 + sqlite-rv64):

* **Do not widen the step set to +/-2,4,8**: it adds only 8-9 points of
  site coverage (~30 sites/corpus) for a 4x block (16 -> 64, +48 cp),
  ~1.3 pairs/cp — and the nolsr result says that population is better
  migrated by tuning than encoded.
* **Compare mode strongly correlates with step direction**: down-loops are
  bltu/bgeu-heavy (64% of musl down-sites; pointer-vs-limit, both operand
  orders), up-loops are beq/bne + bge/bgeu.  Enumerating the 16-block as
  the best 16 JOINT (direction x mode) cells instead of a 4-mode x 2-dir
  product raises adjacent-site coverage from ~79% to 98.7% at identical
  cost — non-product clusters are already expressible in the yaml.
* **inc vs incw**: on rv64, `addiw` is 42-69% of unit sites.  Signed
  counters are provably width-equivalent (overflow is UB) and a cost model
  that prices `incw` as unpairable would migrate them to `addi`; unsigned
  32-bit counters have defined wrap and are NOT provable in general.
  Dropping the w forms halves the block; the unprovable residue simply
  forgoes the pairing (solo `addiw` + branch), losing the optimisation,
  never correctness.

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

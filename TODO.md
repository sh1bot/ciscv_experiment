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
11. **Row layout should name fields, not columns** — DONE (2026-08-04).  `grid` should enumerate the essential fields with their official
   bit positions — `rd`, `funct3`, `rs1`, `rs2`, `funct5`, `opcode5`, `g`, `h`
   — and a row should then be a mapping, `{rd: foo, rs1: bar, ...}`, with any
   field left unset being free for opcode assignment.  A field split into
   sub-parts states them explicitly:

       rd: [{bits: 2, value: "imma[6:5]"}, {bits: 3, value: "rda"}]

   This replaces the positional cell list, the `*2` span notation and the
   `imma[6:5]+rda[2:0]` packing hack; `"0 0 0 1 0"` becomes `rd: unused` with
   the enumerator allocating the pattern from its reserved pool.  `g` and `h`
   are no longer writable by a row at all — they are opcode bits.

10. **Decoder alignment is an OBJECTIVE, not a coincidence.**
   `encoding_assign.py` orders frames by A-slot RISC-V format so leading
   identifier bits track real `opcode[6:2]`.  Hold it while it can be held; if
   a future assignment cannot satisfy it AND fit the namespace, fitting wins
   and the alignment degrades to a nicety for that frame — say so explicitly
   at the point where it is given up, rather than quietly reordering.

5. **Wide `li`** — CLOSED: an ACCEPTED LOSS.  Over 61033 `li` in five
   corpora: 68.8% fit 5 bits, 74.1% fit 6, 90.1% fit 8, 97.4% fit 10, against
   widths of 6 (`indep`), 8 (`li-branch-chain`), 10 (`li-czero`,
   `setup-jump`).  A `lui`+`addi` pair frame is endorsed by four independent
   sources (FRAMES.md §3) but cannot work here: 32 bits of constant against a
   20-bit operand budget.  Base RISC-V's own split of wide constants was made
   on sound grounds — if a 15-bit form were worth having, it would already
   exist — so the tail stays solo and is not second-guessed.
8. **Frame priority.**  DECIDED: default to YAML ORDER, but make it
   experimentally flexible — the scheduler should let a rule be promoted or
   demoted so the effect on attribution and totals can be observed.  Today
   `encoding_budget.py` and `encoding_verify.py` both `break` at the first
   accepting rule, so `RULES` list order silently determines every number
   they print (this is why the arith-mem kill measured against a shifting
   attribution snapshot).  To build: derive rule order from the yaml, and add
   a priority override (CLI and/or API) that reorders without editing either
   source.
9. **Pseudo-op canonicalization** — DECIDED: the yaml owns it.  A top-level
   `pseudo_ops:` section states each name's base opcode, its single canonical
   `encode` form, and the list of `match` spellings the scheduler accepts on
   input (liberal in, strict out — and note `mv` ENCODES as `add rd, x0, rs`,
   never `addi rd, rs, 0`, because this encoding has no immediate-zero
   codepoint).  `rules_conform` now reads it instead of keeping a private
   table.  Remaining: have `isa/instruction.py`'s predicates and
   `encoding_budget.subform()` read it too, so the last two copies go.
10. **Hardware-decoder opcode alignment** — DECIDED: an OBJECTIVE, held
    until it cannot be achieved, at which point it degrades to a nicety for
    the frame that broke it — and that concession is stated explicitly at the
    point it is made, never by quiet reordering.  Recorded in the yaml's
    "Enumeration policy" note.
11. **`rd = x0/x2` sentinel** — DONE.  Enforced in `rules.py` (derived from
    the yaml: the frames that owe the reservation are exactly those whose
    rows draw a destination in the rd column) at zero measured cost —
    musl-rv32 27191 -> 27192, sqlite-rv64 40850 -> 40851, both attribution
    reshuffles.  `encoding_assign.py` then hosts the three sentinel frames
    inside `alu-alu-chain`'s block, two guest identities per lent codepoint:
    **986 -> 918 reserved, spare 38 -> 106**.  (Superseded twice since: a
    guest names ONE pattern, so it pays a codepoint per op rather than half
    of one, and a fourth guest has joined — see the tool's own report for the
    current reservation.)  Each guest's op tables now fit its codepoints with
    rd held at the pattern its rows draw, which is what
    `encoding_assign.py --check-tables` gates.

## A5 — design constraints that live only in tooling or scheduler code

Recorded here so they are not lost; each needs a home in the design documents.

- **Variable-length prefix code for frame identification** — canonical Huffman,
  DEFLATE-style, decoded MSB→LSB over `opcode5:funct3:g:h`, with a Kraft-greedy
  promotion policy.  Exists only in `encoding_assign.py`'s docstring.
- **Global slot discipline** — A executes before B; control transfers may only
  occupy the B slot; unknown instructions never pair; calls are excluded from
  jump frames.  This is packet execution semantics, not a heuristic.
- **Relocation and optimism policy** — a `%pcrel_lo`/auipc-fed offset pairs
  only where the slot's field spans the full 12-bit residue (declared
  `accepts_pcrel_lo`, magnitude unchecked, alignment checked); narrower
  slots refuse it.  Branch and jump displacements are deliberately *not*
  range-checked.  See ACCOUNTING.md §8.
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
- ~~One frame still carries two rule names in one comma-joined string~~ DONE.
  No frame does now: `deref-load-chain, base-load-chain` was split (it shared
  one op-select with nothing selecting between its rows) and its halves have
  since been redrawn as `load0-load10-chain` / `load5-load5-chain`.
  `load-sp-branch-pair, load-base-branch-pair` went earlier, in the A9 split.
  `post-inc-pair`'s explicit `rules_py_names` list remains the pattern to use
  if a frame ever needs two rules again.
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

- **Row-level narrowings are not per-op facts** — the hazard is now FENCED
  (2026-08-06): `row_contract_complaints` (encoding_render, wired into
  `lint_frame` and so into the codepoint-accounting commit gate) computes,
  per declared-width op, the widest row able to hold that op's operands —
  learned from the frame's own template lines, placeholder heads included —
  and complains when `imm_field_bits`' widest-row pricing assumes more.  The
  documented example (a 6-bit LOAD offset in `setup-jump-pair`, whose load
  row also fields `rda`, `rs1a` and `rs1b` and is full at 20 bits while its
  `li` row draws imma 10) is pinned as a must-catch in
  tests/test_conformance.py.  Still open, deliberately: the PRICER itself
  stays widest-row — safe now only because the lint refuses any declaration
  it would misprice — and bits riding register-restricted split rows
  (dual-setup's a0-a7 bands) are still under-counted by the model, which is
  the A12 budget-rule item.  Ops no template line spells (dual-setup's
  `addi4spn`) cannot be judged and are skipped, as in the correspondence
  lint.  (setup-jump's direct-j row separately narrows `li` to 5 bits and
  drops the load offset; the SP-relative chain rows dissolved with A9.)
- **`bit-test-branch-chain a:andi` is unverifiable by interval compare**:
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
`shXadd+load` is one packet (`index-mem-chain`) and `inc + bXX` is one
packet, so element counting costs the same packets and the reduction buys
nothing.  The non-unit tail is therefore reachable value behind a compiler
tuning, joining the RVC register-clustering tax and the clang/GCC gap as
cost-model artefacts in the corpus.  Test: rebuild with LSR/ivopts damped
(`-mllvm -disable-lsr` / `-fno-ivopts`) and remeasure the step census and
`index-mem-chain`.

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

## A12 — findings from the 2026-08-05 yaml/rules conformance review

Open items only; the closed ones (backward-pass dependence safety, pcrel
guards on every memory slot, epilogue's phantom jalr offset, prologue's
undeclared ra-only restriction, pre-inc's shXadd operand direction, the
bit-test and arith-jump template ghosts) were fixed in the review commit.
The bare-`jalr` rd under-specification in the jump frames was closed on
main: `jr_any` was added as the non-linking spelling, arith-jump-pair and
setup-jump-pair declare [jalr_link_ra, jr_any] (setup-jump rebudgeted
16 -> 32), epilogue-pair gets `jr_any` only, and `_guard_link_regs`
enforces closed slots.

- **dual-setup-pair fails the yaml's own budget sanity rule** by the model's
  count (11 not in (16, 32]); the hand count ~19 is in range.  The
  widest-row coarseness of `opcode_codepoints` is the known cause (A8's
  row-contract item).  Either teach the pricer rows or mark the frame
  exempt, so the header's (budget/2, budget] claim is enforceable again.
- **The rd-sentinel note overstates enforcement.**  `doc.reserved` says
  rules.py rejects x0/x2 "wherever a row draws a register" in the rd
  column; `imm_contracts.rd_column_slots` (deliberately) covers only
  DESTINATIONS, so a source there — addi-store-chain's `rbase`,
  pre-inc-pair's `rs2b` on its addi store row — may still be x0/x2
  (`sw zero, 0(sp)` is real traffic).  Harmless while those frames host no
  guests, since the sentinel is only decoded inside a host's block, but the
  note and the hosting precondition ("a REGISTER in every row") should say
  which registers count.

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


## Immediate widths — priced, not yet applied (2026-08-04)

From `results/corpus/IMMEDIATES.md`, against 186 spare codepoints:

- **Buy (44 cp total):** `post-inc-pair` B +1 (+4 cp, 83.6->93.1%),
  `pre-inc-pair` B +1 (+8, 84.8->99.3%), `addi-store-off-chain` B +1
  (+16, 72.1->97.9%), `load-store-chain` A +1 (+16, 87.8->96.1%).
- **Ranked below those:** `load-base-branch-pair` A +2 (+42 cp) — it widens
  the LOAD OFFSET, and the 5-bit displacement is what binds that frame.
- **Refuse:** the ALU family. `rsd-alu-pair` A +2 costs 768 codepoints for
  9.7 points; `alu-alu-chain` +384 for 18.4; `arith-jump-pair` +192 for 12.6.

## In the back pocket

- **`rsd-alu-pair` order-canonicalisation** frees 120 codepoints (16x16 -> a
  136-cell triangle) for 7-9% of that frame's packets. Do it when something
  needs the space, not before — see FINDINGS.
- **`same_op` restructure of `rsd-alu-pair`**: its joint distribution is
  diagonal-dominated (same-op pairs ~60%), and only 9 distinct mnemonics
  appear. A top-8 symmetric block covers 98.6-98.9%.

# Design-document feedback: the `encoding.yaml` migration

## What this is

A consistency review of the project's design documents against the stated change
of course: **`encoding.yaml` becomes the single source of truth and single point
of iteration** for the prospective instruction set, displacing `scheduler/rules.py`
in that role.

The review was deliberately structured so that no single reading could dominate.
Four reviewers were each **blinded to a different facet** and asked the same
question, so that agreement between them would carry evidential weight:

| Reviewer | Could read | Could **not** read |
|---|---|---|
| goals-facet | `GOALS.md` + encoding trio | `PLAN.md`, `VALIDATION.md`, `TODO.md`, `RULES.md`, all `.py` |
| plan-facet | `PLAN.md`, `RULES.md`, `TODO.md` + encoding trio | `GOALS.md`, `VALIDATION.md`, all `.py` |
| validation-facet | `VALIDATION.md`, `TODO.md` + encoding trio | `GOALS.md`, `PLAN.md`, `RULES.md`, all `.py` |
| source-facet | all `.py` + encoding trio | every prose design document |

("encoding trio" = `encoding.yaml`, `encoding.md`, `encoding_budget.md`.)

Every reviewer was told explicitly that **the source code does not yet reflect
the new intent, and that this is expected** — "the code can't do X yet" was ruled
out as a finding. The deliverable was always document edits, not a code gap list.

A fifth pass (not blinded) audited the `encoding.*` tooling — `encoding_render.py`,
`encoding_assign.py`, `encoding_budget.py`, `encoding_verify.py` — on the grounds
that preparing the yaml has caused those tools to codify design constraints of
their own.

Findings that three or four blinded reviewers reached **independently** are marked
**[convergent]**. Those are the highest-confidence items: the reviewers could not
see each other's documents, so agreement is not shared-context bias.

## Scope of this assessment

Assessed against `claude/instruction-pairing-patterns-4fcamc` @ `75cf53d`
("Derive immediate scale from the template; drop all per-frame imm blocks").

That branch is a **superset** of `claude/encoding-yaml-migration-sqt5p1` (@ `b1d069b`),
adding 8 commits. Findings it has closed are recorded in Part 1 and are **not**
repeated as open items. Everything in Part 2 was re-checked against `75cf53d` and
still stands there.

One structural fact frames everything below: **that branch changes no planning
document.** `GOALS.md`, `PLAN.md`, `VALIDATION.md`, `TODO.md`, `CLAUDE.md`, and
`scheduler/RULES.md` are byte-identical to `main`. Its work is in the yaml, the
tooling, and the new `yaml_migration.md`.

---

# Part 1 — Closed by this branch

These were live findings in the review. They are resolved at `75cf53d`; they are
listed so the same ground is not re-covered.

### 1.1 The dataflow / liveness ownership question — **decided**

This was the single largest finding (source-facet). The entire chain contract —
which B operand the chain feeds, whether the intermediate must be dead, distinct-
destination rules, mutual independence for dual frames, the asymmetric reversal
rule — lived only in `rules.py` decorators, unrecoverable from the yaml.

`yaml_migration.md` now decides it rather than closing the gap mechanically: the
**templates already encode the dataflow implicitly** — a shared `tmp` is both the
chain link and the assertion that the intermediate is dead — and the following are
declared **scheduler-owned, deliberately outside the yaml**:

- commutative operand fitting (`rules.py:114, 224, 233, 281`)
- dual-op order-insensitive tuple match (`_canonical_dual`, `rules.py:684`)
- liveness analysis (`live_out`)
- the instruction model (`isa/instruction.py`)

That is a recorded ownership boundary, which is what the review asked for.

### 1.2 Per-frame `imm:` blocks eliminated — schema defect class retired

Immediate width and signedness now attach to the **op** (`slli_6u`, `addi_6s`,
`li_6s`, … as anchored entries), and scale is derived from the template coefficient
via the new `analysis/imm_expr.py`. This retires:

- the `imma`-vs-`imm` key mismatches (prologue-pair, epilogue-pair, dual-mem) **[convergent]**
- "field widths are not declared — they are regex-derived from row cell tokens,
  so width and semantics live in unlinked places"

### 1.3 `ops` normalized to biclique clusters — template/ops mismatch class retired

The `{a, b}` cluster form retires the mismatches reported in `addi-branch-pair`,
`chain-bit-test-branch`, `arith-jump-pair`, `dual-indep-pair`, and `pre-inc-pair`
**[convergent]**.

### 1.4 The rival opcode-namespace models largely reconciled

`--opcodes` now prices each op at `2^ext` codepoints (`ext` = immediate bits above
the frame's base range), unifying opcode space and immediate width into **one**
accounting. The review found three mutually incompatible models in active use;
after this branch there is one coherent model plus one stale outlier
(`encoding_budget.md` — still open, see §C1).

### 1.5 Instruction equivalences moved into the yaml

The `andi` → `slli`/`srli` zero-test rewrite formerly hidden in
`_shift_for_zero_test` is now declared as named patterns (`low-mask-zero-test`,
`high-mask-zero-test`, `zero-compare-eq`/`-ne`, plus a candidate
`single-bit-sign-test`). Nothing consumes them yet, but they are recorded design.

### 1.6 Inter-immediate relations now checkable

`analysis/imm_relations.py` parses template immediate expressions and validates the
implied cross-instruction constraint against the corpus (prologue 100%, mem-pair
100%, dual-mem 0%).

### 1.7 Zero-immediate convention — **decided**

Arithmetic ops cannot encode a zero immediate (degenerate; canonicalises to
`mv`/`li`, and the codepoint may be reclaimed); memory ops can (`0(rs)` is an
ordinary offset). Inferred from instruction type, not declared per frame.

### 1.8 The 4-bit low-register class — **explicitly deferred**

`yaml_migration.md`: "Abandoned for now — the provisional system completes without
the constraint, so those register-class limits fall on the floor today." An explicit
deferral is a resolution; the finding was that the decision was unrecorded.

### 1.9 One code↔yaml conflict actually fixed

`load-sp-branch` / `load-base-branch` offsets are now width-scaled in `rules.py`,
matching the yaml's declared scaling. (See §G2 — `RULES.md` was not updated to match,
which *worsened* a different finding.)

---

# Part 2 — Open findings

## A. Authority and provenance — the planning documents

**None of these documents has been touched.** Every statement below still asserts
the pre-migration hierarchy.

**A1. `PLAN.md:949` — "This is the only file that needs to change when iterating
on pairing policy."** Refers to `rules.py`. This is the most direct contradiction
of the migration in the repository. *Edit:* iteration happens in `encoding.yaml`;
`rules.py` is derived or conformance-checked against it. Note the disqualifier
lists (`A_SLOT_DISQUALIFIERS`, `B_SLOT_DISQUALIFIERS`) may legitimately remain
code-side — say so explicitly, because the yaml has no disqualifier concept.

**A2. `PLAN.md:894` — "`scheduler/RULES.md`, which is authoritative."** Authority
must transfer to `encoding.yaml`; RULES.md becomes a companion.

**A3. `PLAN.md:9` — "The pairing rules are the primary experimental artefact…
iterating on them requires only local edits to one file."** The "one file" is
implicitly `rules.py`. *Edit:* name `encoding.yaml` as the primary artefact and
single point of iteration.

**A4. `PLAN.md` project-layout tree** omits `encoding.yaml`, `encoding.md`,
`encoding_budget.md`, `util/encoding_render.py`, `util/encoding_assign.py`,
`analysis/encoding_budget.py`, `analysis/encoding_verify.py` — and now also
`yaml_migration.md`, `analysis/imm_expr.py`, `analysis/imm_relations.py`. The
design's central file is absent from the map.

**A5. `GOALS.md:22` — "Express a candidate pairing rule as a small, self-contained
function."** *Edit:* a candidate rule is a declarative **frame** (templates + op
clusters + rows) in `encoding.yaml`.

**A6. `GOALS.md:28, 57` — "Compare pairing rates across rule sets."** The comparison
axis is now `encoding.yaml` revisions, not rule sets.

**A7. `VALIDATION.md` never mentions `encoding.yaml`.** The whole document defines
validation as keeping `PLAN.md` and the codebase in sync. See §F.

**A8. `CLAUDE.md`** still directs readers to `scheduler/RULES.md` for pairing rules.

**A9. `yaml_migration.md` is a fifth planning document referenced by nothing.**
It states a goal stronger than anything in `PLAN.md` — "generate the pairing logic
from it, retiring the hand-written checks in `scheduler/rules.py`" — while `PLAN.md`
still describes hand-authored rules as the mechanism. Either promote its content
into `PLAN.md`/`GOALS.md` or have those documents cite it as the migration's
governing plan. Leaving both is how the current contradiction arose.

## B. `encoding.yaml` internal consistency

**B1. The `g`/`h` semantics are contradictory *inside a single file*. [convergent —
all four reviewers]** Three mechanisms now coexist:

- `encoding.yaml:108–109` (Overview): when not carrying 6-bit immediates, g/h
  "are used to extend the range of the `funct3` field" — i.e. opcode bits.
- Per-frame notes at `encoding.yaml:229` and `:418`: g/h extend or provide `immb`
  — i.e. immediate bits.
- The new codepoint model prices extended immediate range at `2^ext` codepoints,
  a third mechanism that does not reference g/h at all.

This is decision 2 in the register below and it blocks §C1. *Edit:* pick one model
and rewrite the Overview bullet; if the funct3-extension role is retired, say so.

**B2. `x31` clobber semantics unresolved.** `encoding.yaml:129` still declares
"One defined output register, plus x31 becomes undefined," and the renderer uses a
`tmp (=x31)` convention. But `yaml_migration.md` §1.1 decides `tmp` means *the
compiler's own register, required dead* — the scheduler never models an x31 clobber.
The two models diverge for any code that uses x31. *Decide:* is the chain temp
architecturally x31 (making x31 unallocatable across packets), or is it any dead
register? This is separable from — and not answered by — the matching semantics
decided in §1.1.

**B3. The `rd = x0/x2` sentinel reservation is declared but unenforced.** The
`reserved` node is `status: active`, yet `rules.py` never checks it (`exclusive_rd`
explicitly treats x0 as a non-collision, and x2 is inside the x0–x15 window that
rsd-alu accepts). If the reservation is real it is a pairing constraint every frame
with an `rd` field inherits. Currently no document records the enforcement gap.

**B4. `level:` is undocumented.** Present on all 18 frames, defined nowhere, and it
drives markdown heading depth — so four level-1 frames render as `#` H1 siblings of
section headers, breaking `encoding.md`'s outline. *Edit:* define it in the yaml
header, or decouple it from heading depth. **[convergent]**

**B5. Comma-joined frame names.** Three frames carry two rule names in one string
(`"deref-chain-load-pair, base-chain-load-pair"`, `"load-sp-branch, load-base-branch"`,
`"dual-mem-addi-pair, dual-mem-shadd-pair"`), which consumers split on `,` to map to
rules. Two rules with different constraints share one drawn layout with no per-rule
distinction. Under machine consumption these should be proper lists.

**B6. Prose is load-bearing for two tools.** `encoding_assign.wants_gh()` regex-greps
frame `notes` for phrases like `` `g` … extend `` to decide whether a frame wants a
wide immediate, and `encoding_render.lint()` suppresses a missing-operand error when
the notes mention the immediate by name (visible as `[immb in g/h per notes]` in lint
output). Both the opcode assignment result and the lint verdict therefore depend on
note *wording*. That is fragile for a source of truth; the constraint needs structured
representation.

## C. Generated-artifact provenance

**C1. `encoding.md` and `encoding_budget.md` have different sources of truth. [convergent]**

This is the root cause of the frame-set mismatch every reviewer independently found —
it is not an editing slip:

- `encoding.md` ← `encoding.yaml`, verified: `--check` reports IDENTICAL byte-for-byte.
- `encoding_budget.md` ← **`rules.py`**. `analysis/encoding_budget.py` iterates the
  `RULES` list and prints rule names.

Consequences, all still present at `75cf53d`:

- `encoding_budget.md:63` tabulates `mvload-jump-pair` (936 matches), which has **no
  yaml frame**; the yaml's `dual-mem-shadd-pair` has no budget row. Both docs claim
  21 frames; the sets differ by this swap.
- `deref-chain-load-pair`/`base-chain-load-pair` and `dual-mem-addi-pair` appear split
  in the budget where the yaml merges them.
- `encoding_budget.md:21, 141` cite generated `encoding.md` as the spec, inverting the
  hierarchy.
- `encoding_budget.md:192` concludes "`g,h` are free for immediates, and all 21 frames
  fit" — **directly contradicted by `encoding_assign.py` on the same branch**, which
  reports 6 extended-immediate clashes (§D2). This branch sharpened the contradiction
  rather than resolving it, because the assigner improved while the budget did not.

*Edit:* re-point `encoding_budget.py` at the yaml and regenerate. Until then the
budget's headline claim is not a claim about the authoritative frame set.

**C2. `mvload-jump-pair` has no yaml frame.** 936 matched pairs; `encoding_verify.py`
labels it "a spec gap" in its own output. *Decide:* add the frame (it carries real
corpus weight and is one of only two frames flagged for immediate shortfall) or drop
the rule and regenerate the budget. **[convergent]**

## D. Design constraints that live only in the tooling

Preparing the yaml caused the tools to codify substantial design. None of it appears
in any planning document — `GOALS.md`, `PLAN.md`, `VALIDATION.md`, `TODO.md`,
`CLAUDE.md`, and `RULES.md` contain **zero** references to `encoding.yaml`,
`encoding.md`, or any of the four tools.

**D1. The ISA uses a variable-length prefix code for frame identification.** Canonical
Huffman, explicitly DEFLATE-style, decoded MSB→LSB over `opcode5:funct3:g:h`, with
per-frame identifier lengths chosen by a Kraft-greedy promotion policy (immediate-hungry
frames promoted to shallower words, cheapest first, until codepoints run out). This is
a first-order architectural commitment and a contention-resolution *policy*. It exists
only in `encoding_assign.py`'s docstring and code.

**D2. The central design tension, quantified but untracked.** The two 16×16 ALU frames
(`chain-alu-pair`, `rsd-alu-pair`) alone claim 512 of 1024 codepoints, which forces
**6 frames** into extended-immediate conflict: `load-chain-alu-pair`, `arith-mem-pair`,
`addi-branch-pair`, `chain-bit-test-branch`, `chain-li-branch`, `mem-pair`. (It was 5
before this branch; `chain-li-branch` joined.) The tool recommends shrinking those
op-lists. This is arguably the most consequential open design question in the project
and it appears in no `TODO.md` item and no plan section.

**D3. A hardware-decoder alignment objective.** Frames are ordered for canonical
assignment by A-slot RISC-V format (load / OP-IMM / store / OP / branch / jump) so the
leading identifier bits track real `opcode[6:2]`, letting "a hardware A-slot decoder
branch on the same bits it already uses." That is a genuine design goal living in a
docstring as a "nice-to-have." *Decide:* adopt it as a stated objective or record it
as incidental.

**D4. Methodology constants that are design choices.** The 90/95/99% coverage targets;
p95 as the immediate-width statistic; the "5-bit + g + h = 7 bits" threshold for
flagging a frame as immediate-starved; the 95% / 99.5% register-pressure thresholds
that decide whether a 4-bit register cut is "costly." These determine what counts as
"fits."

**D5. Greedy first-accepting-rule attribution.** Both `encoding_budget.py` and
`encoding_verify.py` `break` at the first accepting rule, so **rule order determines
every number in the budget analysis**. Frame priority lives in `rules.py`'s `RULES`
list order and has no yaml representation. Under single-source-of-truth this must
become an explicit yaml property, or the docs must state that attribution order is
significant and where it is defined.

**D6. Immediate width-measurement semantics live in analysis code.** `IMM_SIGNED` /
`SHIFT_MN` membership, signed-vs-unsigned bit counting, width-scaled memory offsets,
and the "unaligned → return unscaled width so it overflows and gets flagged"
convention collectively define what "fits" means.

**D7. The packet claims RVC encoding space.** The 2-bit marker `10` means packets
occupy a compressed-encoding quadrant. Stated only in comments and the renderer's
invariant tail. This bears on `GOALS.md`'s RVC-ceiling framing, which currently treats
RVC headroom as something better rules could capture — packets and literal RVC compete
for the same space rather than composing. Worth confirming and stating.

## E. Design constraints still only in scheduler code

Beyond what §1.1 explicitly assigned to the scheduler, these remain unrecorded
anywhere:

**E1. Global slot discipline.** A executes before B; any control transfer may only
occupy the B slot; unknown instructions never pair; calls are excluded from jump
frames. This defines packet execution semantics, not a scheduler heuristic, and
belongs in the design docs.

**E2. Relocation and optimism policy.** `%pcrel_lo`/auipc-fed loads never pair;
unresolved immediates never fit; branch and jump displacements are deliberately
**not** range-checked. The yaml expresses the last of these via `unbounded: true` for
branches, but never says direct-jump `j`/`jal` targets are also unchecked and unencoded.
This is the same optimism that makes the RVC-eligibility ceiling optimistic, and it
should be stated as an explicit measurement-scope decision.

**E3. The pseudo-op taxonomy is defined in three places.** `li`/`mv`/`addi4spn` appear
as yaml opcodes but are *defined* by predicates in `isa/instruction.py`, re-implemented
in `encoding_budget.subform()`, and listed again in `yaml_migration.md` as P1–P5 with
placement explicitly "TBD". The yaml's op vocabulary is meaningless without them.
Resolving P1–P5 placement is a prerequisite for the yaml standing alone.

**E4. Encoding aliasing conventions.** `addi imm==0` passes range checks because it
"encodes as `add rd, rs1, x0`"; `arith-mem` excludes `imm==0` because it would "encode
as a move from x0". Related to the §1.7 decision but not the same statement, and not
recorded.

## F. Validation obligations

`VALIDATION.md` is unchanged and still frames validation as "testing the plan against
the implementation."

**F1. The triage model needs a third category.** Techniques 1 and 4 treat discrepancies
symmetrically — either the code or the plan may be wrong. Once the yaml is authoritative,
encoding discrepancies have a predetermined direction. *Edit:* add a **conformance gap**
category — the yaml specifies X, code/PLAN does Y, so code/PLAN changes; amending the
yaml instead is a *design change*, not a bug fix.

**F2. `Technique 4` is a three-document cross-check (`GOALS × PLAN × code`).** It must
become four, or gain a dedicated `encoding.yaml × code` pairing. An agent blind to the
yaml can no longer adjudicate any pairing-rule discrepancy.

**F3. `Technique 2` (blind plan review) should cover the yaml.** Internal contradictions
and underspecification in `encoding.yaml` are now spec bugs at the source of truth —
§B1, B4, B5 are live examples.

**F4. Missing: yaml schema validation.** Nothing requires that grid `bits` sum to 32,
that rows span exactly 7 cells net of spans, that every row field name resolves to a
grid column or declared operand, that op clusters agree with templates, or that frame
names are unique.

**F5. Missing: regeneration gates.** No obligation to re-render `encoding.md` and require
a zero diff, or to regenerate the budget, after a yaml edit.

**F6. Missing: the codepoint-budget invariant as a standing gate.** Any frame or op-set
addition should re-run the budget and re-verify against the namespace.

**F7. Green lights currently mean less than they appear to.** `--check` reports IDENTICAL
and `--lint` reports 0 problems, yet real defects persist (§B1–B6). Lint checks only
asm-operand ↔ row-field *name* correspondence; it validates neither op clusters against
templates nor the reserved-register rule, and it has a documented prose escape hatch
(§B6). `VALIDATION.md` must not treat a passing lint as conformance evidence.

**F8. `TODO.md` has no migration item at all** — nothing tracks the yaml-as-truth
migration, schema validation tooling, or the regeneration gates.

**F9. `TODO.md:53–54`** ("Cross-rule-set comparison … rules are edited in-file, there is
no `--rules` selector") is stale framing: the comparison axis is now yaml revisions, and
the missing tooling is a stats-diff across them.

**F10. `TODO.md:45–47`** treats the dead `mem_pair` branch as code to delete, but the yaml
defines `mem-pair` as a live frame. Recast as a conformance question before deleting.

## G. `scheduler/RULES.md`

**G1. Its self-description inverts the new hierarchy.** The header presents it as
documentation *of* `rules.py`, "written to be read alongside the code," with each heading
naming a `PairingRule` entry and `check()` function. §1.1 says each rule "corresponds to a
candidate packet encoding." *Edit:* reframe as the scheduler-side enforcement of frames
defined in `encoding.yaml`, with headings naming yaml frames and `check()` names kept only
as implementation cross-references.

**G2. Drift got *worse* on this branch.** `rules.py` moved to width-scaled load-branch
offsets (§1.9), but `RULES.md:339–340` still reads "**10-bit unsigned byte offset**
(`uimm10`, *unscaled*)" and `RULES.md:922–923` still says "offset uimm10 (bytes)". RULES.md
is now wrong about the **code** as well as the yaml.

**G3. Roughly a dozen numeric constraints conflict with the yaml** and are untouched.
Non-exhaustive: `rsd-alu` imm −64..64 vs the drawn field; `store-chain-alu` widths;
`mem-pair` "8-bit sp / 5-bit base" vs the yaml's 6-bit base / 10-bit sp; `load-base-branch`
"5-bit unsigned byte offset (0–31)" vs width-scaled; `chain-li-branch` 8-bit vs 6+1;
`addi-branch-pair` 8-bit vs 6-bit; `prologue`/`epilogue` 7-bit ×16 vs 10-bit ×16, plus
prologue's ra-only store restriction vs the yaml's free `rs1b`; `arith-mem` A-op set and
immediate range; the whole dual family's naming and offset model. *Edit:* under
single-source-of-truth the yaml keeps widths and op-sets; RULES.md keeps scheduler
semantics (deadness, chaining, order-sensitivity) and **references** the yaml for numbers
rather than restating them. The §4 summary table should be regenerated or have its
"Key limits" column replaced with frame references.

**G4. Two yaml-introduced concepts appear nowhere in RULES.md or PLAN.md**: the implicit
chain-temp model (§B2) and the `rd` sentinel reservation (§B3).

## H. Goals-level content

**H1. `GOALS.md:63–72` contradicts the yaml's chain semantics. [goals-facet]** The
Packet execution model states that "a pair behaves exactly like the same two instructions
unpaired," that "register data-dependencies *within* a pair are never a pairing
constraint," and that pairing rules "express only hardware *structural* constraints …
not register compatibility."

The yaml's chain frames require the intermediate be **dead**, which is precisely a
register-liveness pairing constraint — and this branch's own `yaml_migration.md`
*confirms* it, stating that a shared `tmp` is "the assertion that the intermediate is
dead." The branch strengthened the contradiction rather than resolving it. *Edit:* §2
must distinguish independent pairs (where the current text holds) from chain pairs
(where it does not), and acknowledge operand-form constraints — shared `rsda`
read-modify-write, dual-arith2's shared sources, mem-pair's same-base adjacent offsets —
as exactly what the yaml's frames declare.

**H2. No encoding-budget success criterion.** `GOALS.md`'s only quantitative signals are
pairing rate and the RVC ceiling, but codepoint-and-immediate fit is the actual design
gate. *Edit:* a frame set is acceptable only if its codepoint demand fits the namespace
and its p95 immediate widths fit the declared fields.

**H3. The headline pairing-rate metric is now ambiguous, and the ambiguity is
material.** Pairing rate measures `rules.py` acceptance, not encodability under the yaml.
The verifier reports **79.7%** of matched pairs carry an immediate that fits the field as
drawn — so roughly one pair in five that the scheduler counts cannot currently be
encoded. *Decide and state:* is the success metric pairs accepted, or pairs both accepted
**and** encodable? The second number is materially lower than what `results/` reports.

**H4. Concepts the goals never acknowledge**: the frame/row/variant structure and the
`level` tier; the reserved-encoding sentinel mechanism; the fixed 32-bit skeleton and its
RVC-space consequence (§D7); scaled immediates and `unbounded` displacements; the implicit
chain temp.

**H5. `GOALS.md` §5's pseudo-instruction invariant needs strengthening.** It treats
pseudo-vs-canonical as implementation-neutral, but the yaml's op vocabulary makes `li`,
`mv`, and `addi4spn` load-bearing encoding categories — frames are legal for `li` but not
general `addi`. Keep the invariant; add that the canonical vocabulary is defined by the
yaml's op clusters and that normalization must map onto it. (Depends on E3.)

**H6. Solo-reason and per-rule stats vocabulary.** `GOALS.md:31–34, 45–47` frame
diagnostics as per-rule-function feedback. Restate in frame/row terms: a solo reason should
name the yaml frame and the binding constraint; stats become per-frame, ideally per-row
since SP-relative rows are distinct capture paths.

## I. Tests

**I1. Boundary tests encode the pre-yaml design.** `tests/test_pairing.py` hard-codes
`rules.py`'s constants as accept/reject boundaries — immediate edges, the x0–x15 window,
op-set membership, tuple tables. Every §G3 conflict resolved in the yaml's favour
invalidates the corresponding boundary tests. *Decide:* are tests regenerated from the
yaml (parameterized), or frozen as a regression baseline of the pre-migration design?

**I2. Pair-count expectations** in `tests/test_scheduler.py` shift with any yaml
iteration.

**I3. RVC-eligibility tests** measure the comparison baseline, not the new encoding.
The docs should state that this axis is out of the yaml's scope.

---

# Part 3 — Decision register

Ordered by how much else they unblock.

| # | Decision | Status |
|---|---|---|
| 1 | **`g`/`h` semantics** — funct3 extension, immediate bits, or purely `2^ext` codepoints? | **Open.** Blocks C1. Three mechanisms coexist in one file (§B1). |
| 2 | **Shrink the 16×16 ALU op-lists, or accept 6 frames losing extended immediates?** | **Open.** Quantified by the tooling, tracked nowhere (§D2). `util/biclique_tiling.py` appears to be the instrument for this and is referenced only from `encoding_budget.md`. |
| 3 | **`mvload-jump-pair`** — add a frame, or drop the rule? | **Open** (§C2). |
| 4 | **Is the chain temp architecturally x31**, or any dead register? | **Open** (§B2). Distinct from the matching semantics settled in §1.1. |
| 5 | **Wide-`li`** — dedicated frame, lui-split, or accepted loss? | **Open.** This *is* the `dual-indep` 34.4% pack rate; the budget's only genuine shortfall; untracked in `TODO.md`. |
| 6 | **`RULES.md`'s future** — regenerated, reduced to semantics-only, or retired? | **Open** (§G). Drift worsened on this branch. |
| 7 | **Pairing-rate metric** — accepted pairs, or accepted-and-encodable? | **Open** (§H3). |
| 8 | **Frame priority** — an explicit yaml property, or documented as rules.py-order? | **Open** (§D5). Determines every budget number. |
| 9 | **Pseudo-op canonicalization placement** (P1–P5) — yaml, or an ISA-side table? | **Open**, explicitly "TBD" in `yaml_migration.md` (§E3). |
| 10 | **Hardware-decoder opcode alignment** — stated objective or incidental? | **Open** (§D3). |
| 11 | **`rd = x0/x2` sentinel** — enforce it? | **Decided** (active, x2 not x1) but **unenforced in code**, gap unrecorded (§B3). |
| 12 | **Opcode namespace model** | **Effectively decided in tooling** (unified `2^ext`), but unwritten and still contradicted by `encoding_budget.md` (§1.4, §C1). |
| 13 | **Schema growth vs scope-out** | **Largely decided** by `yaml_migration.md`'s captured / scheduler-owned / deferred split (§1.1). Residual: frame priority (#8) and register classes (deferred, §1.8). |
| 14 | **Chain `tmp` matching semantics** | **Decided** — templates encode it; `tmp` matches only `tmp` (§1.1). |
| 15 | **Zero immediates** | **Decided** (§1.7). |

---

# Part 4 — Measurements

All at `75cf53d`, corpus `tests/godot.s tests/testcase0.s`.

```
python3 util/encoding_render.py --check      # IDENTICAL: render matches encoding.md byte-for-byte
python3 util/encoding_render.py --lint       # 0 frame(s) with correspondence problems
python3 util/encoding_render.py --opcodes    # base 854 / codepoints 880 of 1024; 144 spare
python3 util/encoding_assign.py              # 6 extended-immediate clashes
python3 -m analysis.encoding_verify tests/godot.s tests/testcase0.s
```

**Conformance — `encoding_verify`.** 19385 matched pairs, 16258 carried a checkable
immediate, 12965 fit their frame's declared field = **79.7%**. Identical to the figure
before this branch's 8 commits.

Worst frames:

| Frame | Pack rate | Worst overflow |
|---|---:|---|
| `rsd-alu-pair` | 28.2% | `addi_rsd imm=64` needs 8b vs 5b |
| `dual-indep-pair` | 34.4% | `li imm=1110` needs 12b vs 5b |
| `store-chain-alu-pair` | 35.1% | `li imm=64` needs 8b vs 5b |
| `arith-jump-pair` | 60.6% | `li imm=48` needs 7b vs 5b |
| `chain-alu-pair` | 82.5% | `addi_other imm=40` needs 7b vs 5b |

These low rates are the §G3 conflicts made numeric: `rules.py` accepts immediates wider
than the yaml draws. Resolving them in the yaml's favour will reduce measured pairing
rate — see decision 7.

Unframed: `mvload-jump-pair`, 936 matched pairs.

**Extended-immediate clashes — `encoding_assign`.** 6 frames: `load-chain-alu-pair`,
`arith-mem-pair`, `addi-branch-pair`, `chain-bit-test-branch`, `chain-li-branch`,
`mem-pair`. Only `load-sp-branch`/`load-base-branch` obtains extended-immediate leaves.
The tool's own diagnosis: the two 16×16 ALU frames claim 512 of 1024 codepoints.

---

## Suggested sequence

1. Settle decisions 1–4 — they gate the largest edits.
2. `GOALS.md` (§H, §A5–A6), then `PLAN.md` (§A1–A4), then `VALIDATION.md` (§F).
3. Re-point `encoding_budget.py` at the yaml and regenerate `encoding_budget.md` (§C1).
4. `RULES.md` (§G) — after decision 6, since its scope depends on the answer.
5. yaml cleanups (§B4–B6) and `TODO.md` (§F8–F10).
6. `CLAUDE.md` (§A8) last — it is a pointer file and should reflect the settled state.

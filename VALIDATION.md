# Validation Plan: Testing the Plan Against the Implementation

This document captures techniques for using AI agents to keep the design
documents, `encoding.yaml`, and the codebase in sync.  The goal is to catch
drift early: cases where the code does something the plan doesn't describe, or
the plan promises something the code doesn't implement.

**`encoding.yaml` is the source of truth for the packet encoding**, and that
changes how one class of finding is triaged.  Where a discrepancy concerns the
encoding — op-sets, immediate widths, field layout, codepoint budget — it is not
symmetric: the yaml is right by construction and the other side changes.  See
the *conformance gap* category below.

---

## Technique 1 — Direct comparison (plan + code, side by side)

Spawn one or more agents with access to both PLAN.md and the source files.
Ask each agent to list every discrepancy they find between the plan and the
implementation: things the plan says that the code doesn't do, and things the
code does that the plan doesn't mention.

**Prompt sketch:**
> Read PLAN.md in full, then read the source files listed below.  For each
> section of the plan, check whether the implementation matches.  Report every
> discrepancy as a numbered item: state which section of the plan it comes from,
> what the plan says, and what the code actually does.  Do not suggest fixes —
> only report findings.

**Notes:**
- Run multiple models (Opus, Sonnet, Haiku) in parallel on the same prompt.
  Different models catch different things; the union of findings is richer than
  any single pass.
- Findings fall into three categories that must be triaged separately:
  - *Code bug*: the plan is right and the code is wrong — fix the code.
  - *Plan drift*: the code is right (deliberate design) and the plan is stale —
    update the plan.
  - *Conformance gap*: `encoding.yaml` specifies X and the code (or PLAN, or
    RULES.md) does Y.  The direction is predetermined — the code/prose changes.
    Amending the yaml to match the code instead is a **design change**, not a
    bug fix, and should be argued on encoding grounds (codepoint budget,
    immediate fit, corpus yield) rather than slipped in as reconciliation.
- Avoid acting on findings until triage is complete; some apparent bugs are
  intentional design choices that just weren't documented.


## Technique 2 — Blind plan review (plan only, no code)

Spawn an agent with access only to PLAN.md.  Ask it to identify internal
inconsistencies, ambiguities, missing definitions, and sections that are
underspecified to the point where two reasonable implementations could differ.

This technique should cover **`encoding.yaml` as well as PLAN.md**.  Internal
contradictions and underspecification in the yaml are now spec bugs at the
source of truth, and are more damaging than the same defect in prose.

**Prompt sketch:**
> Read PLAN.md.  Do not look at any source files.  Report:
> (a) internal inconsistencies — places where two sections of the plan
>     contradict each other;
> (b) ambiguities — places where a competent implementer could make two
>     different reasonable choices;
> (c) missing definitions — terms used but never defined;
> (d) underspecified behaviour — cases or edge conditions the plan is silent on.

**Notes:**
- This finds problems the plan has regardless of the code.
- Useful to run before a large implementation sprint to catch spec gaps early.
- Also useful after a major refactor: the plan may have been correct before and
  is now self-contradictory.


## Technique 3 — Six targeted approaches (gap-finding menu)

These six approaches find different classes of problems and can be run
independently or combined.  Estimated yield is noted for each.

1. **Write unit tests first** *(medium yield)*
   Draft the test file for one module without looking at the plan, then compare
   what the tests assume against what the plan specifies.  Edge cases that tests
   naturally reach — what happens at a call inside a loop? what is the seed for
   a block with no successors? — surface gaps the plan glosses over.

2. **Trace a concrete example end-to-end** *(medium yield)*
   Pick 10–15 lines of real RISC-V assembly — something with a branch, a call,
   a load/store pair, and one unknown mnemonic — and manually walk through every
   phase the plan describes: two-pass label scan, block boundaries, CFG edges,
   liveness dataflow, dep-graph edges, list scheduling decisions, greedy-advance
   pairing, annotation output.  Any step where you cannot produce a definite
   answer from the plan text alone is a gap.

3. **Ask a model to implement one module from the plan alone** *(highest yield)*
   Give the model only the relevant plan section(s) and ask it to write the
   code.  The questions it asks, the assumptions it makes, and the corners it
   has to invent are all plan deficiencies.  This forces every ambiguity to
   resolve: the model either makes a reasonable guess (revealing the plan left
   room for it) or asks a clarifying question (revealing a genuine gap).

4. **Adversarial edge-case prompting** *(high yield)*
   Ask a model to generate assembly snippets specifically designed to stress
   each design decision: a label that is both a branch target and `.globl`, a
   commutative instruction where `rd == rs1 == rs2`, a block that ends with an
   unknown instruction, a tail call in a loop, a block with only one
   instruction.  Then trace each through the plan.

5. **Cross-check spec-derived tables against primary sources** *(targeted yield)*
   Any table or enumeration in the plan that was written from training data
   rather than a primary source is suspect: the RVC encoding table in §5, ABI
   register conventions, calling-convention saved/clobbered sets.  Fetch the
   actual RISC-V specs and diff them against the plan tables to catch wrong
   bit-widths, alignment constraints, or register-range boundaries.

---


## Technique 4 — Four-document cross-check (GOALS × PLAN × encoding.yaml × code)

Run agents in parallel, each with a different blind spot.  The goal is to catch
discrepancies that only appear when comparing two of the four, and to ensure
that each layer is internally consistent with the others.

`encoding.yaml` is a required axis: an agent blind to it cannot adjudicate any
pairing-rule discrepancy, because the numbers it would be checking against now
live there.

**Agent layout:**

| Agent | Reads | Blind to | Finds |
|-------|-------|----------|-------|
| A | GOALS + PLAN + yaml + code | nothing | all four-way discrepancies |
| B | PLAN + code | GOALS, yaml | plan/code gaps regardless of intent |
| C | GOALS + code | PLAN, yaml | goal/code gaps; implementation decisions with no goal backing |
| D | GOALS + PLAN | yaml, code | plan/goals inconsistency; decisions in PLAN not justified by any goal |
| E | yaml + code | GOALS, PLAN | **conformance gaps** — code limits that disagree with the drawn frames |

**Prompt sketch for each agent:**

> You are auditing a software project.  You have access to [FILES].
> Read every file in full.  Report every discrepancy you find as a numbered
> item: state which document/section each side of the discrepancy comes from,
> what each says, and why they conflict.  Do not suggest fixes — only report
> findings.  Do not read any files other than those listed.

**Triage categories after collating all four agents:**

- *Code bug* — PLAN or GOALS says X, code does Y, code should change.
- *Plan drift* — code is right (deliberate design), plan is stale — update PLAN.
- *Conformance gap* — `encoding.yaml` says X, code or prose says Y; the yaml
  wins (see Technique 1).
- *GOALS gap* — design decision present in PLAN with no corresponding goal —
  either add the goal or question the decision.
- *Acknowledged* — already noted in PLAN as a known limitation or future work.

**Notes:**
- Findings confirmed by two or more agents are higher confidence.
- Agent D (blind to code) finds goal/plan inconsistencies regardless of whether
  the code is correct — useful for catching spec gaps before implementing.
- Agent C (blind to PLAN) is most likely to surface new implicit design
  decisions baked into the code that were never written down anywhere.
- Collect all findings before acting; some apparent bugs are intentional
  simplifications that just weren't documented.

---

## Technique 5 — Mechanical gates on `encoding.yaml`

Agent review is for judgement; these are the checks that should run every time
the yaml changes, without a model in the loop.

**Regeneration gates (available today):**

- `python3 util/encoding_render.py --check` must report IDENTICAL.  A yaml edit
  that does not regenerate `encoding.md` leaves the readable artefact lying.
- `python3 util/encoding_render.py --lint` must report 0 correspondence problems.
- `python3 util/encoding_render.py --opcodes` must fit the 1024-codepoint
  namespace, and every frame must land inside its declared `budget`.
- `python3 util/encoding_assign.py` must exit 0 — a non-zero exit means the
  planned budgets do not fit as prefix-code blocks even if the raw codepoint
  total does.
- `python3 -m analysis.encoding_verify tests/godot.s tests/testcase0.s` — watch
  the pack rate for regressions and the "rules with NO frame" list for gaps.

**Gates that do not exist yet** (see `TODO.md`):

- *Schema validation.*  Nothing enforces that grid `bits` sum to 32, that rows
  span exactly 7 cells net of spans, that every row field name resolves to a
  grid column or declared operand, that op clusters agree with the templates, or
  that frame names are unique.
- *Codepoint budget as a standing gate.*  Adding a frame or widening an op-set
  should re-run the budget automatically, not on recollection.

**A passing lint is not conformance evidence.**  `--lint` checks only that
asm-operand names correspond to row-field names.  It does not validate op
clusters against templates, does not check the reserved-register rule, and has a
prose escape hatch: it suppresses a missing-operand error when a frame's `notes`
mention the immediate by name.  Green here means "the drawing is
self-consistent", not "the design is right".

---

## General notes on running agent reviews

- **Triage before acting.**  Collect all findings first, then classify each as
  code-bug or plan-drift before touching anything.  Acting on findings one at a
  time risks fixing things that are not bugs.

- **Capture the triage rationale.**  When a finding is dismissed as WAI
  (working as intended), record *why* in PLAN.md so the same false-positive
  doesn't recur in future reviews.

- **Model diversity matters.**  Opus tends to produce broader coverage with more
  false positives; smaller models are faster and sometimes catch different
  surface-level inconsistencies.  Running all three and taking the union is more
  effective than running one model multiple times.

- **Scope the review.**  After a focused change (e.g. touching only the pairing
  rules), it is sufficient to review only the affected sections of the plan
  rather than the whole document.

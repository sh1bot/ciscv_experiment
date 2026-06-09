# Validation Plan: Testing the Plan Against the Implementation

This document captures techniques for using AI agents to keep PLAN.md and the
codebase in sync.  The goal is to catch drift early: cases where the code does
something the plan doesn't describe, or the plan promises something the code
doesn't implement.

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
- Findings fall into two categories that must be triaged separately:
  - *Code bug*: the plan is right and the code is wrong — fix the code.
  - *Plan drift*: the code is right (deliberate design) and the plan is stale —
    update the plan.
- Avoid acting on findings until triage is complete; some apparent bugs are
  intentional design choices that just weren't documented.


## Technique 2 — Blind plan review (plan only, no code)

Spawn an agent with access only to PLAN.md.  Ask it to identify internal
inconsistencies, ambiguities, missing definitions, and sections that are
underspecified to the point where two reasonable implementations could differ.

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

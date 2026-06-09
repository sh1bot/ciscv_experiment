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


## Technique 3 — Five-subagent structured audit

*(Details to be recovered from session transcript at
`/root/.claude/projects/-home-user-ai-rv32-scheduler/2a627adc-34c7-5a78-9309-5802550b4dc0.jsonl`.
The broad shape was five agents each with a distinct angle of attack, run in
parallel, with findings synthesised afterward.  Possible decomposition — to be
confirmed against the transcript:)*

1. **Plan-to-code coverage**: does every normative claim in the plan have a
   corresponding implementation?
2. **Code-to-plan coverage**: does every non-trivial code behaviour appear
   somewhere in the plan?
3. **Interface contracts**: do the public APIs (function signatures, return
   types, data-class fields) match what the plan specifies?
4. **Test coverage**: do the tests exercise the behaviours the plan calls out
   as important?
5. **Invariant audit**: does the code uphold the invariants the plan states
   (e.g. liveness dict keying, packet sequential semantics, function-boundary
   isolation)?

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

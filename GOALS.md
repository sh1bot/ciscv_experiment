# Goals: What This Tool Is Supposed To Do

This document states the purpose and key assumptions of the scheduler at a
level above the implementation plan.  It is intended to be stable: changes here
represent a change in what the tool is for, not just how it works.

---

## 0. Primary goal: a workbench for exploring pairing rules

The primary purpose of this tool is to provide a fast feedback loop for
iterating on instruction pairing rules against real-world code.

The encoding space for a custom 32-bit packet format has many degrees of
freedom: which opcode combinations are legal, which operand forms are required,
whether sequential execution within a packet is exploited, and so on.  The
right set of rules is not obvious from first principles — it depends on what
patterns actually appear in the output of real compilers on real codebases.

This tool lets a rule author:

1. Express a candidate pairing rule as a small, self-contained function.
2. Run it against a large real-world binary (e.g. a game engine) to measure
   the pairing rate it achieves.
3. Read the annotated output to understand *why* instructions went solo —
   specifically which constraints were the binding ones — and use that to
   refine the rule or propose a new one.
4. Compare pairing rates across rule sets to evaluate tradeoffs between
   encoding complexity and compression gain.

The annotation format is therefore not just cosmetic output — it is the primary
instrument by which rule authors diagnose and improve pairing coverage.  Solo
reasons, RVC eligibility on missed instructions, and packet numbering all serve
this feedback purpose.

---

## 1. Output: annotated assembly

The tool reads RISC-V assembly and emits annotated assembly describing how each
instruction is packaged.  Every instruction is either **paired** (sharing a
32-bit packet with one other) or **solo** (occupying a packet alone).

Solo instructions carry annotations recording RVC eligibility, unknown-opcode
status, and the reasons each applicable pairing rule rejected them.  The purpose
of these annotations is to inform the design of new rules or modifications to
existing ones.  They also identify instructions that are structurally
uncapturable within a given encoding budget — for example, an instruction with
a large immediate that cannot fit the compressed field width the rule allows.
Knowing an instruction is uncapturable is as useful as knowing a rule tweak
would capture it.

---

## 2. Reordering: maximising pairing within dataflow constraints

To maximise paired packets the scheduler may reorder instructions within a basic
block, subject to one hard constraint: **the reordered sequence must preserve
the original dataflow**.  No instruction may be moved to a position where it
reads a register that has not yet been written, if that register would have been
written before it in the original order.

Where the analysis cannot observe register values directly — at function
boundaries and call sites — the standard RISC-V psABI fills in the gaps,
specifying which registers are live on entry, which must be preserved on exit,
and which are clobbered by calls.  Supporting non-standard calling conventions
is not a current goal.

---

## 3. Scope of reordering

The scheduler reorders within a basic block only.  It does not hoist or sink
instructions across branches.  The tool processes compiler output: the compiler
already made those larger-scope decisions, and the tool's job is to pack what
it was given as efficiently as possible, not to re-optimise the code structure.

---

## 4. Unknown instructions

When an unknown opcode appears the tool annotates it with `[?]` and excludes it
from pairing.  Best-effort dependency analysis is applied so that reordering
around it is not completely blind, but the heuristic is an interim measure.
The correct response to an unknown opcode is to add it to the decoder.

---

## 5. Measurement baseline

Pairing rate measurements use the conservative memory ordering default, in which
every load/store pair is ordered unless explicitly relaxed.  This is the
reference point against which rule changes are evaluated.

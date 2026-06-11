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

The output also includes summary statistics: a per-function block reporting
instruction count, pair count, pair rate, and RVC-eligibility rate; and a
file-level block at the end that aggregates these totals and adds a per-rule
hit count breakdown.  These statistics are the primary quantitative signal for
comparing rule sets.

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

Dividing the problem at function boundaries, and within functions at basic-block
boundaries, is what keeps the analysis tractable for large real-world codebases.
A whole-program or cross-block reordering problem would be too expensive for the
intended use case of fast iteration.

---

## 4. Unknown instructions

When an unknown opcode appears the tool annotates it with `[?]` and excludes it
from pairing.  A best-effort guess at its behaviour is made from its operands:
instructions with a recognisable memory-addressing operand are treated as memory
accesses; registers appearing in operand positions are treated as uses.  This
lets the scheduler move other instructions around an unknown one without being
completely blind to its effects.  The heuristic is an interim measure — the
correct response to an unknown opcode is to add it to the decoder.

---

## 5. Pseudo-instructions

The tool must produce the same pairing results regardless of whether the input
assembly uses pseudo-instructions or their explicit encodings.  `mv a0, a1` and
`addi a0, a1, 0` are the same instruction; `ret` and `jalr x0, ra, 0` are the
same instruction.  This invariant may be satisfied either by normalising all
pseudos to their canonical forms on input, or by handling each form explicitly
throughout the tool — either approach is acceptable as long as the observable
output is identical for equivalent inputs.

---

## 6. Measurement baseline

Pairing rate measurements use the conservative memory ordering default, in which
every load/store pair is ordered unless explicitly relaxed.  This is the
reference point against which rule changes are evaluated.

The RVC-eligibility rate reported alongside the pairing rate serves as a
ceiling reference: it shows the fraction of instructions that *could* be
compressed under RVC, independent of pairing constraints.  A large gap between
the pairing rate and the RVC rate indicates headroom that better rules could
capture.  A small gap indicates the encoding is near saturation.

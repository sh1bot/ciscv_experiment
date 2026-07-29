# Goals: What This Tool Is Supposed To Do

This document states the purpose and key assumptions of the scheduler at a
level above the implementation plan.  It is intended to be stable: changes here
represent a change in what the tool is for, not just how it works.

---

## -1. Purpose

The intent here is to facilitate development of an instruction coding based on
the principle of compressing RISC-V instruction streams by packing pairs of
instructions into a single 32-bit word, so that the compressed instruction
stream remains 32-bit aligned, and redundancies between adjacent instructions
can be exploited to aid in compression.  Since this objective is contradictory
to the design of RVC, it is not intended to coexist with it and uses
overlapping instruction encoding space.

## 0. Primary goal: a workbench for exploring pairing rules

The primary purpose of this tool is to provide a fast feedback loop for
iterating on instruction pairing rules against real-world code.

The encoding space for a custom 32-bit packet format has many degrees of
freedom: which opcode combinations are legal, which operand forms are required,
whether sequential execution within a packet is exploited, and so on.  The
right set of rules is not obvious from first principles — it depends on what
patterns actually appear in the output of real compilers on real codebases.

This tool lets a rule author:

1. Express a candidate pairing rule declaratively as a **frame** in
   `encoding.yaml` — its op clusters, immediate templates, and 32-bit row
   layout — which is the single source of truth for the prospective packet ISA
   and the single point of iteration.  The scheduler enforces those frames at
   runtime; the yaml keeps the encoding facts (widths, op-sets, codepoint
   budget).
2. Run it against a large real-world binary (e.g. a game engine) to measure
   the pairing rate it achieves.
3. Read the annotated output to understand *why* instructions went solo —
   specifically which constraints were the binding ones — and use that to
   refine the frame or propose a new one.
4. Compare pairing rates across `encoding.yaml` revisions to evaluate tradeoffs
   between encoding complexity and compression gain.

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
file-level block at the end that aggregates these totals and adds a per-frame
hit count breakdown.  These statistics are the primary quantitative signal for
comparing `encoding.yaml` revisions.

---

## 2. Reordering: maximising pairing within dataflow constraints

**Packet execution model.**  Within a packet the two instructions execute
sequentially: the A-slot instruction completes before the B-slot instruction
begins.  The B-slot may freely read registers the A-slot wrote; a pair behaves
exactly like the same two instructions unpaired, differing only in code density.

For an **independent pair** — two instructions the frame encodes with full,
separate register fields — register data-dependencies are never a pairing
constraint: they are already honoured by sequential execution and by the
dependency graph, which orders B after A.  Such frames express only hardware
*structural* constraints (which instruction-type and operand-form combinations a
packet can physically encode).

Many frames, however, deliberately trade register-field width for opcode space,
and their *operand form* is then part of the encoding.  A **chain frame** shares
one register field across the pair (A's result is B's input) and requires the
intermediate value to be **dead** so no field need name it — a register-liveness
condition that is a real pairing constraint.  Other frames constrain operand
form similarly: a shared read-modify-write destination (`rsd`), dual-arith
shared sources, or `mem-pair`'s same-base adjacent offsets.  These operand-form
and liveness constraints are exactly what the yaml frames declare; the "never a
constraint" statement above holds only for independent pairs.

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
accesses; the **first operand is treated as an output** — a write of that
register, when it names a recognised register — and any **other recognised
registers** in the operand list are treated as **inputs** (reads).  This follows
the common RISC-V convention that the destination is the first operand, and lets
the scheduler move other instructions around an unknown one without being
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

Pairing rate measurements use the tool's **default invocation** as the baseline:
**list scheduling** (neither `--fast` nor `--thorough`) with the **conservative
memory ordering default** (`--same-base-reorder` off), in which every load/store
pair is ordered unless explicitly relaxed.  This is the reference point against
which rule changes are evaluated.  (`--fast` reports a lower rate because it does
no reordering; `--thorough` reports a per-window upper bound — see PLAN §11/§13.)

The RVC-eligibility rate reported alongside the pairing rate serves as a
ceiling reference: it shows the fraction of instructions that *could* be
compressed under RVC, independent of pairing constraints.  A large gap between
the pairing rate and the RVC rate indicates headroom that better rules could
capture.  A small gap indicates the encoding is near saturation.  Note the
packet format claims the RVC (2-bit `10` marker) encoding quadrant, so packets
and literal RVC compete for the same space rather than composing — the RVC rate
is a comparison baseline, not additive headroom.

---

## 7. Encoding budget: the design gate

Pairing rate alone does not tell you whether a frame set is *realisable*.  Every
frame must fit within a shared, finite encoding: a 1024-codepoint opcode
namespace and a fixed 32-bit packet skeleton whose row layout gives each frame a
bounded immediate field.  A frame set is acceptable only if:

- its total **codepoint demand fits the namespace** (each op costs `2^ext`
  codepoints, where `ext` is the immediate bits it needs above the frame's base
  range — verified by `util/encoding_render.py --opcodes` and the per-frame
  `budget`); and
- its **p95 immediate widths fit the declared fields** (verified by
  `analysis/encoding_verify.py`).

These are the actual design gates, alongside pairing rate.  A frame that pairs
well but overflows the namespace, or whose immediates routinely exceed the drawn
field, is not yet a viable encoding.

Two consequences worth stating explicitly:

- **Acceptance vs encodability.**  The headline pairing rate measures how many
  pairs the scheduler *accepts*; the verifier separately measures how many of
  those carry an immediate that actually *fits* the frame as drawn.  The two
  numbers differ (the second is lower).  Which one is *the* success metric is an
  open decision (see `TODO.md`).
- **Cost proportionate to hit rate.**  A frame's codepoint cost should be
  justified by the share of real pairs it captures.  `analysis/frame_score.py`
  scores this as `log2(hit_share / cost_share)`; a frame that consumes far more
  of the namespace than its hit share earns is a candidate for shrinking.

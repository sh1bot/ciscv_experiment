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

The tool reads RISC-V assembly and emits annotated assembly.  The annotations
describe how each instruction is packaged for the target encoding.

Every instruction is emitted in one of two forms:

- **Paired** — two instructions that together satisfy an encoding rule.  The
  rule accepts the pair because the opcodes and operands of both instructions
  fall within the structural constraints the rule expresses.  The two
  instructions are labelled as the A-slot and B-slot of the same packet.

- **Solo** — a single instruction that could not be paired.  A solo instruction
  carries annotations recording:
  - Whether it is eligible for **RVC** (compressed 16-bit) encoding.  This is
    only noted on solo instructions — a paired instruction that is RVC-eligible
    carries no such annotation, because the pairing opportunity was taken and
    the RVC eligibility is moot.
  - Whether it is an **unknown instruction** — one whose opcode is not
    recognised, which means its register reads, writes, and side effects cannot
    be determined.  Unknown instructions are treated conservatively.
  - The **reasons it was not paired**: for each pairing rule that was
    considered and rejected, a short explanation of which constraint was not
    met.  This is diagnostic information; it lets the user understand what
    changes to the source would enable pairing.

An unknown instruction is a **slot disqualifier**: it may not appear in either
the A-slot or the B-slot of a packet.  Because its side effects are unknown it
cannot safely be paired with anything.

Pairing rules are encoding-based and structural.  A rule accepts a pair solely
on the basis of opcode and operand form — not on the basis of whether the
instructions are data-independent.  Data dependencies are handled separately
by the scheduler (see §2); they are not a concern of the pairing rules
themselves.

---

## 2. Reordering: maximising pairing within dataflow constraints

To maximise the number of paired packets, the scheduler is permitted to reorder
instructions within a basic block.  Reordering is subject to one hard
constraint:

**The reordered sequence must respect the original dataflow.**  Concretely: no
instruction may be moved to a position where it reads from a register that has
not yet been written in the new order, if that register would have been written
before it in the original order.  Equivalently, for every read-after-write
dependency in the original program, the writer must still precede the reader
after reordering.

This constraint is computed as a directed acyclic graph (the *dependency graph*)
over the instructions of each block.  A valid reordering is any topological sort
of this graph.  The scheduler searches the space of valid topological sorts for
one that maximises paired packets.

### Analysis boundaries and the ABI

The dependency graph is constructed within a basic block.  It cannot reach
across block boundaries or across function call boundaries.  At these edges the
tool uses the **ABI (Application Binary Interface)** to fill in what it cannot
observe directly:

- **Function entry**: argument registers (`a0`–`a7`, `fa0`–`fa7`) are assumed
  live-in — a value may be in them that the first instruction of the function
  must not overwrite before reading.

- **Function exit** (return): return-value registers (`a0`/`a1`, `fa0`/`fa1`)
  and callee-saved registers (`s0`–`s11`, `fs0`–`fs11`) are assumed live-out —
  their values must be preserved across the function.

- **Call sites**: a call instruction is assumed to read all argument registers
  (the values passed to the callee) and to clobber all caller-saved registers
  (which the callee may freely overwrite).  Callee-saved registers are assumed
  to be preserved across the call.

These ABI assumptions are the basis on which liveness analysis produces
live-in and live-out sets for each block, which in turn determine which
inter-block register dependencies must be honoured even though the instructions
on the other side of the boundary are not visible to the scheduler.

---

## 3. Scope of reordering

The scheduler reorders instructions within a basic block.  It does not attempt
to move instructions across block boundaries or hoist/sink them across branches.
The rationale is that the tool processes compiler output: a compiler already made
those larger-scope decisions, and second-guessing them risks correctness problems
(hoisting across a branch requires knowing the branch condition is always taken,
etc.) for marginal gain.  The tool's job is to pack what the compiler gave it
as efficiently as possible, not to re-optimise the compiler's code structure.

---

## 4. Unknown instructions

An instruction is *unknown* when its mnemonic is not in the recognised opcode
table.  This most commonly means a custom extension, a newer standard extension
not yet in the decoder, or a vendor-specific intrinsic.

**Pairing**: unknown instructions are disqualified from both packet slots (§1).
The rationale is that their operands, side effects, and structural constraints
are all unverified.

**Dependency analysis**: the tool applies best-effort positional decoding to
unknown instructions — register-name tokens are assigned to `rd`/`rs1`/`rs2`
in order, and `offset(base)` syntax is recognised as a memory operand.  This
is likely to be correct for instructions that follow established RISC-V
operand conventions, since most custom extensions do.  The consequence of
a wrong decode is a missing dependency edge, which could produce an
incorrectly reordered output.

**Policy**: when an unknown instruction appears in real input, the correct
response is to add it to the decoder, not to rely on the heuristic.  The
heuristic is an interim measure that makes the tool useful on unfamiliar input
while the gap is being investigated, not a permanent substitute for correct
decoding.  Unknown instructions are annotated with `[?]` in the output to make
them visible.

---

## 5. Labels as block boundaries

A label is treated as a basic-block boundary (barrier) if it could be the
target of a control-flow transfer from somewhere not immediately preceding it.
The prescan identifies barriers from:

- Branch and jump targets appearing directly in instruction operands.
- `.globl` and `.weak` directives (the label may be called from outside the
  translation unit).
- Data directives that reference a label *without arithmetic* — e.g.
  `.word .Lfoo` makes `.Lfoo` a barrier because something may branch to it
  via a computed address loaded from the table.  However, `.word .Lfoo - .Lbar`
  or `.word .Lfoo + 4` contains arithmetic and is a PCREL or offset expression;
  the label is not the target of a branch, only a value used in a calculation,
  so it is **not** treated as a barrier.

---

## 6. Open questions

The following design decisions have not yet been resolved.  They are noted here
so that they are not silently assumed.

**ABI target**: the tool assumes the standard RISC-V psABI, including `t0` (x5)
as an alternate link register used by linker call stubs.  If the target uses a
non-standard calling convention (firmware, custom RTOS, etc.) the ABI tables in
`isa/abi.py` and `isa/registers.py` would need to be updated.  It is not yet
decided whether supporting multiple ABI profiles is a goal.

**Memory ordering conservatism**: the default memory model is conservative —
every load/store pair is ordered unless `--same-base-reorder` is passed.  For
the primary goal of measuring pairing rates, this default may systematically
suppress pairing opportunities in memory-heavy code.  It is not yet decided
whether the measured pairing rates should reflect "safe by default" or "best
achievable with explicit relaxation".

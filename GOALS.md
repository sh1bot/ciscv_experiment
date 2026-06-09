# Goals: What This Tool Is Supposed To Do

This document states the purpose and key assumptions of the scheduler at a
level above the implementation plan.  It is intended to be stable: changes here
represent a change in what the tool is for, not just how it works.

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
  - Whether it is eligible for **RVC** (compressed 16-bit) encoding, if that
    applies.
  - Whether it is an **unknown instruction** — one whose opcode is not
    recognised, which means its register reads, writes, and side effects cannot
    be determined.  Unknown instructions are treated conservatively.
  - The **reasons it was not paired**: for each pairing rule that was
    considered and rejected, a short explanation of which constraint was not
    met.  This is diagnostic information; it lets the user understand what
    changes to the source would enable pairing.

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

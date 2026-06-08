# RISC-V Instruction Scheduler — Design Plan

## Purpose

This tool reads RISC-V assembly source and produces annotated assembly output in
which instructions have been greedily paired into 32-bit packets.  A packet
contains either one solo instruction (occupying a full 32-bit word) or two
instructions sharing a single 32-bit word.  All instruction boundaries remain
32-bit aligned.  The pairing rules are the primary experimental artefact of the
project; they are deliberately isolated so that iterating on them requires only
local edits to one file.

RVC eligibility is computed and annotated independently.  It has no effect on
pairing decisions — it is informational only.

---

## Project Layout

```
rv_scheduler/
├── __main__.py
├── isa/
│   ├── registers.py          # register indices, ABI names, calling-convention sets
│   ├── abi.py                # call_liveness_effect() — ABI-implied use/def sets
│   ├── instruction.py        # Instruction base class + all computed properties
│   └── decode/
│       ├── __init__.py       # public parse_line() factory
│       ├── rv_i.py           # RV32I / RV64I
│       ├── rv_m.py           # M extension
│       ├── rv_a.py           # A extension
│       ├── rv_f.py           # F extension
│       ├── rv_d.py           # D extension
│       ├── rv_c.py           # C extension + rvc_eligible logic
│       ├── rv_b.py           # B (Zba / Zbb / Zbc / Zbs)
│       ├── rv_v.py           # V extension
│       └── rv_zicsr.py       # Zicsr
├── analysis/
│   ├── cfg.py                # basic-block + function identification, CFG edges
│   ├── depgraph.py           # per-block RAW/WAR/WAW dependency graph
│   └── liveness.py           # iterative liveness dataflow, per-instruction sets
├── scheduler/
│   ├── pairing.py            # can_pair(a, b) -> None | reason_str; greedy pass
│   ├── rules.py              # PairingRule dataclass + all concrete rules
│   └── reorder.py            # list scheduling (default) + BnB (--thorough)
└── output/
    └── annotator.py          # formats annotated assembly output
```

---

## 1. `isa/registers.py`

Define integer and float register sets.  Map every ABI name and `x`/`f` numeric
alias to a canonical integer index (0–31 for each bank).

```python
REG_ALIASES: dict[str, int]   # "zero","ra","sp",…,"x0"–"x31" -> 0–31
FREG_ALIASES: dict[str, int]  # "ft0"–"ft11","fs0"–"fs11","fa0"–"fa7" -> 0–31

CALLER_SAVED: frozenset[int]  # ra, t0–t6, a0–a7  (integer)
CALLEE_SAVED: frozenset[int]  # sp, s0–s11        (integer)
ARG_REGS:     list[int]       # a0–a7 in order
RET_REGS:     frozenset[int]  # a0, a1

FCALLER_SAVED: frozenset[int] # ft0–ft11, fa0–fa7
FCALLEE_SAVED: frozenset[int] # fs0–fs11
FARG_REGS:     list[int]      # fa0–fa7 in order
FRET_REGS:     frozenset[int] # fa0, fa1
```

Provide a helper `reg_name(index) -> str` returning the canonical ABI name.

---

## 2. `isa/abi.py`

A single function consumed by the liveness pass:

```python
def call_liveness_effect(insn: Instruction) -> tuple[
    frozenset[int],  # implicit integer uses
    frozenset[int],  # implicit integer defs (clobbered)
    frozenset[int],  # implicit float uses
    frozenset[int],  # implicit float defs
    frozenset[int],  # live_out_seed: integer registers conservatively live after this insn
    frozenset[int],  # live_out_seed: float registers conservatively live after this insn
    bool,            # terminates_function
]:
```

A call is modelled as a single very busy instruction: it reads all argument
registers (integer and float) and `sp`, clobbers all caller-saved registers
(integer and float), and after it returns, the caller must assume all
callee-saved registers and both return registers are live.  The `live_out_seed`
fields capture this post-call conservative assumption separately from the
`implicit uses` (which drive dep-graph edges *into* the call, not *out* of it).

For terminating instructions (`terminates_function = True`) there are no
successors and `live_out_seed` equals `implicit uses` — what the frame reads
on exit is what must be live.  For non-terminating calls, `live_out_seed` is
the post-return live set rather than the pre-call argument set.

Detection priority: mnemonic is checked first; structural `jalr`/`jal` patterns
are the fallback for assembler output that has already expanded pseudo-instructions.

| Case | Detection | Implicit int uses / float uses | Implicit int defs / float defs | Live-out seed (int / float) | Terminates |
|---|---|---|---|---|---|
| Call (pseudo) | mnemonic `"call"` | `ARG_REGS ∪ {sp}` / `FARG_REGS` | `CALLER_SAVED` / `FCALLER_SAVED` | `RET_REGS ∪ CALLEE_SAVED` / `FRET_REGS ∪ FCALLEE_SAVED` | False |
| Call (encoded) | `jal`/`jalr` writing `ra` (x1) or `t0` (x5) | same | same | same | False |
| Return (pseudo) | mnemonic `"ret"` | `RET_REGS ∪ CALLEE_SAVED` / `FRET_REGS ∪ FCALLEE_SAVED` | ∅ / ∅ | (same as uses) | True |
| Return (encoded) | `jalr x0, ra, 0` | same | same | same | True |
| Tail call (pseudo) | mnemonic `"tail"` | `ARG_REGS ∪ {sp}` / `FARG_REGS` | ∅ / ∅ | (same as uses) | True |
| Tail call (encoded) | `jalr x0, rs1` where rs1 ≠ ra | same | same | same | True |
| Indirect jump | `jalr x0, rs1` (conservative: not a recognisable tail call) | `{sp}` / ∅ | ∅ / ∅ | (same as uses) | True |

`sp` (x2) is in `CALLEE_SAVED` and therefore already present in the return and
tail-call use sets; it is listed explicitly in the call and indirect-jump rows
where it would otherwise be absent.

**Unclassified `jalr`.**  A `jalr` that writes a register other than `x0`, `ra`,
or `t0` does not match any row above.  Such instructions are exotic in practice
(no standard RISC-V ABI usage), but when they occur they are treated as no-ops
from an ABI perspective: `call_liveness_effect()` returns all-empty sets and
`terminates_function = False`.  The dep-graph edges from the encoded register
operands still constrain ordering correctly for any downstream use of the link
value.

`t0` (x5) is treated as a link register alongside `ra` (x1) because the RISC-V
psABI designates it as the alternate link register for linker call stubs.

All argument registers are conservatively assumed live at every call site since
the precise argument count is not known from assembly text alone.

---

## 3. `isa/instruction.py` — `Instruction` base class

```python
@dataclass
class Instruction:
    mnemonic:      str
    operands:      list[str]
    raw:           str            # original source line, reproduced verbatim on output
    label:         str | None     # label on this line, if any

    # Non-instruction lines (assembler-internal labels such as .Lpcrel_hi*)
    # that immediately precede this instruction in the source are stored here
    # so they travel with the instruction if it is reordered.
    prefix_lines:  list[str] = field(default_factory=list)

    # Populated by the decoder subclass:
    rd:   int | None              # integer destination register index
    rs1:  int | None
    rs2:  int | None
    rs3:  int | None              # FMA etc.
    frd:  int | None              # float destination
    frs1: int | None
    frs2: int | None
    frs3: int | None
    imm:  int | None
    branch_target: str | None     # label string for branches / jumps
    is_unknown:    bool = False   # True for unrecognised mnemonics (best-effort decode)

    # Populated by the liveness pass (initially empty):
    live_in:   frozenset[int] = field(default_factory=frozenset)
    live_out:  frozenset[int] = field(default_factory=frozenset)
    flive_in:  frozenset[int] = field(default_factory=frozenset)
    flive_out: frozenset[int] = field(default_factory=frozenset)

    # Populated by the pairing pass (initially empty):
    solo_reasons: set[str] = field(default_factory=set)  # rejection strings from can_pair()
```

### Computed properties

All are derived from the stored fields; none are stored separately.

| Property | Definition |
|---|---|
| `is_rsd` | `rd is not None and (rd == rs1 or rd == rs2)` — True when the destination register also appears as a source operand; no parse-time operand normalisation is performed |
| `is_commutative` | mnemonic is one of `add`, `mul`, `mulh`, `mulhu`, `mulhsu`, `and`, `or`, `xor`, `min`, `minu`, `max`, `maxu`, `fadd`, `fmul` and their `*w` variants |
| `reads_rd` | instruction reads `rd` before writing it — i.e. AMO instructions (`amoadd`, `amoswap`, `amoor`, etc.) which atomically read and update their destination |
| `writes_rd` | `rd is not None` |
| `writes_memory` | store instruction |
| `reads_memory` | load instruction |
| `is_branch` | conditional branch (two CFG successors) |
| `is_jump` | unconditional jump (JAL, JALR — one or zero successors) |
| `is_call` | JAL/JALR writing `ra` (x1) or `t0` (x5) |
| `is_return` | `jalr x0, ra, 0` |
| `is_csr` | accesses a CSR |
| `is_fence` | FENCE / FENCE.I |
| `is_atomic` | A-extension instruction |
| `has_side_effects` | `is_call or is_return or writes_memory or is_csr or is_fence or is_atomic` |
| `uses_int` | `frozenset` of integer registers read by this instruction, **excluding x0** |
| `defs_int` | `frozenset` of integer registers written by this instruction, **excluding x0** |
| `uses_float` | `frozenset` of float registers read |
| `defs_float` | `frozenset` of float registers written |
| `rvc_eligible` | see §5 |
| `rs1_in_rvc_range` | `rs1 is not None and rs1 in range(8, 16)` |
| `rs2_in_rvc_range` | `rs2 is not None and rs2 in range(8, 16)` |
| `rd_in_rvc_range` | `rd is not None and rd in range(8, 16)` |

**x0 is excluded from `uses_int` and `defs_int`** — writes to x0 are no-ops and
reads of x0 always produce zero, so x0 participates in no data dependencies and
must not appear in liveness sets (otherwise it would be "live" everywhere and
pollute every live_in/live_out set in the file).  However, the raw decoded fields
`rd`, `rs1`, `rs2` *may* still hold index 0 when the instruction architecturally
references x0 (e.g. `jalr x0, ra, 0` has `rd = 0`, `jalr x1, x0, target` has
`rs1 = 0`).  These raw fields are needed for instruction classification (e.g.
`is_return` checks `rd == 0`) and RVC eligibility checks; they are just excluded
from the `uses_int`/`defs_int` computed sets.

Helper predicates (used internally by decoders and rules):

```python
def imm_bits(self, n: int) -> bool:   # imm fits in a signed n-bit field
def uimm_bits(self, n: int) -> bool:  # imm fits in an unsigned n-bit field
```

---

## 4. `isa/decode/` — Decoders

### Two-pass parsing

The parser makes two passes over the source file before decoding any
instructions:

1. **Pre-scan:** collect all label definitions, classify each as a *barrier
   label* (appears as a branch/jump target anywhere in the file, or declared by
   `.globl` / `.weak`) or a *non-barrier label* (never a branch target and not
   globally visible).  Assembler-internal labels (`.Lpcrel_hi*`, `.Lfunc_end*`,
   `.Ltmp*`) are almost always non-barriers.
2. **Decode pass:** parse instructions.  Non-barrier labels encountered on their
   own line are accumulated as `prefix_lines` on the immediately following
   instruction.  Barrier labels split basic blocks.  If a barrier label is
   encountered while a non-barrier prefix buffer is non-empty, the buffered
   lines are attached as `prefix_lines` on the first instruction of the *new*
   block (not lost and not associated with the preceding block).  If a
   non-barrier label appears at the end of the file with no following instruction
   (orphaned), it is emitted verbatim at the end of the output as a trailing
   line — it has nothing to attach to and is not a pairing consideration.

This ordering is necessary because `prefix_lines` attachment requires knowing
barrier status upfront, and barrier status requires seeing all branch targets
before any instruction is decoded.

### Decoder interface

Each module exports:

```python
def match(mnemonic: str) -> type[Instruction] | None
```

`decode/__init__.py` calls each module's `match()` in extension-priority order.
If no module matches, an `UnknownInstruction` subclass is returned (not raised)
using best-effort decoding (see below) — unrecognised mnemonics are never fatal.

### Best-effort decoding of unknown instructions

When no decoder matches a mnemonic, the instruction is decoded as follows:

- Scan the operand list left-to-right, parsing each token as a register name if
  possible and ignoring any token that is not a valid register name.
- `defs_int` = the first register-like operand, if any, **excluding x0**.
- `uses_int` = all remaining register-like operands, **excluding x0**.
- `is_unknown = True` — the annotator will emit a `[?]` marker on output.
- The instruction is **not** treated as a barrier in the dependency graph.
  It participates via its inferred `defs_int` / `uses_int` (RAW/WAR/WAW edges
  only) and may be reordered if those edges permit.  Side-effect and memory
  behaviour is unknown, so the instruction is treated as both reading and
  writing memory (conservative memory ordering edge against all adjacent
  memory operations), but it does not anchor all other instructions to it the
  way a true barrier (fence, AMO, ecall) does.
- A `[?]` annotation appears in the output for every unknown instruction.
- A warning is emitted on stderr: `warning: unknown mnemonic '<mnemonic>'`
  on the first occurrence of each unknown mnemonic.  If the inferred register
  effects are wrong, the instruction should be added to the known-instruction
  tables.

### Pseudo-instruction handling

`nop`, `mv`, `li`, `ret`, `neg`, `not`, `seqz`, `snez`, `sltz`, `sgtz` and
similar single-instruction pseudo-instructions are expanded to their canonical
equivalents during parsing.  All downstream code sees only real instructions.

`call` and `tail` are **not** expanded — they are retained as single
instructions.  Their ABI effects (argument registers used, caller-saved registers
clobbered) are handled by `call_liveness_effect()` in `isa/abi.py`.

### C-extension mnemonic handling

C-extension instructions fall into two categories:

1. *Aliases* (e.g. `c.add`, `c.addi`, `c.srli`): the compressed form implies
   `rd == rs1`.  The decoder splices in the implicit `rs1 = rd` operand and
   delegates to the corresponding base-ISA table entry.
2. *Canonical forms* (e.g. `c.mv`, `c.li`, `c.jr`, `c.jal`): have their own
   table entries and are renamed to a canonical base-ISA mnemonic after decode
   (`c.mv` → `add`, `c.jr` → `jalr`, etc.).

Both categories ultimately produce a base-ISA `Instruction`; no compressed
mnemonic survives past `parse_line`.

---

## 5. `isa/decode/rv_c.py` — RVC eligibility

The `rvc_eligible` property is computed by testing the instruction against every
RVC encoding rule from the RISC-V C extension specification and returning `True`
if any rule matches.

**Branch and jump offset ranges are not checked.**  Label addresses are unknown
at schedule time, so the offset constraints for `c.beqz` (±256 B), `c.bnez`
(±256 B), `c.j` (±2 KB), and `c.jal` (±2 KB) are skipped.  The annotation is
therefore an optimistic estimate for branches.

**No architecture gating.**  RV32-only rules (`c.jal`) and RV64-only rules
(`c.addiw`, `c.ld`, etc.) are checked unconditionally.  Since each RVC rule maps
to a distinct base-ISA mnemonic, the rules never conflict:  `c.jal` only fires
for `jal x1, offset` and `c.addiw` only fires for `addiw rd, rd, imm` — these
are different instructions.  The annotation may be slightly optimistic in edge
cases (e.g. `jal x1` annotated `[C]` on RV64 code where that encoding slot is
taken by `c.addiw`), but since `rvc_eligible` is informational only this is
acceptable.

Key rules (exhaustive list per spec; `rd'`/`rs1'`/`rs2'` means register index
in `range(8, 16)`):

| RVC encoding | Condition on the base instruction |
|---|---|
| `c.addi4spn rd', nzuimm` | `addi rd, x2, nzuimm` — rd' in 8–15, nzuimm nonzero, multiple of 4, ≤ 1020 |
| `c.lw rd', imm(rs1')` | `lw rd, imm(rs1)` — both in 8–15, uimm fits 7 bits (4-byte aligned) |
| `c.sw rs2', imm(rs1')` | `sw rs2, imm(rs1)` — both in 8–15, uimm fits 7 bits (4-byte aligned) |
| `c.ld rd', imm(rs1')` | RV64: `ld rd, imm(rs1)` — both in 8–15, uimm fits 8 bits (8-byte aligned) |
| `c.sd rs2', imm(rs1')` | RV64: `sd rs2, imm(rs1)` — both in 8–15, uimm fits 8 bits (8-byte aligned) |
| `c.addi rd, nzimm` | `addi rd, rd, imm` — `is_rsd`, rd ≠ x0, imm ≠ 0, fits 6 signed bits |
| `c.addiw rd, imm` | RV64: `addiw rd, rd, imm` — `is_rsd`, rd ≠ x0, fits 6 signed bits |
| `c.li rd, imm` | `addi rd, x0, imm` — rd ≠ x0, fits 6 signed bits |
| `c.addi16sp imm` | `addi x2, x2, imm` — `is_rsd`, rd = x2, imm ≠ 0, fits 10 signed bits (16-byte aligned) |
| `c.lui rd, nzimm` | `lui rd, nzimm` — rd ≠ x0, rd ≠ x2, nzimm ≠ 0, fits 6 signed bits (×4096) |
| `c.srli rd', shamt` | `srli rd, rd, shamt` — `is_rsd`, rd' in 8–15, 1 ≤ shamt ≤ 31 |
| `c.srai rd', shamt` | `srai rd, rd, shamt` — `is_rsd`, rd' in 8–15, 1 ≤ shamt ≤ 31 |
| `c.andi rd', imm` | `andi rd, rd, imm` — `is_rsd`, rd' in 8–15, fits 6 signed bits |
| `c.sub rd', rs2'` | `sub rd, rd, rs2` — `is_rsd`, both in 8–15 |
| `c.xor rd', rs2'` | `xor rd, rd, rs2` — `is_rsd`, both in 8–15 |
| `c.or  rd', rs2'` | `or rd, rd, rs2` — `is_rsd`, both in 8–15 |
| `c.and rd', rs2'` | `and rd, rd, rs2` — `is_rsd`, both in 8–15 |
| `c.subw rd', rs2'` | RV64: `subw rd, rd, rs2` — `is_rsd`, both in 8–15 |
| `c.addw rd', rs2'` | RV64: `addw rd, rd, rs2` — `is_rsd`, both in 8–15 |
| `c.j offset` | `jal x0, offset` — (offset range not checked) |
| `c.jal offset` | RV32 only: `jal x1, offset` — (offset range not checked) |
| `c.beqz rs1', offset` | `beq rs1, x0, offset` — rs1' in 8–15 (offset range not checked) |
| `c.bnez rs1', offset` | `bne rs1, x0, offset` — rs1' in 8–15 (offset range not checked) |
| `c.slli rd, shamt` | `slli rd, rd, shamt` — `is_rsd`, rd ≠ x0, 1 ≤ shamt ≤ 31 |
| `c.lwsp rd, imm` | `lw rd, imm(x2)` — rd ≠ x0, uimm fits 8 bits (4-byte aligned) |
| `c.ldsp rd, imm` | RV64: `ld rd, imm(x2)` — rd ≠ x0, uimm fits 9 bits (8-byte aligned) |
| `c.jr rs1` | `jalr x0, rs1, 0` — rs1 ≠ x0 |
| `c.mv rd, rs2` | `add rd, x0, rs2` — rd ≠ x0, rs2 ≠ x0 |
| `c.nop` | `addi x0, x0, 0` — rd = x0, imm = 0 |
| `c.ebreak` | `ebreak` |
| `c.jalr rs1` | `jalr x1, rs1, 0` — rs1 ≠ x0 |
| `c.add rd, rs2` | `add rd, rd, rs2` — `is_rsd`, rd ≠ x0, rs2 ≠ x0 |
| `c.swsp rs2, imm` | `sw rs2, imm(x2)` — uimm fits 8 bits (4-byte aligned) |
| `c.sdsp rs2, imm` | RV64: `sd rs2, imm(x2)` — uimm fits 9 bits (8-byte aligned) |

**Float RVC encodings (`c.flw`, `c.fsw`, `c.fld`, `c.fsd`, `c.flwsp`, `c.fswsp`,
`c.fldsp`, `c.fsdsp`) are out of scope** — they require float-register range checks
that interact with the separate float-register bank, and their encoding slots
overlap with integer RVC encodings in a way that is arch-dependent (RV32D vs RV64C).
They are not included in `rvc_eligible` computation.

---

## 6. `analysis/cfg.py` — Basic blocks and functions

### Linear scan

1. Collect all labels and the instruction indices they precede.
2. Identify *barrier labels*: those that appear as branch/jump targets anywhere
   in the file, or that are globally visible (`.globl` / `.weak`).
   Assembler-internal labels that are never branch targets are **not** barrier
   labels and do not split blocks.  (See §4 two-pass parsing.)
3. Mark block-start indices: the first instruction, every instruction that
   follows a branch, jump, return, `call`, or `tail`, and every instruction
   preceded by a barrier label.
4. Group consecutive instructions between boundaries into `BasicBlock` objects.

```python
@dataclass
class BasicBlock:
    labels:             list[str]      # all labels at this address (may be empty or multiple)
    instructions:       list[Instruction]
    successors:         list[BasicBlock]
    predecessors:       list[BasicBlock]
    is_function_entry:  bool           # True when preceded by a .globl directive
```

### CFG edges

| Terminator | Successors |
|---|---|
| Conditional branch | fall-through block + branch-target block |
| `jal` (not a call) | one successor (the target label) |
| `jalr` (not a call) | no successors — target is register-computed; treat as function exit |
| `ret` / `jalr x0, ra, 0` | no successors |
| `call` / `jal x1` | fall-through only (callee is opaque) |
| `tail` | no successors — tail call terminates function |
| Fall-through (non-terminator last instruction) | next block |

### Function grouping

```python
@dataclass
class Function:
    name:    str
    entry:   BasicBlock
    blocks:  list[BasicBlock]
```

A function begins at each `.globl`-declared label.  Its blocks are all those
reachable from the entry block before the next `.globl` entry.  Blocks reachable
from multiple `.globl` entries (e.g. via tail calls) are assigned to all
reachable functions conservatively.

---

## 7. `analysis/liveness.py` — Register liveness

**Two-phase approach.**  The CFG liveness pass runs once over the whole file
before per-block scheduling begins, producing a `live_out` table keyed by block.
This global pass correctly handles conditional branches, loop back-edges,
and fall-throughs.  Per-block scheduling then consults this table for the block's
`live_out` seed before doing its local backward propagation.

The table is keyed by `BasicBlock` identity (object reference), not by label
string, so blocks with zero labels or multiple labels are handled uniformly.

**Liveness must be recomputed after reordering.**  Pairing rules consult
per-instruction `live_in`/`live_out`.  After the reorder pass changes instruction
order, these fields are stale.  The local backward pass is re-run on the
post-schedule ordered list before the greedy-advance pairing pass runs.

### Global pass

Standard iterative backward dataflow over the CFG, per function, using a
worklist.  Integer and float register sets are maintained as separate parallel
`frozenset[int]` domains (`live_in`/`live_out` for integer,
`flive_in`/`flive_out` for float) and iterated together.

```
live_in[B]  = use[B]  ∪  (live_out[B] − def[B])
live_out[B] = ⋃  live_in[S]   for S in successors(B)
```

**ABI injection.**  `call_liveness_effect()` (§2) is the single source of truth
for all ABI-related liveness effects.  It is called on every instruction when
building `use[B]` / `def[B]`:

- `implicit uses` → merged into `use[B]` (integer and float separately).
- `implicit defs` → merged into `def[B]`.
- `terminates_function = True` → the block is treated as having no successors,
  so `live_out[B]` is determined entirely by its seed (see below).

**Initial seeds.**  Before iteration begins, each block's `live_out` is
pre-loaded with the `live_out_seed` from `call_liveness_effect()` applied to
the block's last instruction (if that instruction has ABI effects).  This
accounts for registers that are conservatively live after a call or on function
exit, independent of what the rest of the function observes.  The seed values
come directly from the §2 table; there is no separate seed specification.

**Function-entry seed.**  Blocks with `is_function_entry = True` have their
`live_in` pre-loaded with `ARG_REGS ∪ FARG_REGS` (integer and float argument
registers respectively), since the caller may have passed values in any of them.
`is_function_entry` is set for blocks headed by `.globl`-declared labels.
`.weak` symbols are also treated as function entries for this purpose, since
weak aliases are commonly used as alternate entry points with the same calling
convention.

**`ret` seed conservatism.**  The `live_out_seed` for a return instruction is
`CALLEE_SAVED ∪ FCALLEE_SAVED ∪ RET_REGS ∪ FRET_REGS`.  Including the full
callee-saved sets is intentionally conservative: it ensures that any instruction
that restores a callee-saved register (e.g. `lw s0, 0(sp)`) has a dep-graph edge
to the `ret`, preventing reordering that would place the restore after the return.
It does not prevent dead callee-saved registers from being scheduled freely
earlier in the function — the scheduler does not eliminate dead writes, only
orders instructions.

### Local pass

After global fixed-point, a backward pass within each block assigns `live_in` /
`live_out` to every individual `Instruction`, using the block's global `live_out`
as the seed.  The same `call_liveness_effect()` injection is applied at the
instruction level during this pass.

---

## 8. `analysis/depgraph.py` — Dependency graph

Per basic block, build a directed acyclic graph of ordering constraints needed
for correctness.

**Edge types:**
- **RAW** (read-after-write): instruction B reads a register that A writes.
- **WAR** (write-after-read): instruction B writes a register that A reads.
- **WAW** (write-after-write): both write the same register.
- **Memory ordering**: by default, each memory operation has a dependency edge
  from the previous memory operation in program order (conservative).  With
  `--same-base-reorder`, two mem-ops with the same base register — provided that
  register is not written between them **and no unknown instruction (`is_unknown`)
  appears between them** (unknown instructions may have undeclared memory effects)
  — and non-overlapping byte ranges (same offset ± access width) have this edge
  dropped.
- **Barrier edges**: AMOs, `fence`, `fence.i`, `ecall`, `ebreak`, `call`,
  and `tail` instructions get ordering edges from all preceding instructions
  in the block.  Unknown instructions (`is_unknown`) are **not** treated as
  full barriers; they receive conservative memory ordering edges against all
  adjacent memory operations (see below), but other non-memory instructions
  may still be reordered around them if the register-dependency edges permit.
- **Unknown-instruction memory edges**: `build_dep_graph` checks `is_unknown`
  directly — it does not rely on `reads_memory` or `writes_memory`, which are
  only set for recognised load/store mnemonics.  When an unknown instruction
  is present, a memory ordering edge is inserted between it and every
  memory operation that precedes or follows it within the block, conservatively
  treating it as both a load and a store.

```python
@dataclass
class DepGraph:
    instructions: list[Instruction]
    # edges[i] = set of instruction indices that must come after instructions[i]
    edges: list[set[int]]

def build_dep_graph(block: BasicBlock, same_base_reorder: bool = False) -> DepGraph:
    ...
```

The list scheduler and BnB both operate on a `DepGraph` and only emit orderings
that are topological sorts of it.

---

## 9. `scheduler/rules.py` — Pairing rule definitions

```python
@dataclass
class PairingRule:
    name: str

    # Properties that must be True on the A-slot instruction (first of pair).
    # If any fails, this rule is skipped (not applicable — does not reject).
    a_prerequisites: list[str] = field(default_factory=list)

    # Properties that must be True on the B-slot instruction (second of pair).
    b_prerequisites: list[str] = field(default_factory=list)

    # Returns None  -> this rule does not block the pair.
    # Returns str   -> the pair is rejected; the string is the reason.
    check: Callable[[Instruction, Instruction], str | None]
```

### Slot asymmetry

The A and B slots have fundamentally different constraints and many rules apply
asymmetrically.  `a_prerequisites` and `b_prerequisites` are separate lists to
make this a first-class design consideration rather than an afterthought.

### Per-slot disqualifiers

Some instructions are ineligible for a given slot regardless of what the other
instruction is.  These are expressed as two configurable lists of instruction
property names in `rules.py`:

```python
A_SLOT_DISQUALIFIERS: list[str]  # if any is True on a, a cannot be A-slot
B_SLOT_DISQUALIFIERS: list[str]  # if any is True on b, b cannot be B-slot
```

`can_pair()` checks these lists first, before evaluating any `PairingRule`.  If
`a` is disqualified, the function returns immediately without examining `b` at
all — there is no point searching for a B-slot partner when the A-slot candidate
is already known to be ineligible.

Initial contents (subject to change as rules evolve):

```python
A_SLOT_DISQUALIFIERS = [
    "is_branch",    # branch in A-slot: the processor leaves before B executes
    "is_jump",      # same applies to unconditional jumps and returns
    "is_return",
    "is_call",      # calls belong in B-slot only (see below)
]

B_SLOT_DISQUALIFIERS = [
    # none initially — to be populated as constraints are discovered
]
```

Calls are A-slot disqualified because placing a call in A-slot would transfer
control before the B-slot instruction executes.  In B-slot a call executes after
the A-slot instruction completes, which is the intended sequencing.

**Branches and jumps in B-slot** are explicitly permitted by the packet format:
the B-slot instruction executes after A-slot, so a branch or jump in B-slot
redirects control after A has committed — which is correct.  This is why
`is_branch`, `is_jump`, and `is_return` appear only in `A_SLOT_DISQUALIFIERS`
and not in `B_SLOT_DISQUALIFIERS`.  Pairing an ALU instruction in A-slot with a
branch in B-slot is a meaningful optimisation target.

### Rule evaluation contract

A rule is only *applicable* when every property in `a_prerequisites` is True on
`a`, and every property in `b_prerequisites` is True on `b`.  An inapplicable
rule is silently skipped — it neither approves nor rejects.  Only an applicable
rule that returns a non-`None` string constitutes a rejection.

Prerequisites are a filtering mechanism, not an approval mechanism.  The overall
`can_pair()` function uses a **blocklist model**: a pair is approved unless at
least one applicable rule explicitly rejects it.  A pair for which no rule's
prerequisites match is therefore approved by default.  This is intentional —
the rule set is expected to be complete enough for the instruction combinations
the project targets, and new rules are added when a pairing that should be
rejected slips through.

### Example rule — RSD ALU pair

As a worked example, consider pairing two instructions where both have the form
`{add, sub, and, or, xor} rsd, rs2` — i.e. both are two-register ALU ops where
the destination is also a source (no immediate, no load/store).  This is the
simplest non-trivial rule: it expresses that two independent accumulator-style
operations can share a packet.

The `is_rsd` property is True when `rd` also appears as `rs1` or `rs2`.  The
mnemonic filter is expressed via a prerequisite property `is_rsd_alu_pair_eligible`
(or inline in the check lambda).  The only correctness constraint is that neither
instruction writes a register the other reads or writes (RAW/WAW) — which is
expressed by the check function:

```python
PairingRule(
    name="rsd-alu-pair",
    a_prerequisites=["is_rsd"],
    b_prerequisites=["is_rsd"],
    check=lambda a, b: (
        None
        if (a.mnemonic in {"add","sub","and","or","xor",
                           "addw","subw"}
            and b.mnemonic in {"add","sub","and","or","xor",
                               "addw","subw"}
            and not (a.defs_int & b.uses_int)   # no RAW a→b
            and not (b.defs_int & a.uses_int)   # no WAR b→a
            and not (a.defs_int & b.defs_int))  # no WAW
        else "rsd-alu-pair: register conflict or mnemonic mismatch"
    ),
)
```

Note that this rule is intentionally narrow.  Widening it (e.g. allowing one
side to have an immediate, or allowing load/ALU pairs) is done by adding more
rules, not by expanding this one.

### Adding / removing rules

All of `RULES`, `A_SLOT_DISQUALIFIERS`, and `B_SLOT_DISQUALIFIERS` live in
`rules.py`.  This is the only file that needs to change when iterating on
pairing policy.

---

## 10. `scheduler/pairing.py`

### `can_pair()`

```python
def can_pair(a: Instruction, b: Instruction) -> str | None:
    """Return None if a and b may share a 32-bit packet,
    or a short reason string if not."""
    # Per-slot disqualifiers: fast-out without examining the other instruction.
    for prop in A_SLOT_DISQUALIFIERS:
        if getattr(a, prop):
            return f"A-slot disqualified: {prop}"
    for prop in B_SLOT_DISQUALIFIERS:
        if getattr(b, prop):
            return f"B-slot disqualified: {prop}"

    for rule in RULES:
        if not all(getattr(a, p) for p in rule.a_prerequisites):
            continue
        if not all(getattr(b, p) for p in rule.b_prerequisites):
            continue
        result = rule.check(a, b)
        if result is not None:
            return result
    return None
```

### Greedy-advance pairing model

The fundamental pairing model is *greedy-advance*: maintain a single `free`
candidate (the last unmatched instruction).

```
free = None
for curr in instructions:               # ← loop over ordered instruction list
    if free is not None and can_pair(free, curr) is None:
        emit (free, curr) as pair packet
        free = None
    else:
        if free is not None:
            emit free as solo packet
        free = curr
if free is not None:                    # ← after loop: flush last unmatched
    emit free as solo packet
```

A solo instruction never blocks the following instruction from pairing.  The
list scheduler and BnB (§11) find the instruction ordering over the block's
dependency graph that maximises the number of pairs under this exact model.

### Rejection reason collection

After the reorder pass and liveness recomputation, the pairing pass runs the
greedy-advance model and simultaneously populates `solo_reasons` on each
unpaired instruction.  The accumulation loop is:

```
for each instruction insn emitted solo:
    # examine all instructions that were considered as pairing candidates
    for each candidate cand that was tested against insn:
        reason = can_pair(insn, cand)   # insn as A-slot
        if reason: insn.solo_reasons.add(reason)
        reason = can_pair(cand, insn)   # insn as B-slot
        if reason: insn.solo_reasons.add(reason)
```

In `--fast` (forward-scan) mode each instruction has at most one adjacent
candidate, so `solo_reasons` holds at most one string.  In list-scheduling and
BnB modes the ready queue may offer multiple candidates, and all tested
rejections are accumulated.  Using a set deduplicates identical reasons from
different candidates.

---

## 11. `scheduler/reorder.py` — Instruction reordering

Three scheduling strategies share the same interface:

```python
class ScheduleMode(enum.Enum):
    FORWARD  = "forward"   # --fast: source order, no reordering
    LIST     = "list"      # default: list scheduling with lookahead
    BNB      = "bnb"       # --thorough: branch-and-bound

def schedule(block: BasicBlock, graph: DepGraph, mode: ScheduleMode) -> list[Instruction]:
    """Return a topological sort of graph that maximises pair count.
    In FORWARD mode, graph is unused and may be None."""
```

All three feed their output into the same greedy-advance pairing pass (§10).

---

### Mode 1 — Forward-scan (no reordering)

Instructions are emitted in source order.  The `graph` parameter is ignored; no
dependency graph needs to be built for this mode.  The greedy-advance pairing
pass runs directly over the original instruction list.  O(n).

---

### Mode 2 — List scheduling (default reordering)

Maintains a ready queue of instructions whose dependencies are all satisfied.
At each step:

1. If there is a `free` candidate (an instruction already emitted but not yet
   paired), check the ready queue for any instruction that pairs with it.
   - If exactly one candidate pairs with `free`, emit it immediately.
   - If multiple candidates pair with `free`, prefer the one with the fewest
     alternative pairing options elsewhere in the ready queue (i.e. the one that
     would be hardest to pair later).  This is the one-step lookahead that
     catches the common bad-decision case: A pairs with B, but B could have
     paired with C while A pairs with D.
2. If no pairable candidate exists for `free`, emit `free` as solo and pick the
   next instruction from the ready queue by: **primary criterion** — the
   instruction with the most pairing candidates currently in the ready queue
   (i.e. the one most likely to pair soon, chosen greedily); **tie-breaker** —
   longest critical path to the end of the block, measured in dependency-graph
   hops, computed once before scheduling begins.  No instruction latency model
   is used — all hop edges are unit weight.
3. Update the ready queue as new instructions become unblocked.

O(n log n) in queue operations.

---

### Mode 3 — Branch-and-bound (exhaustive)

The BnB finds the topological sort of the block's dependency graph that
maximises pair count under the greedy-advance model.  Used to establish an upper
bound on what any reordering strategy can achieve with the current rule set.

```python
WINDOW_SIZE   = 16      # blocks longer than this are split into independent windows
NODE_BUDGET   = 50_000  # total search nodes before giving up
STAGNATION    = 5_000   # nodes without improvement before giving up
```

**Upper bound:**
```python
def bound(remaining: int, prev_free: bool) -> int:
    if prev_free:
        return 1 + (remaining - 1) // 2
    else:
        return remaining // 2
```

**Candidate ordering heuristic** (when `prev_free` is True):
1. Tier 1 — ready instructions that pair with `free` as B-slot (A=free, B=cand).
2. Tier 2 — ready instructions that pair with `free` as A-slot (A=cand, B=free),
   provided `cand` has no dependency on `free` (i.e. it is safe to emit `cand`
   before `free`).
3. Tier 3 — remaining ready instructions in index order.

**Fallback:** if `NODE_BUDGET` or `STAGNATION` is exceeded, the list-scheduling
result is used instead.

**Stale liveness during BnB search:** The per-instruction `live_in`/`live_out`
fields are computed for the *source* instruction order and become stale as BnB
explores different orderings.  Pairing rules evaluated inside BnB must not
consult `live_in`/`live_out`.  They may only use register-field properties
(`uses_int`, `defs_int`, `rd`, `rs1`, etc.) which are order-independent.
Liveness-dependent rules (e.g. "rd is dead after B, so WAW is safe") are only
applied during the final greedy-advance pass, after liveness has been
recomputed for the chosen ordering.

**Known limitation — PCREL `auipc`/load pairs:** Assembler-generated
`.Lpcrel_hi*` labels associated with `auipc`/`lw` or `auipc`/`addi` PCREL
pairs are non-barrier labels and travel as `prefix_lines` on the `auipc`.  If
the scheduler separates `auipc` from its paired `lw`/`addi` (which is legal
from a dependency-graph perspective if no register edge connects them), the
`.Lpcrel_hi` label will follow the `auipc` while the `lw` remains at its
original offset — breaking the assembler-internal relocation.  To avoid this,
`auipc` instructions are conservatively treated as having an implicit data
dependency on their immediately following instruction when that instruction
references the same destination register (i.e. when `auipc rd, ...` is
immediately followed by an instruction reading `rd`), preventing separation.
This is a best-effort heuristic; a complete fix would require assembler support.

---

## 12. `output/annotator.py`

Each instruction is emitted as its original source line (verbatim `raw` field),
preceded by any `prefix_lines`, followed by a comment.  The comment format is:

```asm
    add  a0, a1, a2     # [C]  {packet 7a}
    slli t0, t0, 3      # [C]  {packet 7b}
    lw   s0, 0(sp)      # [C]  {solo: RAW hazard on t1, not RSD}
    mul  a1, a2, a3     # [~C] {solo}
    foo  a0, a1         # [?]  {solo}
```

Fields in the comment:

- `[C]` / `[~C]` — RVC eligibility.  `[?]` for unknown instructions.
- `{packet Nb}` / `{packet Na}` — paired; `a` is the first instruction of the
  packet, `b` the second.
- `{solo}` — unpaired with no rejection reason recorded.
- `{solo: reason1, reason2}` — unpaired; the reasons are the deduplicated union
  of all `can_pair()` rejection strings collected for this instruction across
  all adjacent candidates examined during the pairing pass.

With `--annotate-liveness`, the live-in set is also appended to the comment.

---

## 13. `__main__.py` — CLI

```
usage: python -m rv_scheduler [options] input.s

Options:
  --output FILE            Output file (default: stdout)
  --fast                   Forward-scan only, no reordering (fastest; good for
                           iterating on rules)
  --thorough               BnB reordering within windows (slowest; establishes
                           upper bound on pairs)
  --same-base-reorder      Relax memory ordering between provably-independent
                           same-base loads/stores (opt-in; no effect with --fast)
  --annotate-liveness      Include live-in register sets in comments
  -v, --verbose            Include rejection reasons for all attempted pairs
```

### Scheduling modes

All three modes use identical `can_pair()` rules.  Only the instruction ordering
fed into the greedy-advance pairing pass differs.

| Mode | Algorithm | Reorders? | Cost | Use for |
|---|---|---|---|---|
| `--fast` | Forward-scan, source order | No | O(n) | Iterating on rules |
| (default) | List scheduling + lookahead | Yes | O(n log n) | Normal use |
| `--thorough` | Branch-and-bound | Yes | Exponential (budgeted) | Measuring rule quality |

`--thorough` pair counts are an upper bound on what any reordering strategy can
achieve with the current rule set, making it useful for evaluating whether list
scheduling is leaving significant pairs on the table.

---

## Implementation Order

1. `isa/registers.py`, `isa/abi.py` — pure data tables, fully unit-testable
   immediately.
2. `isa/instruction.py` — base class and properties; test with hand-constructed
   objects.
3. `isa/decode/rv_i.py` — RV32I/RV64I decoder; test with a corpus of known
   instruction lines including unknown mnemonics to verify best-effort decode.
4. Remaining decoders (`rv_m`, `rv_a`, `rv_f`, `rv_d`, `rv_b`, `rv_zicsr`,
   `rv_v`) — add one extension at a time.
5. `isa/decode/rv_c.py` — RVC eligibility; test against a table of
   known-eligible / known-ineligible examples covering every rule in §5.
6. `analysis/cfg.py` — test with small synthetic assembly snippets covering
   straight-line, branch, loop, and call patterns; verify non-barrier label
   attachment and two-pass label classification.
7. `analysis/liveness.py` — test with known liveness examples including
   cross-call callee-saved survival and conditional-branch join points.
8. `analysis/depgraph.py` — test RAW/WAR/WAW edge construction, barrier edges
   for unknown instructions, and `--same-base-reorder` independence checks.
9. `scheduler/rules.py` + `scheduler/pairing.py` — start with one trivial rule
   to verify the A-slot branch short-circuit and asymmetric prerequisite
   evaluation before authoring real rules.
10. `scheduler/reorder.py` — test list scheduling on small hand-crafted blocks
    with known optimal pair counts; verify BnB agrees; verify liveness
    recomputation after reordering.
11. `output/annotator.py` + `__main__.py` — integration tests with complete
    assembly files; verify output line count equals input line count; verify
    `[?]` annotation appears for unknown mnemonics.
